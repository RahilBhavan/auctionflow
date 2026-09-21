from __future__ import annotations

import csv
import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from .bars import OHLCVBar
from .historical import HistoricalDataError

NY = ZoneInfo("America/New_York")
HEADER = ("ts_event", "rtype", "publisher_id", "instrument_id", "open", "high", "low", "close", "volume", "symbol")
OUTRIGHT = re.compile(r"MES[HMUZ][0-9]")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z")


@dataclass(frozen=True)
class InspectionInfo(Mapping[str, Any]):
    totals: Mapping[tuple[date, str], int]
    dates: tuple[date, ...]
    counts: Mapping[str, int]
    size_bytes: int
    sha256: str
    path: str

    def __getitem__(self, key: str) -> Any:
        return {"totals": self.totals, "dates": list(self.dates), "counts": self.counts,
                "size_bytes": self.size_bytes, "sha256": self.sha256, "path": self.path}[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("totals", "dates", "counts", "size_bytes", "sha256", "path"))

    def __len__(self) -> int:
        return 6


@dataclass(frozen=True)
class SessionReferences:
    prior_rth_high_ticks: int | None
    prior_rth_low_ticks: int | None
    overnight_high_ticks: int | None
    overnight_low_ticks: int | None


@dataclass(frozen=True)
class DatabentoMESLoad:
    bars: tuple[OHLCVBar, ...]
    inspection: InspectionInfo
    session_references: Mapping[date, SessionReferences]


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _price(value: str, row: int, name: str) -> int:
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise HistoricalDataError(f"row {row}: invalid {name}") from exc
    q = d / Decimal("0.25")
    if not d.is_finite() or q != q.to_integral_value():
        raise HistoricalDataError(f"row {row}: {name} is off MES tick increment")
    return int(q)


def _symbol_kind(symbol: str) -> str:
    if OUTRIGHT.fullmatch(symbol):
        return "outright"
    legs = symbol.split("-")
    if len(legs) == 2 and all(OUTRIGHT.fullmatch(leg) for leg in legs):
        return "spread"
    raise HistoricalDataError(f"unknown or malformed MES symbol: {symbol}")


def _row(row: dict[str, str], n: int) -> tuple[datetime, int, str, int, int, int, int, int]:
    try:
        raw_ts = row["ts_event"]
        if not TIMESTAMP.fullmatch(raw_ts):
            raise ValueError
        ts = datetime.fromisoformat(raw_ts[:-1][:26]).replace(tzinfo=UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalDataError(f"row {n}: invalid UTC timestamp") from exc
    if row.get("rtype") != "33" or row.get("publisher_id") != "1":
        raise HistoricalDataError(f"row {n}: rtype/publisher_id must be 33/1")
    try:
        instrument = int(row["instrument_id"])
    except (KeyError, ValueError) as exc:
        raise HistoricalDataError(f"row {n}: invalid instrument_id") from exc
    symbol = row.get("symbol", "")
    kind = _symbol_kind(symbol)
    if instrument < 0:
        raise HistoricalDataError(f"row {n}: invalid identity")
    prices = [_price(row[c], n, c) for c in ("open", "high", "low", "close")] if kind == "outright" else [0] * 4
    try:
        volume = int(row["volume"])
    except (KeyError, ValueError) as exc:
        raise HistoricalDataError(f"row {n}: volume must be integral") from exc
    if volume < 0 or str(volume) != row["volume"].strip():
        raise HistoricalDataError(f"row {n}: volume must be nonnegative integral")
    if kind == "outright" and not (prices[2] <= prices[0] <= prices[1] and prices[2] <= prices[3] <= prices[1]):
        raise HistoricalDataError(f"row {n}: invalid OHLC geometry")
    return ts, instrument, symbol, *prices, volume


def _reader(path: Path) -> csv.DictReader:
    handle = path.open(newline="", encoding="utf-8")
    reader = csv.DictReader(handle)
    if tuple(reader.fieldnames or ()) != HEADER:
        handle.close()
        raise HistoricalDataError("schema must be exactly: " + ",".join(HEADER))
    return reader


def inspect_databento(path: str | Path) -> InspectionInfo:
    path = Path(path); totals: dict[date, dict[str, int]] = {}; dates: set[date] = set(); previous = None; counts = {"outright": 0, "spread": 0}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != HEADER: raise HistoricalDataError("schema must be exactly: " + ",".join(HEADER))
        for n, row in enumerate(reader, 2):
            if None in row: raise HistoricalDataError(f"row {n}: extra fields")
            ts, _instrument, symbol, *values = _row(row, n)
            if previous is not None and ts < previous: raise HistoricalDataError(f"row {n}: timestamps must not regress")
            previous = ts; kind = _symbol_kind(symbol); counts[kind] += 1
            if kind == "outright":
                d = ts.astimezone(NY).date(); dates.add(d); totals.setdefault(d, {})[symbol] = totals.setdefault(d, {}).get(symbol, 0) + values[-1]
    return InspectionInfo(MappingProxyType({d: MappingProxyType(v) for d, v in totals.items()}), tuple(sorted(dates)), MappingProxyType(counts), path.stat().st_size, file_sha256(path), str(path.resolve()))


def _read_databento_mes_bars(path: Path, info: InspectionInfo) -> tuple[list[OHLCVBar], Mapping[date, SessionReferences]]:
    dates = info.dates; chosen: dict[date, str] = {}
    for prior, current in pairwise(dates):
        candidates = info.totals.get(prior, {})
        if candidates: chosen[current] = max(candidates.items(), key=lambda x: (x[1], x[0]))[0]
    bars: list[OHLCVBar] = []; previous = None
    rth: dict[tuple[date, str], list[int]] = {}; overnight: dict[tuple[date, str], list[int]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != HEADER: raise HistoricalDataError("schema must be exactly: " + ",".join(HEADER))
        for n, row in enumerate(reader, 2):
            if None in row: raise HistoricalDataError(f"row {n}: extra fields")
            ts, instrument, symbol, op, hi, lo, cl, volume = _row(row, n)
            local_dt = ts.astimezone(NY); d = local_dt.date(); local = local_dt.time()
            if symbol in chosen.values():
                if time(9, 30) <= local < time(16):
                    bounds = rth.setdefault((d, symbol), [hi, lo]); bounds[0] = max(bounds[0], hi); bounds[1] = min(bounds[1], lo)
                elif local >= time(16):
                    bounds = overnight.setdefault((d + timedelta(days=1), symbol), [hi, lo]); bounds[0] = max(bounds[0], hi); bounds[1] = min(bounds[1], lo)
                elif local < time(9, 30):
                    bounds = overnight.setdefault((d, symbol), [hi, lo]); bounds[0] = max(bounds[0], hi); bounds[1] = min(bounds[1], lo)
            if chosen.get(d) != symbol or not (time(9, 30) <= local < time(11, 30)): continue
            if volume == 0: raise HistoricalDataError(f"row {n}: selected bar volume must be positive")
            if previous is not None and ts <= previous: raise HistoricalDataError(f"row {n}: selected timestamps must increase")
            previous = ts; bars.append(OHLCVBar(event_id=f"MES-{ts:%Y%m%dT%H%M%SZ}-{len(bars):012d}", source="databento-glbx-mes-ohlcv-1m", contract_id=str(instrument), symbol="MES", timestamp=ts, sequence=len(bars), open_ticks=op, high_ticks=hi, low_ticks=lo, close_ticks=cl, volume=volume))
    references = {}
    for d, symbol in chosen.items():
        prior = dates[dates.index(d) - 1] if d in dates and dates.index(d) else None
        rh = rth.get((prior, symbol)) if prior else None
        oh = overnight.get((d, symbol))
        references[d] = SessionReferences(rh[0] if rh else None, rh[1] if rh else None, oh[0] if oh else None, oh[1] if oh else None)
    return bars, MappingProxyType(references)


def load_databento_mes(path: str | Path) -> DatabentoMESLoad:
    path = Path(path)
    inspection = inspect_databento(path)
    bars, references = _read_databento_mes_bars(path, inspection)
    return DatabentoMESLoad(tuple(bars), inspection, references)


def read_databento_mes_bars(path: str | Path) -> list[OHLCVBar]:
    return list(load_databento_mes(path).bars)

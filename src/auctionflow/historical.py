from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from .bars import OHLCVBar
from .domain import TradeEvent

NY = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
PORTARA_COLUMNS = ("Date", "Time", "Open", "High", "Low", "Close", "Volume")
PORTARA_OFFSET = timezone(timedelta(hours=-6))

class HistoricalDataError(ValueError):
    pass

def _local_to_utc(value: str) -> datetime:
    try: local = datetime.fromisoformat(value)
    except ValueError as exc: raise HistoricalDataError(f"invalid timestamp: {value}") from exc
    if local.tzinfo is not None: raise HistoricalDataError("timestamp must be a naive America/New_York local time")
    candidates = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=NY, fold=fold)
        if aware.astimezone(UTC).astimezone(NY).replace(tzinfo=None) == local: candidates.append(aware)
    if not candidates or (len(candidates) == 2 and candidates[0].utcoffset() != candidates[1].utcoffset()):
        raise HistoricalDataError(f"ambiguous or nonexistent local timestamp: {value}")
    if local.second or local.microsecond: raise HistoricalDataError(f"timestamp must be minute start: {value}")
    return candidates[0].astimezone(UTC)

def _ticks(value: str, row: int, column: str) -> int:
    try: ticks = Decimal(value) / Decimal("0.25")
    except (InvalidOperation, ValueError) as exc: raise HistoricalDataError(f"row {row}: invalid {column}") from exc
    if not ticks.is_finite() or ticks != ticks.to_integral_value(): raise HistoricalDataError(f"row {row}: {column} is off MES tick increment")
    return int(ticks)

def _volume(value: str, row: int) -> int:
    try: quantity = int(value)
    except (TypeError, ValueError) as exc: raise HistoricalDataError(f"row {row}: volume must be a positive integer") from exc
    if quantity <= 0 or str(quantity) != value.strip(): raise HistoricalDataError(f"row {row}: volume must be a positive integer")
    return quantity

def _bar(
    row: Mapping[str, str | None],
    n: int,
    source: str = "firstrate",
    contract_id: str | None = None,
) -> OHLCVBar:
    columns = REQUIRED_COLUMNS
    for column in columns:
        value = row.get(column)
        if value is None or (isinstance(value, str) and not value.strip()): raise HistoricalDataError(f"row {n}: missing {column}")
    ts = _local_to_utc(row["timestamp"])
    prices = {c: _ticks(row[c], n, c) for c in ("open", "high", "low", "close")}
    try:
        return OHLCVBar(event_id=f"MES-{ts:%Y%m%dT%H%M%SZ}-{n:012d}", source=source, contract_id=contract_id, timestamp=ts, sequence=n - 2, **{f"{c}_ticks": prices[c] for c in prices}, volume=_volume(row["volume"], n))
    except ValueError as exc: raise HistoricalDataError(f"row {n}: invalid bar: {exc}") from exc

def _reader(handle: TextIO) -> Iterator[Mapping[str, str | None]]:
    reader = csv.DictReader(handle)
    if reader.fieldnames is None or tuple(reader.fieldnames) != REQUIRED_COLUMNS: raise HistoricalDataError(f"schema must be exactly: {','.join(REQUIRED_COLUMNS)}")
    return reader

def read_firstrate_bars(input_path: str | Path) -> list[OHLCVBar]:
    with Path(input_path).open(newline="", encoding="utf-8") as handle: return [_bar(row, n) for n, row in enumerate(_reader(handle), 2)]

def _portara_contract(path: str | Path, contract_id: str | None = None) -> str:
    if contract_id: return contract_id
    stem = Path(path).stem
    if len(stem) == 8 and stem.startswith("MES") and stem[3:].isalnum(): return stem
    raise HistoricalDataError("Portara filename must infer an exact MES contract ID; pass contract_id explicitly")

def read_portara_bars(input_path: str | Path, contract_id: str | None = None) -> list[OHLCVBar]:
    cid = _portara_contract(input_path, contract_id)
    with Path(input_path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != PORTARA_COLUMNS: raise HistoricalDataError(f"schema must be exactly: {','.join(PORTARA_COLUMNS)}")
        bars = []
        for n, row in enumerate(reader, 2):
            try: ts = datetime.strptime(f'{row.get("Date", "")} {row.get("Time", "")}', "%Y%m%d %H%M").replace(tzinfo=PORTARA_OFFSET).astimezone(UTC)
            except (ValueError, TypeError) as exc: raise HistoricalDataError(f"row {n}: invalid date/time") from exc
            normalized = {"timestamp": ts.replace(tzinfo=None).isoformat(sep=" "), **{k.lower(): row.get(k) for k in ("Open","High","Low","Close","Volume")}}
            try: bar = _bar_portara(normalized, n, cid, ts)
            except ValueError as exc: raise HistoricalDataError(f"row {n}: invalid bar: {exc}") from exc
            if bars and bar.timestamp <= bars[-1].timestamp: raise HistoricalDataError(f"row {n}: timestamps must be strictly increasing")
            bars.append(bar)
    return bars

def _bar_portara(row: Mapping[str, str | None], n: int, cid: str, ts: datetime) -> OHLCVBar:
    prices = {c: _ticks(row[c], n, c) for c in ("open", "high", "low", "close")}
    return OHLCVBar(event_id=f"MES-{ts:%Y%m%dT%H%MZ}-{n:012d}", source="portara", contract_id=cid, timestamp=ts, sequence=n-2, **{f"{c}_ticks": prices[c] for c in prices}, volume=_volume(row["volume"], n))

def read_portara_trade_ticks_as_minute_bars(
    input_path: str | Path, contract_id: str | None = None
) -> list[OHLCVBar]:
    path = Path(input_path)
    cid = contract_id
    if cid is None:
        name = path.name
        suffix = ".trades.txt"
        if not name.endswith(suffix):
            raise HistoricalDataError("Portara filename must infer an exact MES contract ID; pass contract_id explicitly")
        cid = _portara_contract(name[:-len(suffix)])
    bars = []
    current = None
    previous = None
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != ("Timestamp", "Price", "Volume"):
            raise HistoricalDataError("schema must be exactly: Timestamp,Price,Volume")
        for n, row in enumerate(reader, 2):
            timestamp_text = row.get("Timestamp")
            if timestamp_text is None or not timestamp_text.strip():
                raise HistoricalDataError(f"row {n}: missing timestamp")
            try:
                trade_ts = datetime.strptime(timestamp_text.strip(), "%Y%m%d %H:%M:%S.%f").replace(tzinfo=PORTARA_OFFSET).astimezone(UTC)
            except (TypeError, ValueError) as exc:
                raise HistoricalDataError(f"row {n}: invalid timestamp") from exc
            # Equal vendor timestamps retain source file order as the deterministic secondary ordering.
            if previous is not None and trade_ts < previous:
                raise HistoricalDataError(f"row {n}: timestamps must not regress")
            previous = trade_ts
            price = _ticks(row.get("Price", ""), n, "price")
            volume = _volume(row.get("Volume", ""), n)
            minute = trade_ts.replace(second=0, microsecond=0)
            if current is None or minute != current["timestamp"]:
                if current is not None:
                    bars.append(OHLCVBar(**current))
                current = {
                    "event_id": f"MES-{minute:%Y%m%dT%H%MZ}-{len(bars):012d}",
                    "source": "portara",
                    "contract_id": cid,
                    "timestamp": minute,
                    "sequence": len(bars),
                    "open_ticks": price,
                    "high_ticks": price,
                    "low_ticks": price,
                    "close_ticks": price,
                    "volume": volume,
                }
            else:
                current["high_ticks"] = max(current["high_ticks"], price)
                current["low_ticks"] = min(current["low_ticks"], price)
                current["close_ticks"] = price
                current["volume"] += volume
    if current is not None:
        try:
            bars.append(OHLCVBar(**current))
        except ValueError as exc:
            raise HistoricalDataError(f"invalid aggregated bar: {exc}") from exc
    if not bars:
        raise HistoricalDataError("Portara trade input is empty")
    return bars


def group_bars_by_et_date(bars: Sequence[OHLCVBar]) -> dict[date, list[OHLCVBar]]:
    groups = {}
    for bar in bars: groups.setdefault(bar.et_date, []).append(bar)
    return {day: sorted(items, key=lambda b: b.timestamp) for day, items in groups.items()}

def _atomic_events(events: Iterable[str], destination: str | Path) -> None:
    destination = Path(destination)
    if destination.exists(): raise HistoricalDataError("output already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h: h.write("\n".join(events) + ("\n" if events else "")); h.flush(); os.fsync(h.fileno())
        os.link(tmp, destination); os.unlink(tmp)
    except Exception: Path(tmp).unlink(missing_ok=True); raise

def normalize_bars(
    input_path: str | Path,
    output_path: str | Path,
    reader: Callable[[str | Path], list[OHLCVBar]],
    strategy_window_only: bool = False,
) -> None:
    events, previous = [], None
    for bar in reader(input_path):
        if previous is not None and bar.timestamp <= previous: raise HistoricalDataError("timestamps must be strictly increasing")
        previous = bar.timestamp; local_time = bar.timestamp.astimezone(NY).time()
        if strategy_window_only and not (time(9, 35) <= local_time < time(11, 30)): continue
        events.append(TradeEvent(event_id=f"MES-{bar.timestamp:%Y%m%dT%H%M%SZ}-{len(events):012d}", timestamp=bar.timestamp, symbol="MES", sequence=len(events), price_ticks=bar.close_ticks, quantity=bar.volume).model_dump_json())
    _atomic_events(events, output_path)

def normalize_firstrate_mes(
    input_path: str | Path, output_path: str | Path, strategy_window_only: bool = False
) -> None: normalize_bars(input_path, output_path, read_firstrate_bars, strategy_window_only)
def normalize_portara_mes(
    input_path: str | Path, output_path: str | Path, strategy_window_only: bool = False
) -> None: normalize_bars(input_path, output_path, read_portara_bars, strategy_window_only)

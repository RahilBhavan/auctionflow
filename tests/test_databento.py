from __future__ import annotations

import csv
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from auctionflow.databento import HEADER, inspect_databento, read_databento_mes_bars
from auctionflow.historical import HistoricalDataError


def write_rows(path, rows, header=HEADER):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def row(ts="2024-01-02T14:30:00.123456789Z", symbol="MESH4", **changes):
    value = {"ts_event": ts, "rtype": "33", "publisher_id": "1", "instrument_id": "42",
             "open": "5000.00", "high": "5000.25", "low": "4999.75", "close": "5000.00",
             "volume": "3", "symbol": symbol}
    value.update(changes)
    return value


def test_header_must_be_exact_and_rejects_extra_fields(tmp_path):
    path = tmp_path / "bad.csv"
    write_rows(path, [row()], HEADER[:-1] + ("extra", "symbol"))
    with pytest.raises(HistoricalDataError, match="schema must be exactly"):
        inspect_databento(path)
    write_rows(path, [row()], HEADER + ("extra",))
    with pytest.raises(HistoricalDataError, match="schema must be exactly"):
        inspect_databento(path)


def test_row_requires_nanosecond_utc_and_expected_databento_identity(tmp_path):
    path = tmp_path / "bad.csv"
    for changes, message in [
        ({"ts_event": "2024-01-02T14:30:00Z"}, "invalid UTC timestamp"),
        ({"ts_event": "2024-01-02T14:30:00.12345678Z"}, "invalid UTC timestamp"),
        ({"rtype": "1"}, "rtype/publisher_id"),
        ({"publisher_id": "2"}, "rtype/publisher_id"),
    ]:
        write_rows(path, [row(**changes)])
        with pytest.raises(HistoricalDataError, match=message):
            inspect_databento(path)


def test_inspection_streams_source_hash_without_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / "bars.csv"
    write_rows(path, [row()])
    expected = hashlib.sha256(path.open("rb").read()).hexdigest()

    def fail_read_bytes(self):
        raise AssertionError("source must not be read_bytes")

    monkeypatch.setattr(__import__("pathlib").Path, "read_bytes", fail_read_bytes)
    assert inspect_databento(path)["sha256"] == expected


def test_nanosecond_timestamp_is_parsed_as_utc(tmp_path):
    path = tmp_path / "bars.csv"
    write_rows(path, [row()])
    info = inspect_databento(path)
    assert info["dates"] == [datetime(2024, 1, 2, tzinfo=UTC).date()]


@pytest.mark.parametrize("changes", [
    {"open": "NaN"}, {"high": "Infinity"}, {"low": "1.13"}, {"close": "1.12"},
    {"volume": "-1"}, {"volume": "1.0"},
])
def test_finite_quarter_tick_geometry_and_integral_volume(changes, tmp_path):
    path = tmp_path / "bad.csv"
    write_rows(path, [row(**changes)])
    with pytest.raises(HistoricalDataError):
        inspect_databento(path)


def test_spreads_are_excluded_before_price_validation(tmp_path):
    path = tmp_path / "mixed.csv"
    write_rows(path, [row(symbol="MESH4-MESM4", open="not-a-price", high="NaN", low="x", close="y")])
    info = inspect_databento(path)
    assert info["counts"] == {"outright": 0, "spread": 1}


@pytest.mark.parametrize("symbol", ["MESZ4", "MESU4", "MESH4", "MESM4"])
def test_outright_contract_regex(symbol, tmp_path):
    path = tmp_path / "bars.csv"
    write_rows(path, [row(symbol=symbol)])
    assert inspect_databento(path)["counts"]["outright"] == 1


def test_equal_timestamp_interleaving_is_allowed_but_regression_is_rejected(tmp_path):
    path = tmp_path / "bars.csv"
    write_rows(path, [row(symbol="MESH4"), row(symbol="MESM4"), row(ts="2024-01-02T14:29:59.999999999Z")])
    with pytest.raises(HistoricalDataError, match="timestamps must not regress"):
        inspect_databento(path)
    write_rows(path, [row(symbol="MESH4"), row(symbol="MESM4")])
    assert inspect_databento(path)["counts"]["outright"] == 2


def test_nonnegative_volume_and_geometry_are_preserved_in_bars(tmp_path):
    path = tmp_path / "bars.csv"
    write_rows(path, [row()])
    # A single represented date is deliberately excluded by the selection policy.
    assert read_databento_mes_bars(path) == []


def test_missing_required_field_is_rejected(tmp_path):
    path = tmp_path / "missing.csv"
    write_rows(path, [{k: v for k, v in row().items() if k != "volume"}])
    with pytest.raises(HistoricalDataError):
        inspect_databento(path)


def test_forged_inspection_cannot_influence_public_loader(tmp_path):
    path = tmp_path / "bars.csv"
    write_rows(
        path,
        [
            row(symbol="MESH4", volume="10"),
            row(symbol="MESM4", volume="1"),
            row(ts="2024-01-03T14:30:00.000000000Z", symbol="MESH4", instrument_id="42"),
            row(ts="2024-01-03T14:30:00.000000000Z", symbol="MESM4", instrument_id="99"),
        ],
    )
    info = inspect_databento(path)
    forged = replace(
        info,
        totals=MappingProxyType(
            {day: MappingProxyType({"MESM4": 1_000_000}) for day in info.dates}
        ),
    )

    with pytest.raises(TypeError):
        read_databento_mes_bars(path, forged)  # type: ignore[call-arg]
    assert [bar.contract_id for bar in read_databento_mes_bars(path)] == ["42"]

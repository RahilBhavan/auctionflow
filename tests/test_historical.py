from __future__ import annotations

import csv
from pathlib import Path

import pytest

from auctionflow.historical import (
    HistoricalDataError,
    normalize_firstrate_mes,
    read_firstrate_bars,
    read_portara_trade_ticks_as_minute_bars,
)
from auctionflow.replay import replay

FIXTURE = Path(__file__).parent / "fixtures/historical/synthetic_firstrate.csv"


def write_csv(path: Path, rows: list[dict[str, str]], fields=None) -> None:
    fields = fields or ["timestamp", "open", "high", "low", "close", "volume"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def rows() -> list[dict[str, str]]:
    return list(csv.DictReader(FIXTURE.open()))


def test_happy_path_ticks_utc_dst_and_sequence(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    normalize_firstrate_mes(FIXTURE, output)
    lines = output.read_text().splitlines()
    assert len(lines) == 5
    assert '"timestamp":"2024-03-08T14:34:00Z"' in lines[0]
    assert '"timestamp":"2024-03-11T13:35:00Z"' in lines[-1]
    assert '"price_ticks":20001' in lines[1]
    assert '"sequence":0' in lines[0] and '"sequence":4' in lines[-1]
    assert '"event_id":"MES-20240308T143400Z-000000000000"' in lines[0]


@pytest.mark.parametrize("column", ["open", "high", "low", "close"])
def test_every_ohlc_price_must_be_on_tick(tmp_path: Path, column: str) -> None:
    source = tmp_path / "bars.csv"
    data = rows()
    data[0][column] = "5000.10"
    write_csv(source, data)
    with pytest.raises(HistoricalDataError, match=column):
        normalize_firstrate_mes(source, tmp_path / "out.jsonl")


@pytest.mark.parametrize("column", ["timestamp", "open", "high", "low", "close", "volume"])
def test_missing_or_blank_required_values_are_data_errors(tmp_path: Path, column: str) -> None:
    source = tmp_path / "missing.csv"; data = rows(); data[0][column] = ""; write_csv(source, data)
    with pytest.raises(HistoricalDataError, match=rf"row 2: missing {column}"):
        normalize_firstrate_mes(source, tmp_path / "out")


def test_missing_mapping_value_is_data_error() -> None:
    from auctionflow.historical import _bar
    data = rows()[0]; del data["close"]
    with pytest.raises(HistoricalDataError, match="row 2: missing close"):
        _bar(data, 2)


def test_bad_volume_and_schema(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    data = rows(); data[0]["volume"] = "1.5"
    write_csv(source, data)
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(source, tmp_path / "out")
    write_csv(source, [{"timestamp": row["timestamp"], "close": row["close"]} for row in rows()], ["timestamp", "close"])
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(source, tmp_path / "out2")


@pytest.mark.parametrize("timestamp", ["2024-03-10 02:30:00", "2024-11-03 01:30:00"])
def test_rejects_nonexistent_and_ambiguous_local_time(tmp_path: Path, timestamp: str) -> None:
    source = tmp_path / "bars.csv"; data = rows(); data[0]["timestamp"] = timestamp
    write_csv(source, data); output = tmp_path / "out"
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(source, output)
    assert not output.exists()


def test_strategy_window_boundaries(tmp_path: Path) -> None:
    output = tmp_path / "window.jsonl"
    normalize_firstrate_mes(FIXTURE, output, strategy_window_only=True)
    lines = output.read_text().splitlines()
    assert len(lines) == 3
    assert '"sequence":0' in lines[0] and '"sequence":2' in lines[-1]


def test_determinism_exclusive_output_and_failure_cleanup(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    normalize_firstrate_mes(FIXTURE, a); normalize_firstrate_mes(FIXTURE, b)
    assert a.read_bytes() == b.read_bytes()
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(FIXTURE, a)
    bad = tmp_path / "bad.csv"; data = rows(); data[2]["close"] = "1.1"; write_csv(bad, data)
    failed = tmp_path / "failed.jsonl"
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(bad, failed)
    assert not failed.exists()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_rejects_nonfinite_prices(tmp_path: Path, value: str) -> None:
    source = tmp_path / "nonfinite.csv"; data = rows(); data[0]["open"] = value; write_csv(source, data)
    with pytest.raises(HistoricalDataError, match="open"):
        normalize_firstrate_mes(source, tmp_path / "out")


def test_reader_rejects_open_below_low(tmp_path: Path) -> None:
    source = tmp_path / "bad.csv"; data = rows(); data[0]["open"] = "4999.00"; data[0]["low"] = "4999.25"; write_csv(source, data)
    with pytest.raises(HistoricalDataError, match="invalid bar"):
        read_firstrate_bars(source)


def test_reader_rejects_non_minute_and_bad_geometry(tmp_path: Path) -> None:
    source = tmp_path / "bad.csv"; data = rows(); data[0]["timestamp"] = "2024-03-08 09:34:59"; write_csv(source, data)
    with pytest.raises(HistoricalDataError): read_firstrate_bars(source)
    data = rows(); data[0]["high"] = "4999.75"; write_csv(source, data)
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(source, tmp_path / "geometry")


def test_normalization_rejects_duplicate_atomically(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.csv"; data = rows(); data[1]["timestamp"] = data[0]["timestamp"]; write_csv(source, data)
    output = tmp_path / "out"
    with pytest.raises(HistoricalDataError): normalize_firstrate_mes(source, output)
    assert not output.exists()


def test_portara_equal_timestamps_preserve_file_order_and_determinism(tmp_path: Path) -> None:
    source = tmp_path / "MESM4ABC.trades.txt"
    source.write_text(
        "Timestamp,Price,Volume\n"
        "20240308 08:34:00.000,5000.00,2\n"
        "20240308 08:34:00.000,5001.00,3\n"
    )
    bars = read_portara_trade_ticks_as_minute_bars(source)
    assert (bars[0].open_ticks, bars[0].close_ticks, bars[0].volume) == (20000, 20004, 5)
    first = b"\n".join(bar.model_dump_json().encode() for bar in bars)
    second = b"\n".join(
        bar.model_dump_json().encode()
        for bar in read_portara_trade_ticks_as_minute_bars(source)
    )
    assert first == second


def test_cli_and_replay_acceptance(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    from auctionflow.cli import main
    assert main(["normalize-bars", str(FIXTURE), "--output", str(output), "--format", "firstrate-mes-1m"]) == 0
    decisions = tmp_path / "decisions.jsonl"
    replay(output, decisions)
    assert decisions.exists()

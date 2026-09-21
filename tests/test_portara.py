import csv

import pytest

from auctionflow.cli import main
from auctionflow.historical import HistoricalDataError, read_portara_bars

FIELDS = ["Date", "Time", "Open", "High", "Low", "Close", "Volume"]

def write(path, rows, fields=FIELDS):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def row(**changes):
    value = {"Date": "20240603", "Time": "0930", "Open": "5000.00", "High": "5000.25", "Low": "4999.75", "Close": "5000.25", "Volume": "10"}
    value.update(changes); return value

def test_fixed_offset_contract_and_sequence(tmp_path):
    p = tmp_path / "MES2026M.txt"; write(p, [row(), row(Time="0931")])
    bars = read_portara_bars(p)
    assert bars[0].timestamp.isoformat() == "2024-06-03T15:30:00+00:00"
    assert bars[0].et_date.isoformat() == "2024-06-03" and bars[0].contract_id == "MES2026M"
    assert bars[1].sequence == 1

@pytest.mark.parametrize("changes", [{"Date": "2024-06-03"}, {"Time": "9:30"}, {"Open": "5000.10"}, {"Volume": "0"}, {"High": "4999.50"}, {"Open": "NaN"}])
def test_portara_rejects_bad_data(tmp_path, changes):
    p = tmp_path / "MES2026M.txt"; write(p, [row(**changes)])
    with pytest.raises(HistoricalDataError): read_portara_bars(p)

def test_schema_order_and_timestamp_order_contract_name(tmp_path):
    p = tmp_path / "MES2026M.txt"; write(p, [row(), row(Time="0929")])
    with pytest.raises(HistoricalDataError): read_portara_bars(p)
    write(p, [row()], fields=FIELDS[:-1])
    with pytest.raises(HistoricalDataError): read_portara_bars(p)
    p = tmp_path / "ambiguous.txt"; write(p, [row()])
    with pytest.raises(HistoricalDataError): read_portara_bars(p)

def test_cli_normalize_and_backtest_format_and_existing_output(tmp_path):
    p = tmp_path / "MES2026M.txt"; write(p, [row()])
    out = tmp_path / "events.jsonl"
    assert main(["normalize-bars", str(p), "--output", str(out), "--format", "portara-mes-1m"]) == 0
    with pytest.raises(SystemExit): main(["normalize-bars", str(p), "--output", str(out), "--format", "portara-mes-1m"])
    report, sessions = tmp_path / "report.json", tmp_path / "sessions.jsonl"
    assert main(["backtest-bars", str(p), "--format", "portara-mes-1m", "--report", str(report), "--sessions-jsonl", str(sessions), "--experiment-id", "x", "--created-at", "2026-08-04T00:00:00Z"]) == 0
    assert '"format":"portara-mes-1m"' in report.read_text()

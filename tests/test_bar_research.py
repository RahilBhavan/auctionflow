from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from auctionflow.bar_research import _partition, run_bar_backtest
from auctionflow.cli import main
from auctionflow.ib_breakout import IBSessionResult


def fixture(path: Path, days=3):
    with path.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        for day in range(days):
            for minute in range(120):
                h, m = divmod(9 * 60 + 30 + minute, 60)
                value = 100 + day
                w.writerow((f"2024-01-{day + 2:02d}T{h:02d}:{m:02d}", value, value + 1, value - 1, value, 1))


def test_chronological_split_and_invalid_exclusion():
    assert [_partition(i, 5) for i in range(5)] == ["TRAIN", "TRAIN", "TRAIN", "VALIDATION", "TEST"]


def test_deterministic_bytes_and_costs(tmp_path):
    source = tmp_path / "bars.csv"; fixture(source)
    r1, s1 = tmp_path / "r1.json", tmp_path / "s1.jsonl"
    r2, s2 = tmp_path / "r2.json", tmp_path / "s2.jsonl"
    run_bar_backtest(source, r1, s1, "x", "2024-01-01T12:00:00-05:00", 3, 4)
    run_bar_backtest(source, r2, s2, "x", "2024-01-01T12:00:00-05:00", 3, 4)
    assert r1.read_bytes() == r2.read_bytes() and s1.read_bytes() == s2.read_bytes()
    assert '"costs_cents":0' in s1.read_text()


def test_rejects_malformed_metadata_and_existing_paths(tmp_path):
    source = tmp_path / "bars.csv"; fixture(source)
    with pytest.raises(ValueError): run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01")
    with pytest.raises(ValueError): run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01T00:00:00Z", -1)
    (tmp_path / "r").write_text("keep")
    with pytest.raises(ValueError): run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01T00:00:00Z")
    assert (tmp_path / "r").read_text() == "keep"


def test_report_aggregates_and_excludes_interleaved_invalid(tmp_path, monkeypatch):
    source = tmp_path / "bars.csv"; fixture(source, 4)
    import auctionflow.bar_research as module
    def evaluate(group, tc, sc):
        day = group[0].et_date
        invalid = day.day == 3
        return IBSessionResult(session=day, state="DATA_INVALID" if invalid else "ACCEPTED",
            status="DATA_INVALID" if invalid else "ACCEPTED", reason="BAD" if invalid else "SIGNAL",
            direction=None if invalid else "long", quantity=0 if invalid else 2,
            label="UNKNOWN" if invalid else "TARGET_FIRST", gross_cents=0 if invalid else 100,
            costs_cents=0 if invalid else (tc.commission_cents_per_contract + tc.slippage_cents_per_contract) * 2,
            net_cents=0 if invalid else 100 - (tc.commission_cents_per_contract + tc.slippage_cents_per_contract) * 2,
            trading_config_revision=tc.revision, strategy_config_revision=sc.revision)
    monkeypatch.setattr(module, "evaluate_ib_session", evaluate)
    report_path, sessions_path = tmp_path / "report", tmp_path / "sessions"
    report = run_bar_backtest(source, report_path, sessions_path, "x", "2024-01-01T00:00:00Z", 3, 4)
    rows = [__import__("json").loads(x) for x in sessions_path.read_text().splitlines()]
    assert [r["partition"] for r in rows] == ["TRAIN", "DATA_INVALID", "VALIDATION", "TEST"]
    assert report["counts"]["status"] == {"ACCEPTED": 3, "DATA_INVALID": 1}
    assert report["counts"]["reason"] == {"BAD": 1, "SIGNAL": 3}
    assert report["counts"]["label"] == {"TARGET_FIRST": 3, "UNKNOWN": 1}
    assert report["counts"]["direction"] == {"None": 1, "long": 3}
    assert report["counts"]["quantity"] == {"0": 1, "2": 3}
    assert report["totals"]["gross_cents"] == sum(r["gross_cents"] for r in rows)
    assert report["totals"]["costs_cents"] == sum(r["costs_cents"] for r in rows)
    assert report["totals"]["net_cents"] == sum(r["net_cents"] for r in rows)
    valid_rows = [r for r in rows if r["status"] != "DATA_INVALID"]
    assert report["negative_controls"]["NO_TRADE"]["net_cents"] == 0
    assert report["strategy"]["net_delta_cents"] == sum(r["net_cents"] for r in valid_rows)
    assert report["sensitivity"] == {"transaction_cost_multiplier": 2, "description": "Sensitivity analysis, not a negative control.", "gross_cents": sum(r["gross_cents"] for r in valid_rows), "costs_cents": sum(r["costs_cents"] for r in valid_rows) * 2, "net_cents": sum(r["gross_cents"] - 2 * r["costs_cents"] for r in valid_rows)}


def test_strategy_parameters_change_revision_and_results(tmp_path):
    source = tmp_path / "bars.csv"; fixture(source)
    r1, s1 = tmp_path / "r1.json", tmp_path / "s1.jsonl"
    r2, s2 = tmp_path / "r2.json", tmp_path / "s2.jsonl"
    a = run_bar_backtest(source, r1, s1, "x", "2024-01-01T00:00:00Z", strategy="failed_ib", evaluation_ceiling="TRAIN", excursion_ticks=1, target_numerator=3, target_denominator=2)
    b = run_bar_backtest(source, r2, s2, "x", "2024-01-01T00:00:00Z", strategy="failed_ib", evaluation_ceiling="TRAIN", excursion_ticks=2, target_numerator=2, target_denominator=1)
    assert a["strategy_config_revision"] != b["strategy_config_revision"]
    assert s1.read_bytes() != s2.read_bytes()


def test_invalid_strategy_geometry_rejected(tmp_path):
    source = tmp_path / "bars.csv"; fixture(source)
    with pytest.raises(ValueError, match="geometry"):
        run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01T00:00:00Z", strategy="liquidity_sweep", confirmation_bars=3, reclaim_window=3)


def test_evaluation_ceiling_hides_later_partitions(tmp_path):
    source = tmp_path / "bars.csv"; fixture(source, 5)
    report = run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01T00:00:00Z", evaluation_ceiling="TRAIN")
    rows = [__import__("json").loads(x) for x in (tmp_path / "s").read_text().splitlines()]
    assert {row["partition"] for row in rows} <= {"TRAIN"}
    assert "TEST" not in report["partitions"] and "VALIDATION" not in report["partitions"]
    assert report["evaluation_ceiling"] == "TRAIN"


def test_portara_trade_ticks_format_is_deterministic(tmp_path):
    source = tmp_path / "MES2026M.trades.txt"
    source.write_text("Timestamp,Price,Volume\n20240102 09:30:00.100,100.00,1\n20240102 09:30:30.200,100.25,2\n20240102 09:31:00.000,99.75,1\n")
    outputs = [(tmp_path / "r1", tmp_path / "s1"), (tmp_path / "r2", tmp_path / "s2")]
    for report, sessions in outputs:
        run_bar_backtest(source, report, sessions, "ticks", "2024-01-01T00:00:00Z", 3, 4, "portara-mes-trades")
    assert outputs[0][0].read_bytes() == outputs[1][0].read_bytes()
    assert outputs[0][1].read_bytes() == outputs[1][1].read_bytes()
    assert '"source":{"format":"portara-mes-trades"' in outputs[0][0].read_text()


def test_same_paths_and_cli_errors(tmp_path):
    source = tmp_path / "bars.csv"; fixture(source, 3)
    with pytest.raises(ValueError): run_bar_backtest(source, tmp_path / "same", tmp_path / "same", "x", "2024-01-01T00:00:00Z")
    with pytest.raises(SystemExit): main(["backtest-bars", str(source), "--report", str(tmp_path / "r"), "--sessions-jsonl", str(tmp_path / "s"), "--experiment-id", "x", "--created-at", "bad"])
    with pytest.raises(SystemExit): main(["backtest-bars", str(source), "--report", str(tmp_path / "r2"), "--sessions-jsonl", str(tmp_path / "s2"), "--experiment-id", "x", "--created-at", "2024-01-01T00:00:00Z", "--commission-cents-per-contract", "-1"])
    assert main(["backtest-bars", str(source), "--report", str(tmp_path / "r3"), "--sessions-jsonl", str(tmp_path / "s3"), "--experiment-id", "x", "--created-at", "2024-01-01T00:00:00Z"]) == 0


def test_second_publication_failure_rolls_back(tmp_path, monkeypatch):
    source = tmp_path / "bars.csv"; fixture(source)
    import auctionflow.bar_research as module
    real_link = module.os.link
    calls = 0
    def fail_once(src, dst):
        nonlocal calls; calls += 1
        if calls == 2: raise OSError("injected")
        return real_link(src, dst)
    monkeypatch.setattr(module.os, "link", fail_once)
    with pytest.raises(OSError): run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01T00:00:00Z")
    assert not (tmp_path / "r").exists() and not (tmp_path / "s").exists()


def databento_fixture(path):
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from auctionflow.databento import HEADER
    et = ZoneInfo("America/New_York")
    rows = []
    def add(ts, symbol, volume=1, **values):
        rows.append({"ts_event": ts.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "000Z"),
                     "rtype": "33", "publisher_id": "1", "instrument_id": "1", "open": "5000.00",
                     "high": "5000.25", "low": "4999.75", "close": "5000.00", "volume": str(volume),
                     "symbol": symbol, **values})
    for day, symbol, volume in [(2, "MESH4", 10), (2, "MESM4", 20), (3, "MESH4", 10), (3, "MESM4", 20), (4, "MESM4", 30)]:
        add(datetime(2024, 1, day, 9, 0, tzinfo=et), symbol, volume)
    for day, symbol in [(3, "MESM4"), (4, "MESM4")]:
        start = datetime(2024, 1, day, 9, 30, tzinfo=et)
        for minute in range(120):
            add(start + timedelta(minutes=minute), symbol, 1, open=f"{5000 + minute / 4:.2f}", high=f"{5000.25 + minute / 4:.2f}", low=f"{4999.75 + minute / 4:.2f}", close=f"{5000 + minute / 4:.2f}")
        add(start - timedelta(minutes=1), symbol, 1)
        add(start + timedelta(minutes=120), symbol, 1)
    rows.sort(key=lambda item: item["ts_event"])
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER); writer.writeheader(); writer.writerows(rows)


def test_databento_backtest_streams_source_hash_without_read_bytes(tmp_path, monkeypatch):
    source = tmp_path / "data.csv"; databento_fixture(source)
    expected = hashlib.sha256(source.open("rb").read()).hexdigest()

    def fail_read_bytes(self):
        raise AssertionError("source must not be read_bytes")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    report = run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "db", "2024-01-01T00:00:00Z", format="databento-glbx-mes-ohlcv-1m")
    assert report["input_sha256"] == expected


def test_databento_projection_rolls_contract_and_uses_previous_date_volume(tmp_path):
    source = tmp_path / "data.csv"; databento_fixture(source)
    bars = __import__("auctionflow.databento", fromlist=["read_databento_mes_bars"]).read_databento_mes_bars(source)
    assert len(bars) == 240
    assert {bar.contract_id for bar in bars[:120]} == {"1"}
    assert [bar.timestamp.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).time() for bar in bars[:1]] == [__import__("datetime").time(9, 30)]


def test_databento_metadata_report_fields_and_byte_determinism(tmp_path):
    source = tmp_path / "data.csv"; databento_fixture(source)
    (tmp_path / "metadata.json").write_text('{"dataset":"GLBX.MDP3","job_id":"j1"}')
    outputs = [(tmp_path / "r1", tmp_path / "s1"), (tmp_path / "r2", tmp_path / "s2")]
    for report, sessions in outputs:
        result = run_bar_backtest(source, report, sessions, "db", "2024-01-01T00:00:00Z", format="databento-glbx-mes-ohlcv-1m")
        assert result["source"]["metadata"]["job_id"] == "j1"
        assert result["source"]["format"] == "databento-glbx-mes-ohlcv-1m"
        assert result["source"]["selection_policy"]
    assert outputs[0][0].read_bytes() == outputs[1][0].read_bytes()
    assert outputs[0][1].read_bytes() == outputs[1][1].read_bytes()


def test_databento_missing_projection_minutes_remains_existing_invalid_session(tmp_path):
    source = tmp_path / "data.csv"; databento_fixture(source)
    lines = source.read_text().splitlines()
    source.write_text("\n".join(line for line in lines if "15:00:00.000000000Z" not in line) + "\n")
    report = run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "db", "2024-01-01T00:00:00Z", format="databento-glbx-mes-ohlcv-1m")
    assert report["counts"]["status"].get("DATA_INVALID", 0) >= 1


def test_every_valid_session_is_accepted_no_trade_or_rejected(tmp_path, monkeypatch):
    """Issue #2: REJECTED sessions (INSUFFICIENT_RISK, INVALID_GEOMETRY) are valid sessions
    that are neither ACCEPTED nor NO_TRADE; the report must count them so totals reconcile."""
    source = tmp_path / "bars.csv"; fixture(source, 6)
    import auctionflow.bar_research as module
    statuses = {2: "ACCEPTED", 3: "NO_TRADE", 4: "REJECTED", 5: "DATA_INVALID", 6: "REJECTED", 7: "NO_TRADE"}
    def evaluate(group, tc, sc):
        day = group[0].et_date
        status = statuses[day.day]
        return IBSessionResult(session=day, state=status, status=status, reason="X", direction=None,
                               trading_config_revision=tc.revision, strategy_config_revision=sc.revision)
    monkeypatch.setattr(module, "evaluate_ib_session", evaluate)
    report = run_bar_backtest(source, tmp_path / "r", tmp_path / "s", "x", "2024-01-01T00:00:00Z")
    for p in [report["totals"], *report["partitions"].values()]:
        if "accepted_trades" in p and p["status"].get("DATA_INVALID") is None:
            assert p["accepted_trades"] + p["no_trade_sessions"] + p["rejected_sessions"] == p["valid"]
    t = report["totals"]
    assert (t["accepted_trades"], t["no_trade_sessions"], t["rejected_sessions"], t["valid"]) == (1, 2, 2, 5)

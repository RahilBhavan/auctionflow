import json
from datetime import UTC, datetime, timedelta

import pytest

from auctionflow import cli
from auctionflow.domain import TradeCandidate, TradeEvent
from auctionflow.research import OutcomeLabel, canonical_json, label_outcome, sha256


def _candidate():
    return TradeCandidate(candidate_id="c", event_id="e0", session_id="s", strategy="x", direction="long", entry_ticks=100, stop_ticks=99, status="accepted", trading_config_revision="t", strategy_config_revision="s")


def _event(event_id, timestamp, sequence, price):
    return TradeEvent(event_id=event_id, timestamp=timestamp, symbol="MES", sequence=sequence, price_ticks=price, quantity=1)


def test_same_timestamp_sequence_order_and_unknown_sequence_ambiguity():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    def outcome(stop_sequence, target_sequence):
        events = [_event("e0", start, 1, 100), _event("stop", start + timedelta(seconds=1), stop_sequence, 99), _event("target", start + timedelta(seconds=1), target_sequence, 102)]
        return label_outcome(_candidate(), events).label
    assert outcome(2, 3) is OutcomeLabel.STOP_FIRST
    assert outcome(3, 2) is OutcomeLabel.TARGET_FIRST
    assert outcome(None, None) is OutcomeLabel.AMBIGUOUS
    assert outcome(2, 2) is OutcomeLabel.AMBIGUOUS


def _args(tmp_path, experiment="e1"):
    source = tmp_path / "input.jsonl"
    source.write_text((__import__("pathlib").Path(__file__).parent / "fixtures" / "acceptance_breakout.jsonl").read_text())
    paths = {name: tmp_path / name for name in ("decisions.jsonl", "events.parquet", "decisions.parquet", "outcomes.parquet", "shadow.parquet", "report.json", "ledger.jsonl")}
    values = [str(source), "--decisions", str(paths["decisions.jsonl"]), "--events-parquet", str(paths["events.parquet"]), "--decisions-parquet", str(paths["decisions.parquet"]), "--outcomes-parquet", str(paths["outcomes.parquet"]), "--shadow-parquet", str(paths["shadow.parquet"]), "--report", str(paths["report.json"]), "--ledger", str(paths["ledger.jsonl"]), "--experiment-id", experiment, "--author", "test", "--hypothesis", "h", "--created-at", "2025-01-01T00:00:00Z"]
    return paths, ["research", *values]


def test_exact_rerun_and_hash_semantics(tmp_path):
    paths, args = _args(tmp_path)
    assert cli.main(args) == 0
    before = {p: p.read_bytes() for p in paths.values()}
    assert cli.main(args) == 0
    assert {p: p.read_bytes() for p in paths.values()} == before
    lines = paths["ledger.jsonl"].read_bytes().splitlines()
    assert len(lines) == 1
    manifest = json.loads(lines[0])
    assert manifest["output_hash"] == sha256(canonical_json(manifest["artifact_hashes"]))
    report = json.loads(paths["report.json"].read_bytes())
    assert "output_hash" not in report
    assert report["data_output_hash"] == sha256(canonical_json({k: v for k, v in manifest["artifact_hashes"].items() if k not in {"report", "decisions"}}))


def test_changed_same_id_is_immutable_failure(tmp_path):
    paths, args = _args(tmp_path)
    assert cli.main(args) == 0
    before = {p: p.read_bytes() for p in paths.values()}
    (tmp_path / "input.jsonl").write_text((tmp_path / "input.jsonl").read_text() + "\n")
    with pytest.raises(SystemExit):
        cli.main(args)
    assert {p: p.read_bytes() for p in paths.values()} == before


@pytest.mark.parametrize("failure_at", [1, 3, 7])
def test_replace_failure_restores_all_outputs(tmp_path, monkeypatch, failure_at):
    paths, args = _args(tmp_path)
    assert cli.main(args) == 0
    before = {p: p.read_bytes() for p in paths.values()}
    original = cli.os.replace
    calls = 0
    def fail_once(src, dst):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected replace failure")
        return original(src, dst)
    monkeypatch.setattr(cli.os, "replace", fail_once)
    with pytest.raises(SystemExit):
        cli.main([*args, "--overwrite"])
    assert {p: p.read_bytes() for p in paths.values()} == before


def test_ledger_failure_restores_outputs(tmp_path, monkeypatch):
    paths, args = _args(tmp_path)
    assert cli.main(args) == 0
    before = {p: p.read_bytes() for p in paths.values()}
    monkeypatch.setattr(cli, "append_manifest", lambda *a: (_ for _ in ()).throw(OSError("injected append failure")))
    with pytest.raises(SystemExit):
        cli.main([*args, "--overwrite"])
    assert {p: p.read_bytes() for p in paths.values()} == before

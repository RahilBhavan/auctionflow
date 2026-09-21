import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from auctionflow.cli import main
from auctionflow.domain import TradeCandidate, TradeEvent
from auctionflow.research import (
    EXPERIMENT_MANIFEST_SCHEMA,
    ExperimentManifest,
    OutcomeLabel,
    label_outcome,
)

TS = datetime(2025, 1, 2, 14, 36, tzinfo=UTC)

def candidate(entry, stop, direction):
    return TradeCandidate(candidate_id=f"c-{entry}-{stop}", event_id="e0", session_id="s", strategy="x", direction=direction, entry_ticks=entry, stop_ticks=stop, status="accepted", quantity=1, trading_config_revision="t", strategy_config_revision="s")

def events(prices, direction="long"):
    return [TradeEvent(event_id="e0" if i == 0 else f"e{i}", timestamp=TS + timedelta(seconds=i), symbol="MES", sequence=i, price_ticks=p, quantity=1) for i, p in enumerate(prices)]

def test_rounded_target_geometry_and_pnl_odd_even_long_short():
    for entry, stop, direction, prices, target, gross in [(100, 99, "long", [100, 102], 102, 250), (100, 98, "long", [100, 103], 103, 375), (100, 101, "short", [100, 98], 98, 250), (100, 102, "short", [100, 97], 97, 375)]:
        out = label_outcome(candidate(entry, stop, direction), events(prices))
        assert (out.label, out.target_ticks, out.gross_cents) == (OutcomeLabel.TARGET_FIRST, target, gross)

def test_resolution_metrics_stop_at_resolving_event():
    out = label_outcome(candidate(100, 99, "long"), events([100, 101, 102, 110, 90]))
    assert out.events_to_resolution == 2
    assert out.seconds_to_resolution == 2.0
    assert out.mfe_ticks == 2 and out.mae_ticks == 0

def test_unknown_metrics_are_bounded_and_explicit():
    out = label_outcome(candidate(100, 99, "long"), events([100, 101, 100, 101]))
    assert out.label is OutcomeLabel.UNKNOWN
    assert out.events_to_resolution is None and out.seconds_to_resolution is None
    assert out.mfe_ticks == 1 and out.mae_ticks == 0

def test_manifest_schema_is_explicit_and_roundtrips_metadata():
    manifest = ExperimentManifest(experiment_id="x", author="a", created_at="now", hypothesis="h", input_hash="i", output_hash="o", artifact_hashes={"report": "r"}, summary={"n": 1})
    assert EXPERIMENT_MANIFEST_SCHEMA != pa.schema([])
    assert {field.name for field in EXPERIMENT_MANIFEST_SCHEMA} >= {"experiment_id", "artifact_hashes", "summary"}
    row = manifest.model_dump(mode="json")
    table = pa.Table.from_pylist([{k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in row.items()}], schema=pa.schema([(f.name, pa.string()) for f in EXPERIMENT_MANIFEST_SCHEMA]))
    assert table.column("experiment_id")[0].as_py() == "x"

def test_cli_flat_and_envelope_workflows_and_provenance(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "acceptance_breakout.jsonl"
    flat = tmp_path / "flat.jsonl"; flat.write_bytes(fixture.read_bytes())
    envelope = tmp_path / "envelope.jsonl"
    envelope.write_text("".join(json.dumps({"event_id": r["event_id"], "timestamp": r["timestamp"], "payload": r}) + "\n" for r in map(json.loads, fixture.read_text().splitlines())))
    def run(source, prefix):
        paths = {n: tmp_path / f"{prefix}-{n}" for n in ("decisions.jsonl", "events.parquet", "decisions.parquet", "outcomes.parquet", "shadow.parquet", "report.json", "ledger.jsonl")}
        args = [str(source), "--decisions", str(paths["decisions.jsonl"]), "--events-parquet", str(paths["events.parquet"]), "--decisions-parquet", str(paths["decisions.parquet"]), "--outcomes-parquet", str(paths["outcomes.parquet"]), "--shadow-parquet", str(paths["shadow.parquet"]), "--report", str(paths["report.json"]), "--ledger", str(paths["ledger.jsonl"]), "--experiment-id", prefix, "--author", "a", "--hypothesis", "h", "--created-at", "now"]
        assert main(["research", *args]) == 0
        return paths
    p, q = run(flat, "f"), run(envelope, "e")
    assert json.loads(p["report.json"].read_text())["counts"] == json.loads(q["report.json"].read_text())["counts"]
    assert pq.read_table(p["events.parquet"]).column("session_id")[0].as_py()

def test_cli_envelope_mismatch_fails_closed(tmp_path):
    source = tmp_path / "bad.jsonl"; source.write_text(json.dumps({"event_id": "outer", "timestamp": TS.isoformat(), "payload": {"event_id": "inner", "timestamp": TS.isoformat(), "symbol": "MES", "price_ticks": 100, "quantity": 1}}) + "\n")
    with pytest.raises(SystemExit): main(["research", str(source), "--decisions", str(tmp_path/"d"), "--events-parquet", str(tmp_path/"e"), "--decisions-parquet", str(tmp_path/"dp"), "--outcomes-parquet", str(tmp_path/"o"), "--shadow-parquet", str(tmp_path/"s"), "--report", str(tmp_path/"r"), "--ledger", str(tmp_path/"l"), "--experiment-id", "x", "--author", "a", "--hypothesis", "h", "--created-at", "now"])

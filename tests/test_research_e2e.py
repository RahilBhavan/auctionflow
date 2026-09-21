from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq

from auctionflow.cli import main
from auctionflow.research import DECISION_SCHEMA, NORMALIZED_EVENT_SCHEMA, OUTCOME_SCHEMA

ROOT = Path(__file__).parent


def test_research_cli_writes_explicit_research_artifacts_and_is_idempotent(tmp_path):
    source = ROOT / "fixtures" / "acceptance_breakout.jsonl"
    paths = {name: tmp_path / name for name in (
        "decisions.jsonl", "events.parquet", "decisions.parquet",
        "outcomes.parquet", "shadow.parquet", "report.json", "ledger.jsonl",
    )}
    args = [str(source), "--decisions", str(paths["decisions.jsonl"])]
    for option, name in (("events-parquet", "events.parquet"), ("decisions-parquet", "decisions.parquet"),
                         ("outcomes-parquet", "outcomes.parquet"), ("shadow-parquet", "shadow.parquet"),
                         ("report", "report.json"), ("ledger", "ledger.jsonl")):
        args += [f"--{option}", str(paths[name])]
    args += ["--experiment-id", "e2e", "--author", "tests", "--hypothesis", "fixture only",
             "--created-at", "2025-01-01T00:00:00Z"]
    assert main(["research", *args]) == 0
    first = {p: paths[p].read_bytes() for p in paths}
    assert main(["research", *args, "--overwrite"]) == 0
    assert {p: paths[p].read_bytes() for p in paths} == first

    assert pq.read_schema(paths["events.parquet"]) == NORMALIZED_EVENT_SCHEMA
    assert pq.read_schema(paths["decisions.parquet"]) == DECISION_SCHEMA
    assert pq.read_schema(paths["outcomes.parquet"]) == OUTCOME_SCHEMA
    assert pq.read_schema(paths["shadow.parquet"]) == OUTCOME_SCHEMA
    report = json.loads(paths["report.json"].read_bytes())
    assert report["generation_mode"] == "OFFLINE_RESEARCH_ONLY"
    assert "profitability" in report["disclaimer"]
    assert len(paths["ledger.jsonl"].read_bytes().splitlines()) == 1
    assert report["input_hash"] == sha256(source.read_bytes()).hexdigest()
    ledger = json.loads(paths["ledger.jsonl"].read_text())
    ledger_text = paths["ledger.jsonl"].read_text()
    assert str(tmp_path) not in ledger_text
    assert str(Path.home()) not in ledger_text
    assert ledger["artifact_paths"] == {
        "decisions": "decisions.jsonl",
        "events": "events.parquet",
        "decisions_parquet": "decisions.parquet",
        "outcomes": "outcomes.parquet",
        "shadow": "shadow.parquet",
        "report": "report.json",
    }
    assert all("/" not in path for path in ledger["artifact_paths"].values())
    assert all(p.exists() for p in paths.values())

import json
from datetime import UTC, datetime

import pyarrow.parquet as pq
import pytest

from auctionflow.replay import ReplayError, replay


def line(i, price=100, sequence=None):
    value = {"event_id": f"e{i}", "timestamp": datetime(2025, 1, 2, 14, 36 + i, tzinfo=UTC).isoformat(),
             "symbol": "MES", "price_ticks": price, "quantity": 1}
    if sequence is not None:
        value["sequence"] = sequence
    return json.dumps(value)


def test_replay_checkpoints_progress_and_preserves_raw(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text("\n".join([line(1, sequence=1), line(2, 101, 2)]) + "\n")
    decisions = tmp_path / "decisions.jsonl"
    raw = tmp_path / "raw.jsonl"
    replay(source, decisions, raw_path=raw)
    assert raw.read_bytes() == source.read_bytes()
    rows = [json.loads(x) for x in decisions.read_text().splitlines()]
    assert [r["checkpoint"] for r in rows] == [1, 2]
    assert all("account_snapshot_ref" in r and "strategy_snapshot" in r for r in rows)


def test_malformed_and_sequence_gap_fail_loudly(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    with pytest.raises(ReplayError, match=r"line 1, event_id None"):
        replay(bad, tmp_path / "out.jsonl")
    gap = tmp_path / "gap.jsonl"
    gap.write_text(line(1, sequence=1) + "\n" + line(2, sequence=3) + "\n")
    with pytest.raises(ReplayError, match="sequence gap"):
        replay(gap, tmp_path / "out.jsonl")


def test_parquet_exports(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text(line(1) + "\n")
    decisions = tmp_path / "decisions.jsonl"
    normalized = tmp_path / "normalized.parquet"
    parquet = tmp_path / "decisions.parquet"
    replay(source, decisions, parquet_path=parquet, normalized_parquet_path=normalized)
    assert pq.read_table(parquet).num_rows == 1
    assert pq.read_table(normalized).num_rows == 1

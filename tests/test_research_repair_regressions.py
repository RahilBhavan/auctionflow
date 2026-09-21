from datetime import UTC, datetime, timedelta

import pyarrow.parquet as pq

from auctionflow.domain import TradeCandidate, TradeEvent
from auctionflow.research import (
    EXPERIMENT_MANIFEST_SCHEMA,
    ExperimentManifest,
    OutcomeLabel,
    label_outcome,
    rows_to_table,
    write_parquet_atomic,
)


def event(i, price):
    return TradeEvent(event_id=f"e{i}", timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=i), symbol="MES", sequence=i, price_ticks=price, quantity=1)


def candidate(direction="long"):
    return TradeCandidate(candidate_id="c", event_id="e0", session_id="s", strategy="x", direction=direction, entry_ticks=100, stop_ticks=99 if direction == "long" else 101, status="accepted", quantity=1, trading_config_revision="t", strategy_config_revision="s")


def test_target_rounding_and_long_short_pnl():
    long = label_outcome(candidate(), [event(0, 100), event(1, 102)])
    short = label_outcome(candidate("short"), [event(0, 100), event(1, 98)])
    assert (long.target_ticks, long.label, long.gross_cents) == (102, OutcomeLabel.TARGET_FIRST, 250)
    assert (short.target_ticks, short.label, short.gross_cents) == (98, OutcomeLabel.TARGET_FIRST, 250)


def test_resolution_metrics_stop_at_resolving_event_and_are_bounded():
    out = label_outcome(candidate(), [event(0, 100), event(1, 101), event(2, 102), event(3, 110), event(4, 80)])
    assert out.events_to_resolution == 2
    assert out.seconds_to_resolution == 2
    assert (out.mfe_ticks, out.mae_ticks) == (2, 0)


def test_experiment_manifest_schema_empty_and_one_row_roundtrip(tmp_path):
    manifest = ExperimentManifest(experiment_id="x", author="a", created_at="now", hypothesis="h", input_hash="i", output_hash="o", artifact_hashes={"input": "i"})
    assert rows_to_table([], EXPERIMENT_MANIFEST_SCHEMA, ExperimentManifest).schema == EXPERIMENT_MANIFEST_SCHEMA
    path = tmp_path / "manifest.parquet"
    write_parquet_atomic(path, [manifest], EXPERIMENT_MANIFEST_SCHEMA, ExperimentManifest)
    table = pq.read_table(path)
    assert table.schema == EXPERIMENT_MANIFEST_SCHEMA
    assert table["artifact_hashes"].to_pylist() == ['{"input":"i"}']

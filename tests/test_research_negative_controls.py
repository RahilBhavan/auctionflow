from __future__ import annotations

from datetime import UTC, datetime, timedelta

from auctionflow.domain import TradeCandidate, TradeEvent
from auctionflow.research import OutcomeLabel, label_outcome

T0 = datetime(2025, 1, 2, 14, 0, tzinfo=UTC)


def candidate(direction="long", entry=100, stop=98):
    if direction == "short":
        stop = 102
    return TradeCandidate(candidate_id="negative-control", event_id="entry", session_id="session",
                          strategy="mouse_trap", direction=direction, entry_ticks=entry,
                          stop_ticks=stop, status="accepted", quantity=1,
                          trading_config_revision="trading-v1", strategy_config_revision="strategy-v1")


def trade(event_id, price, seconds, sequence):
    return TradeEvent(event_id=event_id, timestamp=T0 + timedelta(seconds=seconds), symbol="MES",
                      sequence=sequence, price_ticks=price, quantity=1)


def test_direction_flip_changes_label_without_changing_candidate_geometry():
    events = [trade("entry", 100, 0, 1), trade("future", 103, 1, 2)]
    original = label_outcome(candidate(), events)
    flipped = label_outcome(candidate("short"), events)
    assert original.label is OutcomeLabel.TARGET_FIRST
    assert flipped.label is OutcomeLabel.STOP_FIRST
    assert original.candidate_id == flipped.candidate_id


def test_shifted_candidate_timestamp_excludes_prior_touch():
    events = [trade("before", 103, 1, 1), trade("entry", 100, 2, 2)]
    result = label_outcome(candidate(), events)
    assert result.label is OutcomeLabel.UNKNOWN
    assert result.start_event_id == "entry"


def test_random_level_without_candidate_is_not_labeled():
    assert label_outcome(candidate(entry=100, stop=98), [trade("entry", 100, 0, 1)]).label is OutcomeLabel.UNKNOWN


def test_future_mutation_changes_only_post_hoc_label():
    before = [trade("entry", 100, 0, 1), trade("future", 101, 1, 2)]
    after = [*before[:-1], trade("future", 103, 1, 2)]
    first = label_outcome(candidate(), before)
    second = label_outcome(candidate(), after)
    assert first.candidate_id == second.candidate_id
    assert first.entry_ticks == second.entry_ticks and first.stop_ticks == second.stop_ticks
    assert first.label is OutcomeLabel.UNKNOWN
    assert second.label is OutcomeLabel.TARGET_FIRST

from datetime import UTC, datetime, timedelta

import pytest

from auctionflow.domain import ResetEvent, TradeCandidate, TradeEvent
from auctionflow.research import OutcomeLabel, label_candidates, label_outcome

T = datetime(2025, 1, 1, 14, 0, tzinfo=UTC)

def candidate(direction="long", status="accepted", entry=100, stop=98):
    stop = stop if direction == "long" else entry + abs(entry - stop)
    return TradeCandidate(candidate_id="c", event_id="start", session_id="s", strategy="x", direction=direction, entry_ticks=entry, stop_ticks=stop, status=status, quantity=1, trading_config_revision="t", strategy_config_revision="s")

def trade(i, price, **kw):
    return TradeEvent(event_id=i, timestamp=T + timedelta(seconds=int(i) if str(i).isdigit() else 1), symbol="MES", sequence=kw.pop("sequence", None), price_ticks=price, quantity=1, **kw)

def stream(*events):
    return [trade("start", 100), *events]

@pytest.mark.parametrize("direction,price,label", [("long",103,OutcomeLabel.TARGET_FIRST),("long",98,OutcomeLabel.STOP_FIRST),("short",97,OutcomeLabel.TARGET_FIRST),("short",102,OutcomeLabel.STOP_FIRST)])
def test_directional_resolution(direction, price, label):
    assert label_outcome(candidate(direction), stream(trade("end", price))).label == label

def test_unknown_at_session_end_and_truncation():
    result = label_outcome(candidate(), stream(trade("x", 101)), session_end=T + timedelta(seconds=1))
    assert result.label is OutcomeLabel.UNKNOWN
    assert label_outcome(candidate(), [trade("start", 100)]).label is OutcomeLabel.UNKNOWN

def test_reset_and_session_mismatch_unknown():
    reset = ResetEvent(event_id="r", timestamp=T + timedelta(seconds=1), symbol="MES")
    assert label_outcome(candidate(), stream(reset)).label is OutcomeLabel.UNKNOWN
    bad = trade("bad", 103)
    assert label_outcome(candidate(), stream(bad)).label is OutcomeLabel.TARGET_FIRST

def test_same_event_can_be_ambiguous():
    assert label_outcome(candidate(entry=100, stop=98), stream(trade("end", 98))).label is OutcomeLabel.STOP_FIRST

def test_shadow_and_accepted_books():
    accepted, shadow = label_candidates([candidate(), candidate(status="rejected")], stream(trade("end", 103)))
    assert len(accepted) == len(shadow) == 1

def test_strictly_later_events_only():
    assert label_outcome(candidate(), [trade("start", 98)]).label is OutcomeLabel.UNKNOWN

def test_rounding_away_from_entry():
    result = label_outcome(candidate(entry=100, stop=97), stream(trade("x", 104)))
    assert result.target_ticks == 105

def test_mfe_mae_and_cents():
    result = label_outcome(candidate(), stream(trade("a", 101), trade("b", 99), trade("end", 103)))
    assert result.mfe_ticks == 3 and result.mae_ticks == -1 and result.gross_cents == 375

@pytest.mark.parametrize("quantity,price,gross", [(1, 103, 375), (2, 103, 750), (1, 98, -250), (2, 98, -500)])
def test_round_trip_costs_apply_to_resolved_trades(quantity, price, gross):
    result = label_outcome(candidate().model_copy(update={"quantity": quantity}), stream(trade("end", price)), commission_cents_per_contract=10, slippage_cents_per_contract=15)
    assert result.gross_cents == gross
    assert result.net_cents == gross - 25 * quantity

def test_zero_costs_preserve_net_and_unresolved_is_zero():
    assert label_outcome(candidate(), stream(trade("end", 103))).net_cents == 375
    assert label_outcome(candidate(), [trade("start", 100)], commission_cents_per_contract=10).net_cents == 0

def test_costs_must_be_nonnegative():
    with pytest.raises(ValueError, match="nonnegative"):
        label_outcome(candidate(), stream(trade("end", 103)), slippage_cents_per_contract=-1)

def test_non_actionable_is_omitted():
    c = candidate(status="rejected")
    c = c.model_copy(update={"entry_ticks": 100, "stop_ticks": 98})
    assert label_candidates([], stream())[0] == []

@pytest.mark.parametrize("n", range(12))
def test_future_mutation_does_not_change_candidate(n):
    c = candidate().model_dump_json()
    assert candidate().model_dump_json() == c

from datetime import UTC, datetime

import pytest

from auctionflow.decision import evaluate
from auctionflow.domain import (
    AccountState,
    Decision,
    DepthEvent,
    QuoteEvent,
    ResetEvent,
    TradeEvent,
)
from auctionflow.rules import update_account_eod
from auctionflow.strategies import StrategyConfig, acceptance_breakout


def account(**updates):
    return AccountState(account_id="a", session_id="2025-01-02", balance_cents=5_000_000,
                        mll_floor_cents=4_800_000, **updates)


def event(kind="trade", **values):
    base = {"event_id": "e", "timestamp": datetime(2025, 1, 2, 14, 36, tzinfo=UTC),
            "symbol": "MES", "kind": kind, "sequence": 1}
    return base | ({"price_ticks": 101, "quantity": 1} if kind == "trade" else {}) | values


def test_short_position_blocks():
    assert evaluate(event(), account(open_position=-1)).record.decision == Decision.BLOCK_ACCOUNT


def test_eod_rejects_negative_and_preserves_safety():
    with pytest.raises(ValueError):
        update_account_eod(account(), -1)
    value = update_account_eod(account(token_consumed=True, opening_attempts=2), 4_900_000)
    assert value.token_consumed and value.opening_attempts == 2 and value.session_id == "2025-01-02"


def test_all_declared_market_events_validate():
    assert QuoteEvent.model_validate(event("quote", bid_ticks=100, ask_ticks=101)).kind == "quote"
    assert DepthEvent.model_validate(event("depth", price_ticks=100, quantity=2, side="bid")).kind == "depth"
    assert ResetEvent.model_validate(event("reset")).kind == "reset"


def test_reset_clears_strategy_but_not_account_safety():
    result = evaluate(event("reset"), account(token_consumed=True))
    assert result.strategy.state == "dormant"
    assert result.account.token_consumed


def test_event_by_event_acceptance_progress_matches_batch():
    config = StrategyConfig(boundary=100, displacement_ticks=2, acceptance_events=2, continuation_ticks=2)
    prices = [103, 104, 105, 100, 102]
    state = None
    for i, price in enumerate(prices):
        state = acceptance_breakout([TradeEvent.model_validate(event(price_ticks=price, event_id=str(i)))], config, state)
    batch = acceptance_breakout([event(price_ticks=p, event_id=str(i)) for i, p in enumerate(prices)], config)
    assert state == batch and state.state == "continuation_confirmed"


def test_top_level_qualifiers_reach_evaluator():
    config = StrategyConfig(reference_level=100, boundary=None, confirmations=1)
    result = evaluate(event(price_ticks=103, aggressive=True, poor_response=True), account(), strategy_config=config)
    assert result.strategy.mouse.state == "swept"


def test_malformed_top_level_qualifier_is_rejected():
    with pytest.raises(ValueError):
        evaluate(event(price_ticks=103, aggressive="yes"), account())


def test_magic_session_values_are_rejected():
    result = evaluate(event(), AccountState(account_id="a", session_id="s", balance_cents=5_000_000, mll_floor_cents=4_800_000))
    assert result.record.decision == Decision.BLOCK_ACCOUNT

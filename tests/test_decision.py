from datetime import UTC, datetime

from auctionflow.config import TradingConfig
from auctionflow.decision import evaluate
from auctionflow.domain import AccountState, Decision
from auctionflow.strategies import StrategyConfig


def event(i=1, price=100, **extra):
    return {"event_id": f"e{i}", "timestamp": datetime(2025, 1, 2, 14, 36, tzinfo=UTC),
            "symbol": "MES", "price_ticks": price, "quantity": 1, **extra}


def account(**updates):
    base = {"account_id": "a", "session_id": "2025-01-02", "balance_cents": 5_000_000,
            "mll_floor_cents": 4_800_000}
    return AccountState(**(base | updates))


def test_fixed_gates_and_authorization_token():
    assert evaluate(event(), account(health_known=False)).record.decision == Decision.BLOCK_ACCOUNT
    assert evaluate(event(), account(), TradingConfig(symbol="NQ")).record.decision == Decision.BLOCK_ACCOUNT
    outside = event(timestamp=datetime(2025, 1, 2, 17, 0, tzinfo=UTC))
    assert evaluate(outside, account(session_id="2025-01-02")).record.decision == Decision.NO_TRADE
    assert evaluate(event(), account(open_position=1)).record.decision == Decision.BLOCK_ACCOUNT
    assert evaluate(event(), account(token_consumed=True)).record.decision == Decision.BLOCK_ACCOUNT


def test_incomplete_setup_and_insufficient_risk():
    config = StrategyConfig(reference_level=100, boundary=None)
    result = evaluate({**event(price=101), "metadata": {"aggressive": True, "poor_response": True}}, account(), strategy_config=config)
    assert result.record.decision in (Decision.WATCH, Decision.NO_TRADE)
    poor = account(mll_floor_cents=5_000_000)
    result = evaluate({**event(price=101), "metadata": {"aggressive": True, "poor_response": True}}, poor, strategy_config=config)
    assert result.record.decision == Decision.BLOCK_ACCOUNT
    assert result.strategy.state == "dormant"


def test_strategy_state_is_returned_without_future_leakage():
    config = StrategyConfig(reference_level=100, boundary=None, confirmations=2)
    first = evaluate(event(1, 103), account(), strategy_config=config)
    later = evaluate(event(2, 100), account(), strategy_config=config, strategy=first.strategy)
    assert first.record.event_id == "e1"
    assert later.record.event_id == "e2"
    assert first.strategy.state != "armed"

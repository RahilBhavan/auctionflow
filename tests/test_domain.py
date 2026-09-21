from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auctionflow.domain import AccountState, Event, QuoteEvent, TradeEvent


def test_models_are_immutable_and_validate_boundaries():
    event = Event(event_id="e1", timestamp=datetime.now(UTC), symbol="MES")
    with pytest.raises(ValidationError):
        event.event_id = "changed"
    with pytest.raises(ValidationError):
        Event(event_id="", timestamp=event.timestamp, symbol="MES")
    with pytest.raises(ValidationError):
        TradeEvent(event_id="e1", timestamp=event.timestamp, symbol="MES", quantity=0, price_ticks=1)
    with pytest.raises(ValidationError):
        QuoteEvent(event_id="e1", timestamp=event.timestamp, symbol="MES", bid_ticks=2, ask_ticks=1)


def test_account_buffer_and_fail_closed_state_are_explicit():
    account = AccountState(
        account_id="a", session_id="2026-08-03", balance_cents=5_000_000, mll_floor_cents=4_800_000,
        health_known=False, position_state_known=False, protective_state_known=False,
    )
    assert account.mll_buffer_cents == 200_000
    assert account.health_known is False
    assert account.position_state_known is False
    assert account.protective_state_known is False
    assert account.authorization_allowed is False
    assert AccountState(account_id="a", session_id="2025-01-02", balance_cents=1, mll_floor_cents=0).authorization_allowed
    assert not AccountState(account_id="a", session_id="2025-01-02", balance_cents=1, mll_floor_cents=0, token_consumed=True).authorization_allowed
    assert not AccountState(account_id="a", session_id="2025-01-02", balance_cents=1, mll_floor_cents=0, open_position=1).authorization_allowed

    with pytest.raises(ValidationError):
        AccountState(account_id="a", session_id="2025-01-02", balance_cents=-1, mll_floor_cents=0)

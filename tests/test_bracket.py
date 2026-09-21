from datetime import UTC, datetime

import pytest

from auctionflow.bracket import BracketIntent, build_bracket_intent, validate_stop_change
from auctionflow.domain import TradeCandidate
from auctionflow.reconciliation import ReconciliationResult


def parts(direction="long", qty=2, stop=95, entry=100):
    candidate = TradeCandidate(candidate_id="c", event_id="e", session_id="s", strategy="x", direction=direction,
        entry_ticks=entry, stop_ticks=stop, status="accepted", quantity=qty, trading_config_revision="t",
        strategy_config_revision="srev")
    reconciliation = ReconciliationResult(status="MATCH", blocking=False, fingerprint="f", reason_codes=(),
        account_id="a", session_id="s", observed_at=datetime(2026, 1, 1, tzinfo=UTC))
    return candidate, reconciliation


def test_builds_exact_outward_targets_and_deterministic_id():
    c, r = parts(stop=97, entry=100)
    one = build_bracket_intent(c, "a", "d", r, datetime(2026, 1, 1, tzinfo=UTC))
    two = build_bracket_intent(c, "a", "d", r, datetime(2026, 1, 1, tzinfo=UTC))
    assert one.target_ticks == 105 and one.intent_id == two.intent_id
    assert BracketIntent.model_validate(one.model_dump()) == one
    tampered = one.model_dump()
    tampered["intent_id"] = "0" * 64
    with pytest.raises(ValueError):
        BracketIntent.model_validate(tampered)
    c, r = parts("short", stop=103, entry=100)
    assert build_bracket_intent(c, "a", "d", r, datetime(2026, 1, 1, tzinfo=UTC)).target_ticks == 95


def test_whitespace_identifiers_are_rejected():
    c, r = parts()
    intent = build_bracket_intent(c, "a", "d", r, datetime(2026, 1, 1, tzinfo=UTC))
    for field in ("decision_id", "account_id", "session_id", "strategy", "trading_config_revision", "strategy_config_revision", "reconciliation_fingerprint"):
        bad = intent.model_dump()
        bad[field] = "   "
        with pytest.raises(ValueError):
            BracketIntent.model_validate(bad)


def test_stop_changes_are_pure_and_never_widen_or_cross():
    c, r = parts(stop=95)
    intent = build_bracket_intent(c, "a", "d", r, datetime(2026, 1, 1, tzinfo=UTC))
    assert validate_stop_change(intent, 97).stop_ticks == 97
    with pytest.raises(ValueError):
        validate_stop_change(intent, 94)
    with pytest.raises(ValueError):
        validate_stop_change(intent, 100)


def test_contract_is_frozen_and_flags_are_literal():
    c, r = parts()
    intent = build_bracket_intent(c, "a", "d", r, datetime(2026, 1, 1, tzinfo=UTC))
    assert intent.mode == "SIMULATION_ONLY" and intent.oco_required and not intent.scale_in_allowed
    with pytest.raises((TypeError, ValueError)):
        intent.stop_ticks = 99
    with pytest.raises(ValueError):
        BracketIntent.model_validate({**intent.model_dump(), "quantity": 0})

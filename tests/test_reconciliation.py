from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from auctionflow.reconciliation import (
    ExpectedAccountState,
    ObservedBrokerTelemetry,
    ReconciliationResult,
    reconcile,
    state_store_reconcile_fields,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def snapshot(model, **changes):
    values = {"account_id": "a", "session_id": "s", "position_quantity": 0, "position_known": True,
              "working_entry_orders": 0, "protective_stop_orders": 0,
              "protective_target_orders": 0, "orders_known": True, "observed_at": NOW}
    values.update(changes)
    return model(**values)


def test_match_and_state_store_fields():
    result = reconcile(snapshot(ExpectedAccountState), snapshot(ObservedBrokerTelemetry))
    assert result.status == "MATCH"
    assert not result.blocking
    assert state_store_reconcile_fields(result)["open_position"] == 0


def test_missing_unknown_and_mismatch_cases():
    expected = snapshot(ExpectedAccountState)
    assert reconcile(expected, None).reason_codes == ("OBSERVED_SNAPSHOT_MISSING",)
    unknown = reconcile(expected, snapshot(ObservedBrokerTelemetry, position_known=False, orders_known=False))
    assert unknown.status == "UNKNOWN" and unknown.blocking
    mismatch = reconcile(snapshot(ExpectedAccountState, account_id="x", session_id="t"),
                         snapshot(ObservedBrokerTelemetry, account_id="a", session_id="s",
                                  position_quantity=2, working_entry_orders=1,
                                  protective_stop_orders=1, protective_target_orders=1))
    assert mismatch.reason_codes == ("ACCOUNT_ID_MISMATCH", "SESSION_ID_MISMATCH", "POSITION_MISMATCH",
                                     "WORKING_ENTRY_ORDERS", "ORPHAN_PROTECTIVE_STOP",
                                     "ORPHAN_PROTECTIVE_TARGET")


def test_freshness_stale_blocks_and_equal_or_later_matches():
    expected = snapshot(ExpectedAccountState, observed_at=NOW)
    stale = reconcile(expected, snapshot(ObservedBrokerTelemetry, observed_at=NOW - timedelta(days=1)))
    assert stale.status == "UNKNOWN" and stale.blocking
    assert stale.reason_codes == ("OBSERVED_SNAPSHOT_STALE",)
    with pytest.raises(ValueError):
        state_store_reconcile_fields(stale)
    assert reconcile(expected, snapshot(ObservedBrokerTelemetry, observed_at=NOW)).status == "MATCH"
    assert reconcile(expected, snapshot(ObservedBrokerTelemetry,
                                        observed_at=NOW.replace(day=2))).status == "MATCH"


def test_fingerprint_is_deterministic_and_open_expected_is_rejected():
    observed = snapshot(ObservedBrokerTelemetry)
    assert reconcile(snapshot(ExpectedAccountState), observed).fingerprint == reconcile(
        snapshot(ExpectedAccountState), observed).fingerprint
    result = reconcile(snapshot(ExpectedAccountState, position_quantity=1), observed)
    assert result.status == "MISMATCH"
    assert result.reason_codes[0] == "EXPECTED_OPEN_POSITION_UNSUPPORTED"


def test_strict_types_utc_and_extra_forbidden():
    with pytest.raises(ValidationError):
        snapshot(ExpectedAccountState, position_quantity=True)
    with pytest.raises(ValidationError):
        snapshot(ExpectedAccountState, observed_at="2026-01-01")
    with pytest.raises(ValidationError):
        snapshot(ExpectedAccountState, unexpected=1)
    with pytest.raises(ValidationError):
        snapshot(ExpectedAccountState, schema_version="other")
    with pytest.raises(ValidationError):
        snapshot(ObservedBrokerTelemetry, schema_version="other")
    with pytest.raises(ValidationError):
        ReconciliationResult(schema_version="other", status="MATCH", blocking=False,
                             fingerprint="f", reason_codes=(), account_id="a", session_id="s",
                             observed_at=NOW)
    with pytest.raises(ValueError):
        state_store_reconcile_fields(reconcile(snapshot(ExpectedAccountState), None))

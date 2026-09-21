from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .research import canonical_json, sha256

RECONCILIATION_SCHEMA_VERSION = "auctionflow.reconciliation.v1"


ReconciliationVersion = Literal[RECONCILIATION_SCHEMA_VERSION]


class NormalizedSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: ReconciliationVersion = RECONCILIATION_SCHEMA_VERSION
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    position_quantity: int = Field(ge=0)
    position_known: bool
    working_entry_orders: int = Field(ge=0)
    protective_stop_orders: int = Field(ge=0)
    protective_target_orders: int = Field(ge=0)
    orders_known: bool
    observed_at: datetime

    @field_validator("account_id", "session_id")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must be nonempty")
        return value

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("observed_at must be timezone-aware UTC")
        return value


class ExpectedAccountState(NormalizedSnapshot):
    pass


class ObservedBrokerTelemetry(NormalizedSnapshot):
    pass


ReconciliationStatus = Literal["MATCH", "MISMATCH", "UNKNOWN"]


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: ReconciliationVersion = RECONCILIATION_SCHEMA_VERSION
    status: ReconciliationStatus
    blocking: bool
    fingerprint: str | None
    reason_codes: tuple[str, ...]
    account_id: str
    session_id: str
    observed_at: datetime | None

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)):
            raise ValueError("observed_at must be timezone-aware UTC")
        return value


def reconcile(expected: ExpectedAccountState, observed: ObservedBrokerTelemetry | None) -> ReconciliationResult:
    if observed is None:
        return ReconciliationResult(status="UNKNOWN", blocking=True, fingerprint=None,
            reason_codes=("OBSERVED_SNAPSHOT_MISSING",), account_id=expected.account_id,
            session_id=expected.session_id, observed_at=None)

    reasons: list[str] = []
    if expected.account_id != observed.account_id:
        reasons.append("ACCOUNT_ID_MISMATCH")
    if expected.session_id != observed.session_id:
        reasons.append("SESSION_ID_MISMATCH")
    # Freshness is ordered after identity and before telemetry knowledge checks.
    if observed.observed_at < expected.observed_at:
        reasons.append("OBSERVED_SNAPSHOT_STALE")
    if not observed.position_known:
        reasons.append("POSITION_UNKNOWN")
    if not observed.orders_known:
        reasons.append("ORDERS_UNKNOWN")
    if expected.position_quantity != 0:
        reasons.append("EXPECTED_OPEN_POSITION_UNSUPPORTED")
    if expected.position_quantity != observed.position_quantity:
        reasons.append("POSITION_MISMATCH")
    if observed.working_entry_orders:
        reasons.append("WORKING_ENTRY_ORDERS")
    if expected.position_quantity == 0 and observed.protective_stop_orders:
        reasons.append("ORPHAN_PROTECTIVE_STOP")
    if expected.position_quantity == 0 and observed.protective_target_orders:
        reasons.append("ORPHAN_PROTECTIVE_TARGET")

    status: ReconciliationStatus = "MISMATCH" if reasons else "MATCH"
    if (not observed.position_known or not observed.orders_known
            or "OBSERVED_SNAPSHOT_STALE" in reasons):
        status = "UNKNOWN"
    fingerprint = sha256(canonical_json(observed.model_dump(mode="json")))
    return ReconciliationResult(status=status, blocking=status != "MATCH", fingerprint=fingerprint,
        reason_codes=tuple(reasons), account_id=observed.account_id, session_id=observed.session_id,
        observed_at=observed.observed_at)


def state_store_reconcile_fields(result: ReconciliationResult) -> dict[str, object]:
    """Return only the fields accepted by StateStore.reconcile; perform no persistence."""
    if result.status != "MATCH" or result.fingerprint is None:
        raise ValueError("only a MATCH result can be applied to StateStore")
    return {"health_known": True, "open_position": 0, "protective_orders_known": True,
            "fingerprint": result.fingerprint}

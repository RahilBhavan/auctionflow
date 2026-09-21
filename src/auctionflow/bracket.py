from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import TradeCandidate
from .reconciliation import ReconciliationResult

BRACKET_SCHEMA_VERSION = "auctionflow.bracket.v1"


class BracketIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[BRACKET_SCHEMA_VERSION] = BRACKET_SCHEMA_VERSION
    intent_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    direction: Literal["long", "short"]
    quantity: Literal[1, 2]
    entry_ticks: int
    stop_ticks: int
    target_ticks: int
    target_ratio_numerator: Literal[3] = 3
    target_ratio_denominator: Literal[2] = 2
    entry_order_type: Literal["LIMIT"] = "LIMIT"
    stop_order_type: Literal["STOP"] = "STOP"
    target_order_type: Literal["LIMIT"] = "LIMIT"
    time_in_force: Literal["DAY"] = "DAY"
    oco_required: Literal[True] = True
    reduce_only_children: Literal[True] = True
    scale_in_allowed: Literal[False] = False
    reentry_allowed: Literal[False] = False
    stop_widening_allowed: Literal[False] = False
    trading_config_revision: str = Field(min_length=1)
    strategy_config_revision: str = Field(min_length=1)
    reconciliation_fingerprint: str = Field(min_length=1)
    created_at: datetime
    mode: Literal["SIMULATION_ONLY"] = "SIMULATION_ONLY"

    @field_validator("decision_id", "account_id", "session_id", "strategy", "trading_config_revision", "strategy_config_revision", "reconciliation_fingerprint")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must be nonempty")
        return value

    @field_validator("created_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def geometry(self) -> BracketIntent:
        if self.direction == "long" and not self.stop_ticks < self.entry_ticks < self.target_ticks:
            raise ValueError("long geometry must be stop < entry < target")
        if self.direction == "short" and not self.target_ticks < self.entry_ticks < self.stop_ticks:
            raise ValueError("short geometry must be target < entry < stop")
        if abs(self.target_ticks - self.entry_ticks) * self.target_ratio_denominator != (abs(self.entry_ticks - self.stop_ticks) * self.target_ratio_numerator + self.target_ratio_denominator - 1) // self.target_ratio_denominator * self.target_ratio_denominator:
            raise ValueError("target distance must equal ratio rounded outward")
        if self.intent_id != _intent_id(self.model_dump(mode="json")):
            raise ValueError("intent_id does not match semantic fields")
        return self


def _target(entry: int, stop: int, direction: str, numerator: int, denominator: int) -> int:
    distance = (abs(entry - stop) * numerator + denominator - 1) // denominator
    return entry + distance if direction == "long" else entry - distance


def _intent_id(fields: dict[str, object]) -> str:
    semantic = {key: value for key, value in fields.items() if key != "intent_id"}
    normalized = (BracketIntent.model_construct(**semantic).model_dump(mode="json")
                  if isinstance(semantic.get("created_at"), datetime) else semantic)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_bracket_intent(
    candidate: TradeCandidate,
    account_id: str,
    decision_id: str,
    reconciliation: ReconciliationResult,
    created_at: datetime,
    ratio_numerator: int = 3,
    ratio_denominator: int = 2,
    trading_config_revision: str | None = None,
    strategy_config_revision: str | None = None,
) -> BracketIntent:
    if candidate.status != "accepted" or not candidate.quantity or candidate.quantity > 2:
        raise ValueError("candidate must be accepted with quantity 1 or 2")
    if reconciliation.status != "MATCH" or reconciliation.blocking or not reconciliation.fingerprint:
        raise ValueError("reconciliation must be a non-blocking MATCH")
    if candidate.session_id != reconciliation.session_id or reconciliation.account_id != account_id:
        raise ValueError("session/account reconciliation mismatch")
    if trading_config_revision is not None and candidate.trading_config_revision != trading_config_revision:
        raise ValueError("trading config revision mismatch")
    if strategy_config_revision is not None and candidate.strategy_config_revision != strategy_config_revision:
        raise ValueError("strategy config revision mismatch")
    if (ratio_numerator, ratio_denominator) != (3, 2):
        raise ValueError("only the 3/2 ratio is supported")
    values = {
        "schema_version": BRACKET_SCHEMA_VERSION, "decision_id": decision_id, "account_id": account_id,
        "session_id": candidate.session_id, "strategy": candidate.strategy, "direction": candidate.direction,
        "quantity": candidate.quantity, "entry_ticks": candidate.entry_ticks, "stop_ticks": candidate.stop_ticks,
        "target_ticks": _target(candidate.entry_ticks, candidate.stop_ticks, candidate.direction, 3, 2),
        "target_ratio_numerator": 3, "target_ratio_denominator": 2, "entry_order_type": "LIMIT", "stop_order_type": "STOP",
        "target_order_type": "LIMIT", "time_in_force": "DAY", "oco_required": True, "reduce_only_children": True,
        "scale_in_allowed": False, "reentry_allowed": False, "stop_widening_allowed": False,
        "trading_config_revision": candidate.trading_config_revision, "strategy_config_revision": candidate.strategy_config_revision,
        "reconciliation_fingerprint": reconciliation.fingerprint, "created_at": created_at, "mode": "SIMULATION_ONLY",
    }
    return BracketIntent(intent_id=_intent_id(values), **values)


def validate_stop_change(intent: BracketIntent, new_stop_ticks: int) -> BracketIntent:
    allowed = new_stop_ticks >= intent.stop_ticks if intent.direction == "long" else new_stop_ticks <= intent.stop_ticks
    remains_valid = new_stop_ticks < intent.entry_ticks if intent.direction == "long" else new_stop_ticks > intent.entry_ticks
    if not allowed or not remains_valid:
        raise ValueError("stop change must be equal or tighter and remain beyond entry")
    values = intent.model_dump(mode="python")
    values["stop_ticks"] = new_stop_ticks
    values["target_ticks"] = _target(intent.entry_ticks, new_stop_ticks, intent.direction, 3, 2)
    values["intent_id"] = _intent_id(values)
    return BracketIntent.model_validate(values)

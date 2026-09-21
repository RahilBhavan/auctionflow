from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

DECISION_SCHEMA_VERSION = "1"
class Decision(StrEnum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    SETUP_ARMED = "SETUP_ARMED"
    AUTHORIZE_1_MES = "AUTHORIZE_1_MES"
    AUTHORIZE_2_MES = "AUTHORIZE_2_MES"
    BLOCK_ACCOUNT = "BLOCK_ACCOUNT"
class ObservationMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    aggressive: bool = False
    poor_response: bool = False
    confirmation: bool = False
    phase_count: int = Field(default=0, ge=0)
class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str = Field(min_length=1)
    timestamp: datetime
    symbol: str = Field(pattern=r"^[A-Z0-9]{1,12}$")
    sequence: int | None = Field(default=None, ge=0)
    metadata: ObservationMetadata = ObservationMetadata()
    aggressive: StrictBool = False
    poor_response: StrictBool = False
    confirmation: StrictBool = False
class RawEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str = Field(min_length=1)
    timestamp: datetime
    payload: dict[str, Any]
class TradeEvent(Event):
    kind: Literal["trade"] = "trade"
    price_ticks: int
    quantity: int = Field(gt=0)
    metadata: ObservationMetadata = ObservationMetadata()
class QuoteEvent(Event):
    kind: Literal["quote"] = "quote"
    bid_ticks: int
    ask_ticks: int
    @model_validator(mode="after")
    def valid_market(self):
        if self.ask_ticks < self.bid_ticks: raise ValueError("ask must not be below bid")
        return self
class DepthEvent(Event):
    kind: Literal["depth"] = "depth"
    price_ticks: int
    quantity: int = Field(ge=0)
    side: Literal["bid", "ask"]
class ResetEvent(Event):
    kind: Literal["reset"] = "reset"
class AccountState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    balance_cents: int = Field(ge=0)
    mll_floor_cents: int = Field(ge=0)
    open_position: int = 0
    opening_attempts: int = Field(default=0, ge=0)
    token_consumed: bool = False
    health_known: bool = True
    position_state_known: bool = True
    protective_state_known: bool = True
    @model_validator(mode="after")
    def valid_position(self):
        if not isinstance(self.open_position, int) or isinstance(self.open_position, bool): raise TypeError("open_position must be a signed integer quantity")
        return self
    @property
    def mll_buffer_cents(self): return self.balance_cents - self.mll_floor_cents
    @property
    def authorization_allowed(self): return self.health_known and self.position_state_known and self.protective_state_known and self.open_position == 0 and not self.token_consumed
class StrategyState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    state: str = "dormant"
    direction: Literal["long", "short"] | None = None
    structural_stop_ticks: int | None = None
    mouse: StrategyState | None = None
    breakout: StrategyState | None = None
    progress: int = Field(default=0, ge=0)
class RiskProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entry_ticks: int
    stop_ticks: int
    quantity: int = Field(ge=0)
    risk_budget_cents: int = Field(ge=0)
    stop_risk_cents: int = Field(ge=0)
class TradeCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    candidate_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    direction: Literal["long", "short"]
    entry_ticks: int
    stop_ticks: int
    status: Literal["accepted", "rejected"]
    rejection_reasons: tuple[str, ...] = ()
    quantity: int | None = Field(default=None, ge=0)
    risk_budget_cents: int | None = Field(default=None, ge=0)
    trading_config_revision: str
    strategy_config_revision: str
    @model_validator(mode="after")
    def valid_geometry(self):
        if (self.direction == "long" and self.stop_ticks >= self.entry_ticks) or (self.direction == "short" and self.stop_ticks <= self.entry_ticks): raise ValueError("stop must be beyond entry")
        return self
class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: str = DECISION_SCHEMA_VERSION
    decision: Decision
    reason_codes: tuple[str, ...] = ()
    event_id: str
    timestamp: datetime
    session_id: str
    account_snapshot_hash: str
    config_revision: str
    strategy_config_revision: str
    candidate: TradeCandidate | None = None
    risk: RiskProposal | None = None
class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    record: DecisionRecord
    account: AccountState
    strategy: StrategyState = StrategyState()
    candidate: TradeCandidate | None = None

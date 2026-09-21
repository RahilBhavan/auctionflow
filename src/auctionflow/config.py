from __future__ import annotations

from .strategies import StrategyConfig

MOUSE_TRAP_RESEARCH_DEFAULTS = StrategyConfig(reference_level=0)
ACCEPTANCE_BREAKOUT_RESEARCH_DEFAULTS = StrategyConfig(boundary=0)

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class TradingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    symbol: str = Field(default="MES", pattern=r"^[A-Z0-9]{1,12}$")
    session_timezone: str = "America/New_York"
    session_start: str = "09:35"
    session_end: str = "11:30"
    account_size_cents: int = 5_000_000
    mll_trailing_cushion_cents: int = 200_000
    max_risk_cents: int = 20_000
    risk_buffer_percent: int = Field(default=10, ge=0, le=100)
    mes_tick_value_cents: int = 125
    max_initial_quantity: int = Field(default=2, ge=1, le=4)
    hard_max_quantity: int = Field(default=4, ge=1, le=4)
    commission_cents_per_contract: int = Field(default=0, ge=0)
    slippage_cents_per_contract: int = Field(default=0, ge=0)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @property
    def revision(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

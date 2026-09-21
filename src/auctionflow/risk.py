from __future__ import annotations

from .config import TradingConfig
from .domain import RiskProposal


def mes_stop_risk_cents(entry_ticks: int, stop_ticks: int, quantity: int, config: TradingConfig | None = None) -> int:
    value = config.mes_tick_value_cents if config else 125
    return abs(entry_ticks - stop_ticks) * value * quantity


def risk_budget_cents(buffer_cents: int, remaining_budget_cents: int | None = None, config: TradingConfig | None = None) -> int:
    config = config or TradingConfig()
    limits = [config.max_risk_cents, buffer_cents * config.risk_buffer_percent // 100]
    if remaining_budget_cents is not None:
        limits.append(remaining_budget_cents)
    return max(0, min(limits))


def propose_risk(entry_ticks: int, stop_ticks: int, buffer_cents: int, remaining_budget_cents: int | None = None, config: TradingConfig | None = None) -> RiskProposal:
    config = config or TradingConfig()
    budget = risk_budget_cents(buffer_cents, remaining_budget_cents, config)
    unit = mes_stop_risk_cents(entry_ticks, stop_ticks, 1, config) + config.commission_cents_per_contract + config.slippage_cents_per_contract
    quantity = min(config.max_initial_quantity, config.hard_max_quantity, budget // unit) if unit else 0
    return RiskProposal(entry_ticks=entry_ticks, stop_ticks=stop_ticks, quantity=quantity, risk_budget_cents=budget, stop_risk_cents=unit * quantity)

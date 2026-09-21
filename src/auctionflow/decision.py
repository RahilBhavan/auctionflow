from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .config import TradingConfig
from .domain import (
    AccountState,
    Decision,
    DecisionRecord,
    DepthEvent,
    EvaluationResult,
    Event,
    QuoteEvent,
    RawEnvelope,
    ResetEvent,
    StrategyState,
    TradeCandidate,
    TradeEvent,
)
from .reasons import (
    ACCOUNT_UNHEALTHY,
    AUTHORIZATION,
    INSUFFICIENT_RISK,
    INVALID_STOP,
    OPEN_POSITION,
    OUTSIDE_SESSION,
    SETUP,
    STALE_DATA,
    TOKEN_CONSUMED,
)
from .risk import propose_risk
from .session import is_session_open, session_id
from .strategies import (
    RESEARCH_DEFAULT_STRATEGY_CONFIG,
    StrategyConfig,
    acceptance_breakout,
    evaluate_strategies,
    mouse_trap,
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
def _record(decision, event, config, account, strategy_config, reasons, risk=None, candidate=None):
    reasons = (reasons,) if isinstance(reasons, str) else tuple(reasons)
    return DecisionRecord(decision=decision, reason_codes=reasons, event_id=event.event_id, timestamp=event.timestamp,
                          session_id=session_id(event.timestamp, config), account_snapshot_hash=_hash(account.model_dump(mode="json")),
                          config_revision=config.revision, strategy_config_revision=strategy_config.revision, risk=risk, candidate=candidate)
def _result(decision, event, config, account, strategy, sc, reasons, risk=None, candidate=None, updated=None):
    record = _record(decision, event, config, account, sc, reasons, risk, candidate)
    return EvaluationResult(record=record, account=updated or account, strategy=strategy, candidate=candidate)
def evaluate(event: Event | Mapping[str, Any], account: AccountState, config: TradingConfig | None = None, strategy: StrategyState | None = None, strategy_config: StrategyConfig | None = None) -> EvaluationResult:
    config, strategy, strategy_config = config or TradingConfig(), strategy or StrategyState(), strategy_config or RESEARCH_DEFAULT_STRATEGY_CONFIG
    event = _event(event)
    if event.symbol != config.symbol: return _result(Decision.BLOCK_ACCOUNT,event,config,account,strategy,strategy_config,(STALE_DATA,))
    if session_id(event.timestamp, config) != account.session_id: return _result(Decision.BLOCK_ACCOUNT,event,config,account,strategy,strategy_config,(STALE_DATA,))
    if account.mll_buffer_cents <= 0 or not (account.health_known and account.position_state_known and account.protective_state_known): return _result(Decision.BLOCK_ACCOUNT,event,config,account,strategy,strategy_config,(ACCOUNT_UNHEALTHY,))
    if not is_session_open(event.timestamp, config): return _result(Decision.NO_TRADE,event,config,account,strategy,strategy_config,(OUTSIDE_SESSION,))
    if account.open_position: return _result(Decision.BLOCK_ACCOUNT,event,config,account,strategy,strategy_config,(OPEN_POSITION,))
    if account.token_consumed: return _result(Decision.BLOCK_ACCOUNT,event,config,account,strategy,strategy_config,(TOKEN_CONSUMED,))
    mouse = mouse_trap([event], strategy_config, strategy.mouse if strategy.mouse is not None else strategy)
    breakout = acceptance_breakout([event], strategy_config, strategy.breakout if strategy.breakout is not None else StrategyState(state="inside_auction"))
    decision, reason = evaluate_strategies(mouse, breakout)
    combined = StrategyState(state=mouse.state if mouse.state != "dormant" else breakout.state, direction=mouse.direction or breakout.direction, structural_stop_ticks=mouse.structural_stop_ticks or breakout.structural_stop_ticks, mouse=mouse, breakout=breakout)
    if decision != Decision.SETUP_ARMED: return _result(decision,event,config,account,combined,strategy_config,(SETUP if reason == "SETUP" else reason))
    chosen = mouse if mouse.state == "armed" else breakout
    entry = getattr(event, "price_ticks", chosen.structural_stop_ticks)
    candidate_base = {"candidate_id": event.event_id, "event_id": event.event_id, "session_id": account.session_id, "strategy": "mouse_trap" if chosen is mouse else "acceptance_breakout", "direction": chosen.direction, "entry_ticks": entry, "stop_ticks": chosen.structural_stop_ticks, "trading_config_revision": config.revision, "strategy_config_revision": strategy_config.revision}
    if chosen.structural_stop_ticks is None or chosen.direction is None or ((chosen.direction == "long" and chosen.structural_stop_ticks >= entry) or (chosen.direction == "short" and chosen.structural_stop_ticks <= entry)):
        return _result(Decision.NO_TRADE, event, config, account, combined, strategy_config, (INVALID_STOP,))
    risk = propose_risk(entry, chosen.structural_stop_ticks, account.mll_buffer_cents, config=config)
    status = "accepted" if risk.quantity >= 1 else "rejected"
    candidate = TradeCandidate(**candidate_base,status=status,rejection_reasons=() if status == "accepted" else (INSUFFICIENT_RISK,),quantity=risk.quantity,risk_budget_cents=risk.risk_budget_cents)
    if status == "rejected": return _result(Decision.NO_TRADE,event,config,account,combined,strategy_config,(INSUFFICIENT_RISK,),risk,candidate)
    updated = account.model_copy(update={"token_consumed": True, "opening_attempts": account.opening_attempts + 1})
    decision = Decision.AUTHORIZE_2_MES if risk.quantity == 2 else Decision.AUTHORIZE_1_MES
    return _result(decision,event,config,account,combined,strategy_config,(AUTHORIZATION,),risk,candidate,updated)
def _event(value):
    if isinstance(value, Event): return value
    if isinstance(value, RawEnvelope): value = value.model_dump(mode="python")
    if isinstance(value, Mapping) and "payload" in value:
        envelope = RawEnvelope.model_validate(value); payload = dict(envelope.payload)
        payload_timestamp = payload.get("timestamp")
        if isinstance(payload_timestamp, str):
            from datetime import datetime
            payload_timestamp = datetime.fromisoformat(payload_timestamp)
        if payload.get("event_id") != envelope.event_id or payload_timestamp != envelope.timestamp: raise ValueError("envelope identity mismatch")
        value = payload
    kind = value.get("kind", "trade"); types = {"trade": TradeEvent, "quote": QuoteEvent, "depth": DepthEvent, "reset": ResetEvent}
    if kind not in types: raise ValueError(f"unsupported event kind: {kind}")
    return types[kind].model_validate(value)
evaluate_decision = evaluate

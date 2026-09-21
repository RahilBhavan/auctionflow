"""Pure, deterministic research-only strategy state machines."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .domain import Decision, StrategyState, TradeEvent


@dataclass(frozen=True)
class StrategyConfig:
    reference_level: int | None = None
    sweep_min_ticks: int = 1
    sweep_max_ticks: int = 8
    reclaim_window: int = 3
    confirmations: int = 2
    boundary: int | None = None
    displacement_ticks: int = 2
    acceptance_events: int = 2
    retest_tolerance_ticks: int = 1
    continuation_ticks: int = 1
    stop_offset_ticks: int = 1

    def canonical_json(self) -> str:
        import json
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))

    @property
    def revision(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


RESEARCH_DEFAULT_STRATEGY_CONFIG = StrategyConfig(reference_level=100, boundary=100,
                                                   acceptance_events=2, continuation_ticks=2)


@dataclass(frozen=True)
class Observation:
    price_ticks: int
    event_id: str | None = None
    timestamp: Any = None
    symbol: str | None = None
    sequence: int | None = None
    quantity: int | None = None
    kind: str | None = None
    aggressive: bool = False
    poor_response: bool = False
    confirmation: bool = False
    reset: bool = False


def _observation(value: Observation | Mapping[str, Any] | TradeEvent) -> Observation:
    if isinstance(value, Observation):
        return value
    if isinstance(value, Mapping):
        return Observation(
            price_ticks=int(value.get("price_ticks", value.get("bid_ticks", value.get("ask_ticks", 0)))),
            aggressive=bool(value.get("aggressive", value.get("aggressive_flow", False))),
            poor_response=bool(value.get("poor_response", value.get("impact_residual", False))),
            confirmation=bool(value.get("confirmation", False)),
            reset=value.get("kind") == "reset" or bool(value.get("reset", False)),
        )
    metadata = getattr(value, "metadata", None)
    return Observation(price_ticks=getattr(value, "price_ticks", getattr(value, "bid_ticks", getattr(value, "ask_ticks", 0))),
                       kind=getattr(value, "kind", None), reset=getattr(value, "kind", None) == "reset",
                       aggressive=getattr(value, "aggressive", getattr(metadata, "aggressive", False)),
                       poor_response=getattr(value, "poor_response", getattr(metadata, "poor_response", False)),
                       confirmation=getattr(value, "confirmation", getattr(metadata, "confirmation", False)))


def _state(state: StrategyState, name: str, direction: str | None = None, stop: int | None = None) -> StrategyState:
    return state.model_copy(update={"state": name, "direction": direction, "structural_stop_ticks": stop})


def mouse_trap(observations: Iterable[Observation | Mapping[str, Any] | TradeEvent], config: StrategyConfig, state: StrategyState | None = None) -> StrategyState:
    """Advance Mouse Trap without clocks, I/O, or future-event lookahead."""
    state = state or StrategyState()
    level = config.reference_level
    if level is None:
        return _state(state, "invalidated")
    count = state.progress
    extreme = state.structural_stop_ticks
    confirmations = state.progress if state.state == "confirmation_pending" else 0
    for raw in observations:
        obs = _observation(raw)
        if obs.reset:
            return StrategyState()
        p = obs.price_ticks
        if state.state in ("armed", "invalidated"):
            continue
        if state.state == "dormant" and abs(p - level) <= config.sweep_max_ticks:
            state = _state(state, "level_approach")
        if state.state == "level_approach":
            distance = abs(p - level)
            if config.sweep_min_ticks <= distance <= config.sweep_max_ticks and obs.aggressive and obs.poor_response:
                direction = "short" if p > level else "long"
                extreme = p
                state = _state(state, "swept", direction)
        elif state.state == "swept":
            count += 1
            extreme = p if extreme is None else (max(extreme, p) if state.direction == "short" else min(extreme, p))
            if count > config.reclaim_window:
                return _state(state, "invalidated", state.direction, extreme + config.stop_offset_ticks if state.direction == "short" else extreme - config.stop_offset_ticks)
            if (state.direction == "short" and p <= level) or (state.direction == "long" and p >= level):
                state = _state(state, "reclaim_pending", state.direction, extreme + config.stop_offset_ticks if state.direction == "short" else extreme - config.stop_offset_ticks)
                count = 0
        elif state.state == "reclaim_pending":
            count += 1
            if count > config.reclaim_window:
                return _state(state, "invalidated")
            if (state.direction == "short" and p <= level) or (state.direction == "long" and p >= level):
                state = _state(state, "confirmation_pending", state.direction, state.structural_stop_ticks)
        elif state.state == "confirmation_pending":
            count += 1
            confirmations += int(obs.confirmation or (state.direction == "short" and p < level) or (state.direction == "long" and p > level))
            if count > config.reclaim_window:
                return _state(state, "invalidated")
            if confirmations >= config.confirmations:
                return _state(state, "armed", state.direction, state.structural_stop_ticks)
    return state.model_copy(update={"progress": count})


def acceptance_breakout(observations: Iterable[Observation | Mapping[str, Any] | TradeEvent], config: StrategyConfig, state: StrategyState | None = None) -> StrategyState:
    state = state or StrategyState(state="inside_auction")
    if config.boundary is None:
        return _state(state, "invalidated")
    count = state.progress
    for raw in observations:
        obs = _observation(raw)
        if obs.reset:
            return StrategyState(state="inside_auction")
        p = obs.price_ticks
        if state.state == "inside_auction" and abs(p - config.boundary) >= config.displacement_ticks:
            direction = "long" if p > config.boundary else "short"
            state = _state(state, "broken", direction, config.boundary - config.stop_offset_ticks if direction == "long" else config.boundary + config.stop_offset_ticks)
        elif state.state == "broken":
            count += 1
            if (state.direction == "long" and p < config.boundary) or (state.direction == "short" and p > config.boundary):
                return _state(state, "invalidated", state.direction, state.structural_stop_ticks)
            if count > config.reclaim_window:
                return _state(state, "invalidated", state.direction, state.structural_stop_ticks)
            if count >= config.acceptance_events:
                state = _state(state, "hold_pending", state.direction, state.structural_stop_ticks)
                count = 0
        elif state.state == "hold_pending":
            count += 1
            if count > config.reclaim_window:
                return _state(state, "invalidated", state.direction, state.structural_stop_ticks)
            if abs(p - config.boundary) <= config.retest_tolerance_ticks:
                state = _state(state, "retest_pending", state.direction, state.structural_stop_ticks)
                count = 0
        elif state.state == "retest_pending":
            count += 1
            if count > config.reclaim_window:
                return _state(state, "invalidated", state.direction, state.structural_stop_ticks)
            if (state.direction == "long" and p >= config.boundary + config.continuation_ticks) or (state.direction == "short" and p <= config.boundary - config.continuation_ticks):
                return _state(state, "continuation_confirmed", state.direction, state.structural_stop_ticks)
    return state.model_copy(update={"progress": count})


mouse_trap_state_machine = mouse_trap
acceptance_breakout_state_machine = acceptance_breakout


def evaluate_strategies(mouse: StrategyState, breakout: StrategyState) -> tuple[Decision, str]:
    armed = [s for s in (mouse, breakout) if s.state in ("armed", "continuation_confirmed")]
    if len(armed) > 1 or (len(armed) == 2 and armed[0].direction != armed[1].direction):
        return Decision.NO_TRADE, "STRATEGY_CONFLICT"
    if armed:
        return Decision.SETUP_ARMED, "AUTHORIZATION_CANDIDATE"
    if any(s.state not in ("dormant", "inside_auction", "invalidated") for s in (mouse, breakout)):
        return Decision.WATCH, "SETUP"
    return Decision.NO_TRADE, "NO_SETUP"

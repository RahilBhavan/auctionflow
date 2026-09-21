import json
from pathlib import Path

from auctionflow.domain import Decision, StrategyState
from auctionflow.strategies import Observation, StrategyConfig, evaluate_strategies, mouse_trap

FIXTURE = Path(__file__).parent / "fixtures/mouse_trap.jsonl"
CONFIG = StrategyConfig(reference_level=100, sweep_min_ticks=2, sweep_max_ticks=4, confirmations=2)


def fixture_observations():
    return [Observation(price_ticks=v["price_ticks"], aggressive=v["price_ticks"] == 103, poor_response=v["price_ticks"] == 103) for line in FIXTURE.read_text().splitlines() for v in [json.loads(line)]]


def test_irrelevant_activity_is_no_trade():
    result = mouse_trap([Observation(90), Observation(95)], CONFIG)
    assert result.state == "dormant"
    assert evaluate_strategies(result, StrategyState(state="inside_auction")) == (Decision.NO_TRADE, "NO_SETUP")


def test_approach_is_watch():
    result = mouse_trap([Observation(98)], CONFIG)
    assert result.state == "level_approach"
    assert evaluate_strategies(result, StrategyState(state="inside_auction")) == (Decision.WATCH, "SETUP")


def test_sweep_reclaim_arms_with_structural_stop():
    result = mouse_trap(fixture_observations(), CONFIG)
    assert result.state == "confirmation_pending"
    assert result.direction == "short"
    assert result.structural_stop_ticks == 104
    assert evaluate_strategies(result, StrategyState(state="inside_auction")) == (Decision.WATCH, "SETUP")


def test_missing_reclaim_times_out():
    result = mouse_trap([Observation(103, aggressive=True, poor_response=True), Observation(104), Observation(105), Observation(106), Observation(107)], CONFIG)
    assert result.state == "invalidated"


def test_reset_invalidates_current_sequence():
    result = mouse_trap([Observation(98), Observation(0, reset=True)], CONFIG)
    assert result == StrategyState()

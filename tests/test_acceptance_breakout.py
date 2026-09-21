import json
from pathlib import Path

from auctionflow.domain import Decision, StrategyState
from auctionflow.strategies import (
    Observation,
    StrategyConfig,
    acceptance_breakout,
    evaluate_strategies,
)

FIXTURE = Path(__file__).parent / "fixtures/acceptance_breakout.jsonl"
CONFIG = StrategyConfig(boundary=100, displacement_ticks=2, acceptance_events=2, continuation_ticks=2)


def fixture_observations():
    return [Observation(price_ticks=json.loads(line)["price_ticks"]) for line in FIXTURE.read_text().splitlines()]


def test_breakout_fixture_confirms_with_structural_stop():
    result = acceptance_breakout(fixture_observations(), CONFIG)
    assert result.state == "continuation_confirmed"
    assert result.direction == "long"
    assert result.structural_stop_ticks == 99
    assert evaluate_strategies(StrategyState(), result) == (Decision.SETUP_ARMED, "AUTHORIZATION_CANDIDATE")


def test_break_is_watch_before_acceptance():
    result = acceptance_breakout([Observation(103)], CONFIG)
    assert result.state == "broken"
    assert evaluate_strategies(StrategyState(), result) == (Decision.WATCH, "SETUP")


def test_breakout_timeout_invalidates():
    result = acceptance_breakout([Observation(103), Observation(104), Observation(105), Observation(106), Observation(107), Observation(108), Observation(109), Observation(110)], CONFIG)
    assert result.state == "invalidated"


def test_failed_retest_invalidates():
    result = acceptance_breakout([Observation(103), Observation(104), Observation(105), Observation(101), Observation(100), Observation(99), Observation(98), Observation(97)], CONFIG)
    assert result.state == "invalidated"


def test_conflicting_armed_strategies_fail_closed():
    mouse = StrategyState(state="armed", direction="short", structural_stop_ticks=104)
    breakout = StrategyState(state="continuation_confirmed", direction="long", structural_stop_ticks=99)
    assert evaluate_strategies(mouse, breakout) == (Decision.NO_TRADE, "STRATEGY_CONFLICT")

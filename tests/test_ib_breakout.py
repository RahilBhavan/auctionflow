from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from auctionflow.bars import OHLCVBar
from auctionflow.config import TradingConfig
from auctionflow.databento import SessionReferences
from auctionflow.ib_breakout import (
    BarStrategyConfig,
    FailedIBBreakoutConfig,
    LiquiditySweepConfig,
    evaluate_failed_ib_breakout,
    evaluate_ib_session,
    evaluate_liquidity_sweep_reclaim,
)


def make_bars(post=()):
    start = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    values = list(post) + [(100, 101, 99, 100)] * (60 - len(post))
    out = [OHLCVBar(event_id=str(i), source="test", timestamp=start + timedelta(minutes=i),
                    sequence=i, open_ticks=100, high_ticks=101, low_ticks=99, close_ticks=100,
                    volume=1) for i in range(60)]
    for i, (o, h, l, c) in enumerate(values, 60):
        out.append(OHLCVBar(event_id=str(i), source="test", timestamp=start + timedelta(minutes=i),
                            sequence=i, open_ticks=o, high_ticks=h, low_ticks=l, close_ticks=c,
                            volume=1))
    return out


def valid_long_post():
    return [(100, 103, 100, 102), (102, 103, 101, 101),
            (102, 103, 101, 102), (102, 104, 102, 103), (103, 109, 102, 104)]


def test_exact_crossing_hour_is_contiguous():
    assert evaluate_ib_session(make_bars()).status == "NO_TRADE"


def test_valid_long_fsm_enters_on_next_bar_open():
    result = evaluate_ib_session(make_bars(valid_long_post()))
    assert (result.status, result.direction, result.entry_ticks) == ("ACCEPTED", "long", 103)


def test_entry_bar_can_hit_target():
    result = evaluate_ib_session(make_bars(valid_long_post()))
    assert (result.label, result.exit_ticks, result.gross_cents) == ("TARGET_FIRST", 109, 1500)


def test_long_stop_exit_uses_stop_ticks():
    bars = make_bars(valid_long_post())
    bars[64] = bars[64].model_copy(update={"low_ticks": 99, "high_ticks": 104})
    result = evaluate_ib_session(bars)
    assert (result.label, result.exit_ticks) == ("STOP_FIRST", 99)


def test_short_stop_exit_uses_stop_ticks():
    post = [(100, 100, 97, 98), (98, 99, 97, 99), (98, 99, 97, 98),
            (98, 98, 96, 97), (97, 98, 96, 97)] + [(97, 98, 96, 97)] * 55
    bars = make_bars(post)
    bars[64] = bars[64].model_copy(update={"low_ticks": 97, "high_ticks": 101})
    result = evaluate_ib_session(bars)
    assert (result.label, result.exit_ticks) == ("STOP_FIRST", 101)


def test_acceptance_allows_boundary_close():
    post = [(100, 103, 100, 102), (102, 103, 101, 101), *valid_long_post()[2:]]
    assert evaluate_ib_session(make_bars(post)).status == "ACCEPTED"


def test_acceptance_requires_breakout_displacement():
    post = [(100, 102, 100, 101), (101, 103, 100, 101)]
    assert evaluate_ib_session(make_bars(post)).status == "NO_TRADE"


def test_acceptance_boundary_invalidation_prevents_reentry():
    post = [(100, 103, 100, 102), (102, 102, 99, 100), *valid_long_post()]
    assert evaluate_ib_session(make_bars(post)).status == "NO_TRADE"


def test_missing_bar_invalid():
    bars = make_bars()[::2]
    assert evaluate_ib_session(bars).status == "DATA_INVALID"


def test_duplicate_timestamp_invalid():
    bars = make_bars(); bars[10] = bars[9].model_copy(update={"sequence": 10})
    assert evaluate_ib_session(bars).status == "DATA_INVALID"


def test_out_of_order_invalid():
    bars = make_bars(); bars[10], bars[11] = bars[11], bars[10]
    assert evaluate_ib_session(bars).status == "DATA_INVALID"


def test_sequence_gap_invalid():
    bars = make_bars(); bars[20] = bars[20].model_copy(update={"sequence": 99})
    assert evaluate_ib_session(bars).status == "DATA_INVALID"


def test_mixed_contract_invalid():
    bars = make_bars(); bars[1] = bars[1].model_copy(update={"contract_id": "other"})
    bars[2] = bars[2].model_copy(update={"contract_id": "main"})
    assert evaluate_ib_session(bars).status == "DATA_INVALID"


@pytest.mark.parametrize("field,value", [("acceptance_bars", 0), ("acceptance_window", 0),
                                           ("displacement_ticks", 0), ("retest_window", 0)])
def test_strategy_positive_validation(field, value):
    with pytest.raises(ValidationError):
        BarStrategyConfig(**{field: value})


def test_invalid_target_geometry():
    with pytest.raises(ValidationError):
        BarStrategyConfig(target_numerator=1, target_denominator=2)


def test_config_revision_is_deterministic():
    assert BarStrategyConfig().revision == BarStrategyConfig().revision
    assert BarStrategyConfig().revision != BarStrategyConfig(stop_offset_ticks=2).revision


def test_frozen_result():
    result = evaluate_ib_session(make_bars())
    with pytest.raises(ValidationError):
        result.status = "ACCEPTED"


def test_result_has_no_legacy_fields():
    assert not {"aggressive", "poor_response", "confirmation"} & set(type(evaluate_ib_session(make_bars())).model_fields)


def test_frozen_bar_rejects_bad_ohlc():
    with pytest.raises(ValidationError):
        OHLCVBar(event_id="x", source="x", timestamp=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
                 sequence=0, open_ticks=2, high_ticks=1, low_ticks=0, close_ticks=1, volume=1)


def test_empty_input_invalid():
    assert evaluate_ib_session([]).status == "DATA_INVALID"


def test_symbol_is_frozen_to_mes():
    with pytest.raises(ValidationError):
        OHLCVBar(event_id="x", source="x", timestamp=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
                 symbol="NQ", sequence=0, open_ticks=1, high_ticks=1, low_ticks=1, close_ticks=1, volume=1)


def test_trading_revision_changes_with_costs():
    assert TradingConfig().revision != TradingConfig(commission_cents_per_contract=1).revision


def test_incomplete_evaluation_span_invalid():
    assert evaluate_ib_session(make_bars()[:-1]).reason == "EVALUATION_SPAN_REQUIRES_120_CONTIGUOUS_BARS"


def test_mixed_none_and_concrete_contract_invalid():
    bars = make_bars(); bars[0] = bars[0].model_copy(update={"contract_id": "MES-1"})
    assert evaluate_ib_session(bars).reason == "SESSION_CONTRACT_IDENTITY_INVALID"


def test_overnight_gap_before_evaluation_span_is_accepted():
    overnight = OHLCVBar(event_id="overnight", source="test", timestamp=datetime(2024, 1, 2, 13, tzinfo=UTC),
                         sequence=0, open_ticks=100, high_ticks=101, low_ticks=99, close_ticks=100, volume=1)
    bars = [overnight] + [bar.model_copy(update={"sequence": bar.sequence + 1}) for bar in make_bars()]
    result = evaluate_ib_session(bars)
    assert result.status == "NO_TRADE"
    assert result.mode == "BAR_OHLC_RESEARCH_ONLY"


def test_ambiguous_bar_is_exit_evidence_without_pnl():
    ambiguous = make_bars(valid_long_post())
    ambiguous[64] = ambiguous[64].model_copy(update={"low_ticks": 99, "high_ticks": 109})
    result = evaluate_ib_session(ambiguous)
    assert result.label == "AMBIGUOUS"
    assert (result.exit_id, result.exit_ticks) == ("64", 104)
    assert (result.gross_cents, result.net_cents) == (0, 0)


def test_target_rounding_is_integer_outward():
    config = BarStrategyConfig(target_numerator=4, target_denominator=3, stop_offset_ticks=2)
    result = evaluate_ib_session(make_bars(valid_long_post()), strategy_config=config)
    assert result.target_ticks == 110


@pytest.mark.parametrize("kwargs", [{"ib_start": "09:31"}, {"ib_end": "10:31"},
                                     {"ib_start": "09:30", "ib_end": "11:00"},
                                     {"ib_end": "09:30"}, {"entry_end": "10:30"}])
def test_strategy_time_order_and_duration_validation(kwargs):
    with pytest.raises(ValidationError):
        BarStrategyConfig(**kwargs)


def test_session_exit_records_long_pnl_and_costs():
    post = valid_long_post()[:4] + [(103, 104, 102, 103)] * 56
    result = evaluate_ib_session(make_bars(post), trading_config=TradingConfig(commission_cents_per_contract=10))
    assert (result.label, result.exit_id, result.exit_ticks) == ("SESSION_EXIT", "119", 103)
    assert (result.gross_cents, result.net_cents) == (0, -20)


def test_session_exit_records_short_pnl_and_costs():
    post = [(100, 100, 97, 98), (98, 99, 97, 99), (98, 99, 97, 98),
            (98, 98, 96, 97), (97, 98, 96, 97)] + [(97, 98, 96, 97)] * 55
    result = evaluate_ib_session(make_bars(post), trading_config=TradingConfig(commission_cents_per_contract=10))
    assert result.direction == "short"
    assert (result.label, result.exit_id, result.exit_ticks) == ("SESSION_EXIT", "119", 97)
    assert (result.gross_cents, result.net_cents) == (0, -20)


def test_risk_budget_uses_minimum_cap_and_rejects_zero():
    limited = evaluate_ib_session(make_bars(valid_long_post()), remaining_daily_budget_cents=700)
    assert (limited.risk_budget_cents, limited.quantity) == (700, 1)
    rejected = evaluate_ib_session(make_bars(valid_long_post()), remaining_setup_cap_cents=0)
    assert rejected.reason == "INSUFFICIENT_RISK"
    assert rejected.quantity == 0


def test_continuous_series_has_data_warning():
    result = evaluate_ib_session(make_bars())
    assert result.data_warning == "CONTINUOUS_SERIES_NO_CONTRACT_ROLL_ADJUSTMENT"


def test_concrete_contract_has_no_data_warning():
    bars = [bar.model_copy(update={"contract_id": "MES-1"}) for bar in make_bars()]
    assert evaluate_ib_session(bars).data_warning is None


def test_liquidity_sweep_default_confirmation_accepts_immediately():
    bars = make_bars([(100, 103, 100, 101), (101, 103, 100, 102)])
    references = SessionReferences(102, 98, None, None)
    result = evaluate_liquidity_sweep_reclaim(bars, references)
    assert (result.status, result.direction) == ("ACCEPTED", "short")


def test_liquidity_sweep_confirmation_requires_all_bars():
    bars = make_bars([(100, 103, 100, 102), (102, 103, 100, 101),
                      (101, 103, 100, 103), (102, 102, 101, 102)])
    references = SessionReferences(102, 98, None, None)
    result = evaluate_liquidity_sweep_reclaim(
        bars, references, strategy_config=LiquiditySweepConfig(confirmation_bars=2)
    )
    assert result.status == "NO_TRADE"


def test_failed_ib_earliest_breakout_blocks_later_reclaim():
    post = [(100, 103, 100, 102), (102, 103, 101, 102),
            (101, 104, 100, 103), (103, 104, 102, 104),
            (104, 105, 100, 101)]
    result = evaluate_failed_ib_breakout(
        make_bars(post), strategy_config=FailedIBBreakoutConfig(reclaim_window=1)
    )
    assert result.status == "NO_TRADE"
    assert result.reason == "NO_QUALIFIED_RECLAIM"

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auctionflow.domain import AccountState
from auctionflow.rules import (
    OFFICIAL_TOPSTEP_50K_MLL,
    current_mll_buffer_cents,
    mll_breached,
    next_mll_floor_cents,
    update_account_eod,
)


@given(
    previous=st.integers(min_value=0, max_value=5_000_000),
    eod=st.integers(min_value=0, max_value=10_000_000),
)
def test_mll_floor_never_decreases_or_exceeds_account_cap(previous, eod):
    floor = next_mll_floor_cents(previous, eod)
    assert floor >= previous
    assert floor <= 5_000_000
    assert floor >= min(previous, max(0, eod - 200_000))


def test_official_50k_snapshot_examples_and_breach_boundary():
    assert next_mll_floor_cents(4_800_000, 5_050_000) == 4_850_000
    assert next_mll_floor_cents(4_850_000, 4_800_000) == 4_850_000
    assert next_mll_floor_cents(4_990_000, 5_200_000) == 5_000_000
    assert mll_breached(4_850_000, 4_850_000)
    assert not mll_breached(4_850_001, 4_850_000)


def test_rule_snapshot_revision_is_deterministic_and_change_sensitive():
    assert OFFICIAL_TOPSTEP_50K_MLL.revision == OFFICIAL_TOPSTEP_50K_MLL.revision
    changed = replace(OFFICIAL_TOPSTEP_50K_MLL, trailing_cushion_cents=200_001)
    assert changed.revision != OFFICIAL_TOPSTEP_50K_MLL.revision


@pytest.mark.parametrize("value", [-1, True, 1.0, "1"])
def test_rule_inputs_reject_invalid_nonnegative_integers(value):
    with pytest.raises(ValueError):
        next_mll_floor_cents(value, 4_800_000)
    with pytest.raises(ValueError):
        next_mll_floor_cents(4_800_000, value)
    with pytest.raises(ValueError):
        mll_breached(value, 4_800_000)
    with pytest.raises(ValueError):
        mll_breached(4_800_000, value)
    with pytest.raises(ValueError):
        current_mll_buffer_cents(value, 4_800_000)
    with pytest.raises(ValueError):
        current_mll_buffer_cents(4_800_000, value)


def test_current_mll_buffer_can_be_negative():
    assert current_mll_buffer_cents(4_700_000, 4_800_000) == -100_000


@pytest.mark.parametrize("field", ["account_size_cents", "trailing_cushion_cents", "lock_floor_cents"])
@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_rule_snapshot_amounts_reject_nonnegative_nonintegers(field, value):
    with pytest.raises(ValueError):
        replace(OFFICIAL_TOPSTEP_50K_MLL, **{field: value})


def test_mll_rejects_invalid_states():
    with pytest.raises(ValueError):
        next_mll_floor_cents(5_000_001, 4_800_000)


def test_mll_floor_update_returns_immutable_next_account():
    account = AccountState(account_id="a", session_id="2025-01-02", balance_cents=5_000_000, mll_floor_cents=4_700_000)
    updated = update_account_eod(account, 4_950_000)
    assert updated is not account
    assert updated.balance_cents == 4_950_000
    assert updated.mll_floor_cents == 4_750_000
    assert account.balance_cents == 5_000_000


def test_config_revision_is_stable_and_changes_with_values():
    from auctionflow.config import TradingConfig

    assert TradingConfig().revision == TradingConfig().revision
    assert TradingConfig(symbol="MES").revision != TradingConfig(symbol="MNQ").revision

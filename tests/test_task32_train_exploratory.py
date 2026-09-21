import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, "scripts")

from task32_train_exploratory import (
    DriveConfig,
    _trade,
    expanding_prior_width_median,
    opening_range_drive,
    partition_groups,
)

from auctionflow.bars import OHLCVBar
from auctionflow.config import TradingConfig


def bar(i, op, hi, lo, cl):
    return OHLCVBar(event_id=f"e{i}", source="test", timestamp=datetime(2024, 1, 2, 14, 30 + i, tzinfo=UTC), open_ticks=op, high_ticks=hi, low_ticks=lo, close_ticks=cl, volume=1, sequence=i)

def test_trade_enters_next_bar_and_not_signal_bar():
    tc = TradingConfig(commission_cents_per_contract=122, slippage_cents_per_contract=125)
    rows = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 100, 100), bar(2, 101, 103, 101, 102)]
    result = _trade(rows[0].et_date, rows, "long", 0, 99, 1.0, tc, DriveConfig(5, .5, 1.0))
    assert result["label"] == "TARGET_FIRST"

def test_one_shot_trade_ignores_later_signals():
    tc = TradingConfig(commission_cents_per_contract=122, slippage_cents_per_contract=125)
    rows = [bar(0, 100, 101, 99, 100), bar(1, 100, 103, 100, 102), bar(2, 100, 103, 100, 102)]
    result = _trade(rows[0].et_date, rows, "long", 0, 99, 1.0, tc, DriveConfig(5, .5, 1.0))
    assert result["status"] == "ACCEPTED"


def test_partition_isolation():
    rows = [bar(i, 100, 101, 99, 100) for i in range(10)]
    for i, row in enumerate(rows):
        object.__setattr__(row, "timestamp", row.timestamp.replace(day=2 + i))
    train, validation, test = partition_groups(rows)
    assert {d for d, _ in train}.isdisjoint({d for d, _ in validation})
    assert {d for d, _ in validation}.isdisjoint({d for d, _ in test})
    assert len(train) + len(validation) + len(test) == 10


def test_expanding_median_uses_prior_state_only():
    assert expanding_prior_width_median([]) == 0
    assert expanding_prior_width_median([10, 20, 30]) == 20
    assert expanding_prior_width_median([10, 20, 30, 1000]) == 30


def test_et_filtering_does_not_depend_on_process_timezone():
    rows = [bar(i, 100, 101, 99, 100) for i in range(16)]
    old = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Pacific/Honolulu"
        result = opening_range_drive(rows, DriveConfig(5, .5, 1.0), TradingConfig())
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
    assert result["status"] == "NO_TRADE"

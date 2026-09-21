from datetime import UTC, datetime

import pytest

from auctionflow.session import is_session_open, session_id


def utc(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_window_is_start_inclusive_end_exclusive():
    assert is_session_open(utc(2026, 1, 5, 14, 35))
    assert not is_session_open(utc(2026, 1, 5, 16, 30))
    assert not is_session_open(utc(2026, 1, 5, 14, 34))


def test_named_timezone_handles_dst():
    assert session_id(utc(2026, 7, 6, 13, 35)) == "2026-07-06"
    assert is_session_open(utc(2026, 7, 6, 13, 35))
    assert is_session_open(utc(2026, 3, 9, 13, 35))


def test_naive_timestamp_fails_closed():
    with pytest.raises(ValueError):
        is_session_open(datetime(2026, 1, 5, 9, 35))  # noqa: DTZ001
    with pytest.raises(ValueError):
        session_id(datetime(2026, 1, 5, 9, 35))  # noqa: DTZ001

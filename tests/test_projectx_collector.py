from datetime import UTC, datetime
from pathlib import Path

import pytest

from auctionflow.collector import RawCaptureWriter
from auctionflow.projectx import (
    FixtureTransport,
    ProjectXCollector,
    RateLimitError,
    RestRateLimiter,
)

FIXTURE = Path(__file__).parent / "fixtures/projectx/basic.json"


def test_fixture_callbacks_are_preserved_in_order():
    result = ProjectXCollector(FixtureTransport(FIXTURE), utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)).run()
    callbacks = [item.payload for item in result if item.method == "callback"]
    assert callbacks == [{"stream": "trades", "price": 100, "contract_id": "MES"}, {"stream": "trades", "price": 100, "contract_id": "MES"}, {"unknown": True}, "malformed"]


def test_reconnect_resubscribe_and_reconcile():
    sleeps = []
    transport = FixtureTransport({"reconnect": 4, "reconciliation": [1], "callbacks": []})
    result = ProjectXCollector(transport, utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
                               max_reconnects=4, sleep=sleeps.append).run()
    assert transport.subscriptions == ["trades", "quotes", "depth", "orders"] * 5
    assert transport.rest_calls == ["reconciliation"] * 5
    assert sum(item.method == "reconcile" for item in result) == 5
    assert sleeps == [1.0, 2.0, 4.0, 4.0]


def test_reconnect_exhaustion_emits_failure():
    sleeps = []
    collector = ProjectXCollector(FixtureTransport({"connect_failures": 9}), max_reconnects=4,
                                  sleep=sleeps.append)
    with pytest.raises(RuntimeError, match="reconnect exhausted"):
        collector.run()
    assert collector.envelopes[-1].health["status"] == "failure"
    assert sleeps == [1.0, 2.0, 4.0, 4.0]


def test_rate_limits_and_retry_after_without_real_sleep():
    now = [0.0]
    sleeps = []
    limiter = RestRateLimiter(monotonic=lambda: now[0], sleep=lambda delay: (sleeps.append(delay), now.__setitem__(0, now[0] + delay)))
    for _ in range(50):
        limiter.acquire("history")
    limiter.acquire("history")
    assert sleeps[-1] == 30
    responses = iter([RateLimitError(retry_after=4), "ok"])
    def operation():
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    assert limiter.request(operation, retries=1) == "ok"
    assert 4 in sleeps


def test_depth_reset_and_writer_are_preserved(tmp_path):
    payload = {"stream": "depth", "reset": True, "bids": [], "asks": []}
    path = tmp_path / "capture.jsonl"
    transport = FixtureTransport({"callbacks": [payload]})
    with RawCaptureWriter(path) as writer:
        result = ProjectXCollector(transport, writer=writer, utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)).run()
    assert next(item.payload for item in result if item.method == "callback") == payload
    assert path.read_text().count("\n") == len(result)


def test_selected_streams_filter_known_and_preserve_unknown():
    transport = FixtureTransport({"callbacks": [{"stream": "quotes", "price": 1}, {"mystery": True}, "bad"]})
    result = ProjectXCollector(transport, subscriptions=("trades",), utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)).run()
    assert not any(item.stream == "quotes" and item.method == "callback" for item in result)
    assert [item.payload for item in result if item.method == "callback" and item.stream == "unknown"] == [{"mystery": True}, "bad"]


def test_default_tuple_and_empty_selectors():
    payload = {"stream": "trades", "account_id": "A", "contract_id": "NQ", "elapsed_seconds": 1}
    result = ProjectXCollector(FixtureTransport({"callbacks": [payload]}),
        subscriptions=("trades", "quotes", "depth", "orders"), utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)).run()
    callbacks = [item.payload for item in result if item.method == "callback"]
    assert callbacks == [{"stream": "trades", "account_id": "A", "contract_id": "NQ"}]


def test_import_boundary():
    source = Path(__file__).parents[1] / "src/auctionflow/projectx.py"
    text = source.read_text()
    for forbidden in ("auctionflow.decision", "auctionflow.strategies", "auctionflow.risk", "submit_order", "modify_order", "cancel_order"):
        assert forbidden not in text

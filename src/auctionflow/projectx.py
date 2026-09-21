"""Offline ProjectX collector with callback-oriented fixture transport."""
from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collector import CollectorRawEnvelope


class RateLimitError(Exception):
    def __init__(self, status: int = 429, retry_after: float | None = None) -> None:
        super().__init__(f"REST request rate limited ({status})")
        self.status, self.retry_after = status, retry_after


class RestRateLimiter:
    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.monotonic, self.sleep = monotonic, sleep
        self._calls: dict[str, deque[float]] = {"history": deque(), "other": deque()}

    def acquire(self, endpoint: str = "other") -> None:
        kind, limit, window = ("history", 50, 30.0) if endpoint == "history" else ("other", 200, 60.0)
        calls = self._calls[kind]
        while True:
            now = self.monotonic()
            while calls and calls[0] <= now - window:
                calls.popleft()
            if len(calls) < limit:
                calls.append(now)
                return
            self.sleep(max(0.0, calls[0] + window - now))

    def request(self, operation: Callable[[], Any], endpoint: str = "other", *, retries: int = 3) -> Any:
        self.acquire(endpoint)
        for attempt in range(retries + 1):
            try:
                return operation()
            except RateLimitError as exc:
                if attempt >= retries:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else min(2.0 ** attempt, 8.0)
                self.sleep(max(0.0, delay))
                self.acquire(endpoint)
        raise AssertionError("unreachable")


class FixtureTransport:
    """Deterministic transport: REST responses and websocket callbacks come from JSON."""
    def __init__(self, fixture: str | Path | Mapping[str, Any], *, on_message: Callable[[Any], None] | None = None) -> None:
        self.fixture = fixture
        self.on_message = on_message
        self.connected = False
        self.subscriptions: list[str] = []
        self.rest_calls: list[str] = []
        self._data = json.loads(Path(fixture).read_text()) if isinstance(fixture, (str, Path)) else dict(fixture)
        self._connect_failures = self._data.get("connect_failures", 0)
        self._reconnect = self._data.get("reconnect", False)

    def authenticate(self) -> Mapping[str, Any]:
        return self._data.get("auth", {"authenticated": True})

    def rest(self, endpoint: str) -> Any:
        self.rest_calls.append(endpoint)
        response = self._data.get("rest", {}).get(endpoint, self._data.get("reconciliation", []))
        if isinstance(response, Mapping) and response.get("status") == 429:
            raise RateLimitError(retry_after=response.get("retry_after"))
        return response

    def connect(self) -> None:
        if self._connect_failures:
            self._connect_failures -= 1
            raise ConnectionError("fixture connection failed")
        self.connected = True

    def subscribe(self, stream: str) -> None:
        self.subscriptions.append(stream)

    def callbacks(self) -> list[Any]:
        return list(self._data.get("callbacks", []))

    def disconnect(self) -> None:
        self.connected = False

    @property
    def should_reconnect(self) -> bool:
        return self._reconnect

    def consume_reconnect(self) -> None:
        self._reconnect = self._reconnect - 1 if isinstance(self._reconnect, int) else False


class ProjectXCollector:
    def __init__(self, transport: FixtureTransport, *, source: str = "projectx", hub: str = "projectx",
                 subscriptions: tuple[str, ...] = ("trades", "quotes", "depth", "orders"),
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 max_reconnects: int = 3, utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 writer: Any = None, account_ids: tuple[str, ...] = (), contract_ids: tuple[str, ...] = (),
                 duration_seconds: float | None = None) -> None:
        self.transport, self.source, self.hub = transport, source, hub
        self.subscriptions, self.max_reconnects = subscriptions, max_reconnects
        self.account_ids, self.contract_ids = account_ids, contract_ids
        self.clock, self.utc_clock, self.sleep = monotonic, utc_clock, sleep
        self.writer, self.duration_seconds = writer, duration_seconds
        self.limiter = RestRateLimiter(monotonic=monotonic, sleep=sleep)
        self.sequence = 0
        self.envelopes: list[CollectorRawEnvelope] = []
        transport.on_message = self.callback

    def _emit(self, method: str, stream: str, payload: Any = None, health: Mapping[str, Any] | None = None) -> None:
        now = self.clock()
        envelope = CollectorRawEnvelope(source=self.source, transport="fixture", hub=self.hub,
            method=method, stream=stream, received_at=self.utc_clock(),
            monotonic_ns=max(0, int(now * 1_000_000_000)), local_sequence=self.sequence,
            payload=payload, health=dict(health or {}))
        self.envelopes.append(envelope)
        if self.writer is not None:
            self.writer.append(envelope)
        self.sequence += 1

    def callback(self, payload: Any) -> None:
        stream = payload.get("stream", "unknown") if isinstance(payload, Mapping) else "unknown"
        if isinstance(payload, Mapping):
            account_scoped = {"accounts", "positions", "orders", "fills"}
            market_scoped = {"quotes", "trades", "depth"}
            if self.account_ids and stream in account_scoped and payload.get("account_id") not in self.account_ids:
                self._emit("callback", "health", None, {"status": "ignored_scope", "stream": stream, "scope": "account"})
                return
            if self.contract_ids and stream in market_scoped and payload.get("contract_id") not in self.contract_ids:
                self._emit("callback", "health", None, {"status": "ignored_scope", "stream": stream, "scope": "contract"})
                return
            if self.account_ids and payload.get("account_id") and payload["account_id"] not in self.account_ids: return
            if self.contract_ids and payload.get("contract_id") and payload["contract_id"] not in self.contract_ids: return
        known = {"quotes", "trades", "depth", "accounts", "positions", "orders", "fills"}
        if stream in known and stream not in self.subscriptions:
            self._emit("callback", "health", None, {"status": "ignored_stream", "stream": stream, "evidence": "callback_received"})
            return
        if isinstance(payload, Mapping) and "elapsed_seconds" in payload:
            payload = {key: value for key, value in payload.items() if key != "elapsed_seconds"}
        self._emit("callback", stream, payload)

    def run(self) -> list[CollectorRawEnvelope]:
        self._emit("authenticate", "lifecycle", self.transport.authenticate(), {"status": "ok"})
        self._emit("reconcile", "reconciliation", self.limiter.request(lambda: self.transport.rest("reconciliation")), {"status": "ok"})
        attempts = 0
        while True:
            try:
                self.transport.connect()
                self._emit("connect", "lifecycle", None, {"status": "connected"})
                for stream in self.subscriptions:
                    self.transport.subscribe(stream)
                    self._emit("subscribe", stream, None, {"status": "subscribed"})
                for item in self.transport.callbacks():
                    if self.duration_seconds is not None:
                        elapsed = item.get("elapsed_seconds") if isinstance(item, Mapping) else None
                        if elapsed is not None and elapsed > self.duration_seconds: break
                    self.callback(item)
                self.transport.disconnect()
                self._emit("disconnect", "lifecycle", None, {"status": "disconnected"})
                if not self.transport.should_reconnect:
                    break
                attempts += 1
                if attempts > self.max_reconnects:
                    self._emit("failure", "health", None, {"status": "failure", "error": "reconnect exhausted"})
                    raise RuntimeError("ProjectX reconnect exhausted")
                self._emit("reconnect", "lifecycle", None, {"status": "retry"})
                self.sleep(min(2.0 ** (attempts - 1), 4.0))
                self.transport.consume_reconnect()
                self._emit("reconcile", "reconciliation", self.limiter.request(lambda: self.transport.rest("reconciliation")), {"status": "ok"})
            except ConnectionError as exc:
                attempts += 1
                if attempts > self.max_reconnects:
                    self._emit("failure", "health", None, {"status": "failure", "error": "reconnect exhausted"})
                    raise RuntimeError("ProjectX reconnect exhausted") from exc
                self.sleep(min(2.0 ** (attempts - 1), 4.0))
        return self.envelopes

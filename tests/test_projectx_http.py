from __future__ import annotations

import ast
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import pytest
from pydantic import ValidationError

from auctionflow.projectx_http import (
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    ProjectXHttp,
    urllib_transport,
)

START = datetime(2025, 1, 20, 15, 47, 39, 882000, tzinfo=UTC)
END = datetime(2025, 1, 30, 15, 47, 39, 882000, tzinfo=UTC)


def parsed(raw: str) -> dict[str, Any]:
    result = json.loads(raw, parse_float=Decimal)
    assert isinstance(result, dict)
    return result


AUTH_RESPONSE = parsed(
    '{"token":"jwt-token","success":true,"errorCode":0,"errorMessage":null}'
)

ACCOUNT_RESPONSE = parsed(
    """{
      "accounts":[{
        "id":101,"name":"SIM","balance":50000.25,
        "canTrade":true,"isVisible":true,"simulated":true
      }],
      "success":true,"errorCode":0,"errorMessage":null
    }"""
)

CONTRACT_RESPONSE = parsed(
    """{
      "contracts":[{
        "id":"CON.F.US.MES.U25","name":"MESU5",
        "description":"Micro E-mini S&P 500: September 2025",
        "tickSize":0.25,"tickValue":1.25,"activeContract":true,
        "symbolId":"F.US.MES"
      }],
      "success":true,"errorCode":0,"errorMessage":null
    }"""
)

BAR_RESPONSE = parsed(
    """{
      "bars":[
        {"t":"2025-01-20T15:48:00.123456Z","o":6000.10,"h":6001.20,
         "l":5999.30,"c":6000.40,"v":0},
        {"t":"2025-01-20T15:47:00+00:00","o":5999,"h":6000.10,
         "l":5998.90,"c":6000,"v":12}
      ],
      "success":true,"errorCode":0,"errorMessage":null
    }"""
)

ORDER_RESPONSE = parsed(
    """{
      "orders":[{
        "id":36598,"accountId":101,"contractId":"CON.F.US.MES.U25",
        "symbolId":"F.US.MES",
        "creationTimestamp":"2025-01-20T15:47:39.882000Z",
        "updateTimestamp":"2025-01-20T15:48:00+00:00",
        "status":2,"type":2,"side":0,"size":1,"limitPrice":null,
        "stopPrice":null,"fillVolume":1,"filledPrice":6000.25,"customTag":null
      }],
      "success":true,"errorCode":0,"errorMessage":null
    }"""
)

POSITION_RESPONSE = parsed(
    """{
      "positions":[{
        "id":6124,"accountId":101,"contractId":"CON.F.US.MES.U25",
        "creationTimestamp":"2025-01-20T15:47:39.882000+00:00",
        "type":1,"size":2,"averagePrice":5999.75
      }],
      "success":true,"errorCode":0,"errorMessage":null
    }"""
)

TRADE_RESPONSE = parsed(
    """{
      "trades":[{
        "id":8603,"accountId":101,"contractId":"CON.F.US.MES.U25",
        "creationTimestamp":"2025-01-20T15:47:39.882000Z",
        "price":6000.25,"profitAndLoss":null,"fees":1.40,
        "side":0,"size":1,"voided":false,"orderId":36598
      }],
      "success":true,"errorCode":0,"errorMessage":null
    }"""
)


@pytest.fixture(autouse=True)
def prohibit_unexpected_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("test attempted real network access")

    monkeypatch.setattr(urllib.request, "urlopen", fail)


class StubTransport:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    def __call__(
        self, path: str, payload: dict[str, Any], token: str | None
    ) -> dict[str, Any]:
        self.calls.append((path, payload, token))
        return self.responses[path]


def authenticated_client(
    responses: dict[str, dict[str, Any]], *, limiter: Any = None
) -> tuple[ProjectXHttp, StubTransport]:
    transport = StubTransport({"/api/Auth/loginKey": AUTH_RESPONSE, **responses})
    client = ProjectXHttp(
        transport, username="user-name", api_key="api-secret", rate_limiter=limiter
    )
    client.authenticate()
    return client, transport


def test_authentication_uses_exact_payload_and_environment_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOPSTEPX_USERNAME", "env-user")
    monkeypatch.setenv("TOPSTEPX_API_KEY", "env-secret")
    transport = StubTransport({"/api/Auth/loginKey": AUTH_RESPONSE})

    client = ProjectXHttp(transport)
    client.authenticate()

    assert transport.calls == [
        (
            "/api/Auth/loginKey",
            {"userName": "env-user", "apiKey": "env-secret"},
            None,
        )
    ]
    assert "env-user" not in repr(client)
    assert "env-secret" not in repr(client)
    assert "jwt-token" not in repr(client)


def test_credentials_and_transport_failures_never_leak_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TOPSTEPX_USERNAME", raising=False)
    monkeypatch.delenv("TOPSTEPX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="credentials") as missing:
        ProjectXHttp(lambda *_: {}, username=None, api_key=None)
    assert "secret" not in str(missing.value).lower()

    def leaking_transport(
        path: str, payload: dict[str, Any], token: str | None
    ) -> dict[str, Any]:
        raise RuntimeError(f"leaked {payload} {token}")

    client = ProjectXHttp(
        leaking_transport, username="private-user", api_key="private-key"
    )
    with pytest.raises(RuntimeError, match="authentication request failed") as failed:
        client.authenticate()
    message = str(failed.value)
    assert "private-user" not in message
    assert "private-key" not in message


def test_accounts_schema_payload_and_explicit_simulated_policy() -> None:
    client, transport = authenticated_client({"/api/Account/search": ACCOUNT_RESPONSE})

    accounts = client.accounts(only_active=False)

    assert accounts[0].balance == Decimal("50000.25")
    assert transport.calls[-1] == (
        "/api/Account/search",
        {"onlyActiveAccounts": False},
        "jwt-token",
    )
    assert client.select_account(accounts, 101, required_simulated=True) is accounts[0]
    with pytest.raises(ValueError, match="required account"):
        client.select_account(accounts, 101, required_simulated=False)
    with pytest.raises(TypeError, match="boolean"):
        client.select_account(accounts, 101, required_simulated=1)  # type: ignore[arg-type]


def test_account_simulated_field_is_mandatory_and_not_inferred() -> None:
    account = ACCOUNT_RESPONSE["accounts"][0].copy()
    del account["simulated"]
    client, _ = authenticated_client(
        {
            "/api/Account/search": {
                **ACCOUNT_RESPONSE,
                "accounts": [account],
            }
        }
    )
    with pytest.raises(ValidationError):
        client.accounts()


def test_contract_search_and_available_use_exact_payloads_and_models() -> None:
    client, transport = authenticated_client(
        {
            "/api/Contract/search": CONTRACT_RESPONSE,
            "/api/Contract/available": CONTRACT_RESPONSE,
        }
    )

    searched = client.contracts("MES", live=False)
    available = client.available_contracts(live=True)

    assert searched[0].tick_size == Decimal("0.25")
    assert searched[0].tick_value == Decimal("1.25")
    assert client.select_contract(available, "CON.F.US.MES.U25") is available[0]
    assert transport.calls[-2:] == [
        (
            "/api/Contract/search",
            {"searchText": "MES", "live": False},
            "jwt-token",
        ),
        ("/api/Contract/available", {"live": True}, "jwt-token"),
    ]


def test_history_uses_full_swagger_payload_and_lossless_json_values() -> None:
    client, transport = authenticated_client(
        {"/api/History/retrieveBars": BAR_RESPONSE}
    )

    bars = client.bars(
        "CON.F.US.MES.U25",
        START,
        END,
        live=False,
        unit=3,
        unit_number=2,
        limit=7,
        include_partial_bar=True,
    )

    assert bars[0].open == Decimal("6000.10")
    assert bars[0].timestamp == datetime(
        2025, 1, 20, 15, 48, 0, 123456, tzinfo=UTC
    )
    assert bars[0].volume == 0
    assert bars[0].timestamp > bars[1].timestamp  # The API may return newest first.
    assert transport.calls[-1] == (
        "/api/History/retrieveBars",
        {
            "contractId": "CON.F.US.MES.U25",
            "live": False,
            "startTime": START.isoformat(),
            "endTime": END.isoformat(),
            "unit": 3,
            "unitNumber": 2,
            "limit": 7,
            "includePartialBar": True,
        },
        "jwt-token",
    )


def test_order_history_exact_model_payload_and_optional_end() -> None:
    client, transport = authenticated_client({"/api/Order/search": ORDER_RESPONSE})

    orders = client.orders(101, START, END)
    client.orders(101, START)

    assert orders[0].filled_price == Decimal("6000.25")
    assert orders[0].limit_price is None
    assert orders[0].creation_timestamp.tzinfo is UTC
    assert transport.calls[-2][1] == {
        "accountId": 101,
        "startTimestamp": START.isoformat(),
        "endTimestamp": END.isoformat(),
    }
    assert transport.calls[-1][1] == {
        "accountId": 101,
        "startTimestamp": START.isoformat(),
    }


def test_open_positions_exact_model_and_payload() -> None:
    client, transport = authenticated_client(
        {"/api/Position/searchOpen": POSITION_RESPONSE}
    )

    positions = client.positions(101)

    assert positions[0].average_price == Decimal("5999.75")
    assert transport.calls[-1] == (
        "/api/Position/searchOpen",
        {"accountId": 101},
        "jwt-token",
    )


def test_trade_history_allows_swagger_nullable_pnl_and_exact_payload() -> None:
    client, transport = authenticated_client({"/api/Trade/search": TRADE_RESPONSE})

    trades = client.trades(101, START, END)

    assert trades[0].price == Decimal("6000.25")
    assert trades[0].profit_and_loss is None
    assert trades[0].fees == Decimal("1.40")
    assert transport.calls[-1] == (
        "/api/Trade/search",
        {
            "accountId": 101,
            "startTimestamp": START.isoformat(),
            "endTimestamp": END.isoformat(),
        },
        "jwt-token",
    )


def test_models_reject_extra_fields_float_coercion_and_non_utc_timestamps() -> None:
    extra = {**BAR_RESPONSE, "bars": [{**BAR_RESPONSE["bars"][0], "k": 1}]}
    client, transport = authenticated_client({"/api/History/retrieveBars": extra})
    with pytest.raises(ValidationError):
        client.bars("CON.F.US.MES.U25", START, END)

    transport.responses["/api/History/retrieveBars"] = {
        **BAR_RESPONSE,
        "bars": [{**BAR_RESPONSE["bars"][0], "o": 6000.1}],
    }
    with pytest.raises(ValidationError):
        client.bars("CON.F.US.MES.U25", START, END)

    non_utc = START.astimezone(timezone(timedelta(hours=-5)))
    with pytest.raises(ValueError, match="UTC"):
        client.bars("CON.F.US.MES.U25", non_utc, END)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"success": False, "errorCode": 1, "errorMessage": "denied"}, "invalid"),
        ({"success": True, "errorCode": True, "errorMessage": None}, "invalid"),
        ({"success": True, "errorCode": 0, "errorMessage": None}, "invalid"),
    ],
)
def test_response_envelopes_fail_closed(
    response: dict[str, Any], expected: str
) -> None:
    client, _ = authenticated_client({"/api/Account/search": response})
    with pytest.raises((TypeError, ValueError), match=expected):
        client.accounts()


def test_authentication_is_required_and_endpoint_allowlist_precedes_transport() -> None:
    calls: list[str] = []

    def transport(
        path: str, payload: dict[str, Any], token: str | None
    ) -> dict[str, Any]:
        calls.append(path)
        return AUTH_RESPONSE

    client = ProjectXHttp(transport, username="user", api_key="key")
    with pytest.raises(RuntimeError, match="authentication required"):
        client.accounts()
    assert calls == []

    client.authenticate()
    forbidden_path = "/api/Order/" + bytes((112, 108, 97, 99, 101)).decode()
    with pytest.raises(ValueError, match="endpoint"):
        client._call(forbidden_path, {})
    assert calls == ["/api/Auth/loginKey"]


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda client: client.accounts(only_active=1), "boolean"),
        (lambda client: client.contracts(1), "string"),
        (lambda client: client.available_contracts(live=1), "boolean"),
        (lambda client: client.positions(0), "positive integer"),
        (lambda client: client.trades(101, END, START), "time window"),
        (lambda client: client.bars("id", END, START), "history window"),
        (lambda client: client.bars("id", START, END, unit=7), "between 1 and 6"),
        (lambda client: client.bars("id", START, END, limit=20_001), "between 1 and 20000"),
    ],
)
def test_invalid_requests_never_reach_transport(operation: Any, message: str) -> None:
    client, transport = authenticated_client({})
    before = len(transport.calls)
    with pytest.raises((TypeError, ValueError), match=message):
        operation(client)
    assert len(transport.calls) == before


def test_rate_limiter_receives_history_and_other_buckets() -> None:
    class Limiter:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        def acquire(self, kind: str) -> None:
            self.kinds.append(kind)

    limiter = Limiter()
    client, _ = authenticated_client(
        {
            "/api/Account/search": ACCOUNT_RESPONSE,
            "/api/History/retrieveBars": BAR_RESPONSE,
        },
        limiter=limiter,
    )
    client.accounts()
    client.bars("CON.F.US.MES.U25", START, END)
    assert limiter.kinds == ["other", "other", "history"]


class FakeResponse:
    def __init__(self, body: bytes, content_length: str | None = None) -> None:
        self.body = body
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self.read_limit: int | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.body[:limit]


def test_urllib_transport_builds_bounded_post_and_parses_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        b'{"value":0.10000000000000001,"success":true,"errorCode":0,"errorMessage":null}'
    )
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = urllib_transport("/api/Account/search", {"onlyActiveAccounts": True}, "jwt")

    request = captured["request"]
    assert request.full_url == "https://api.topstepx.com/api/Account/search"
    assert request.method == "POST"
    assert json.loads(request.data) == {"onlyActiveAccounts": True}
    assert request.get_header("Authorization") == "Bearer jwt"
    assert request.get_header("Content-type") == "application/json"
    assert captured["timeout"] == REQUEST_TIMEOUT_SECONDS
    assert response.read_limit == MAX_RESPONSE_BYTES + 1
    assert result["value"] == Decimal("0.10000000000000001")


@pytest.mark.parametrize(
    ("body", "length", "message"),
    [
        (b"[]", None, "invalid TopstepX response"),
        (b"not-json", None, "invalid TopstepX JSON"),
        (b'{"x":NaN}', None, "invalid TopstepX JSON"),
        (b"{}", str(MAX_RESPONSE_BYTES + 1), "response too large"),
        (b"{}", "not-an-int", "invalid TopstepX response"),
    ],
)
def test_urllib_transport_rejects_invalid_or_oversized_responses(
    monkeypatch: pytest.MonkeyPatch, body: bytes, length: str | None, message: str
) -> None:
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse(body, length)
    )
    with pytest.raises((RuntimeError, TypeError), match=message):
        urllib_transport("/api/Account/search", {}, "jwt")


def test_urllib_transport_rejects_body_over_read_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="response too large"):
        urllib_transport("/api/Account/search", {}, "jwt")
    assert response.read_limit == MAX_RESPONSE_BYTES + 1


def test_urllib_transport_sanitizes_http_and_network_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def http_failure(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("https://fixed.invalid", 401, "secret", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", http_failure)
    with pytest.raises(RuntimeError, match="HTTP error 401") as http_error:
        urllib_transport("/api/Account/search", {}, "jwt-secret")
    assert "jwt-secret" not in str(http_error.value)

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("secret timeout")),
    )
    with pytest.raises(RuntimeError, match="request failed") as network_error:
        urllib_transport("/api/Account/search", {}, "jwt-secret")
    assert "jwt-secret" not in str(network_error.value)


def test_urllib_transport_rejects_unapproved_paths_and_unsafe_tokens_before_io() -> None:
    forbidden_path = "/api/Order/" + bytes((112, 108, 97, 99, 101)).decode()
    with pytest.raises(ValueError, match="endpoint"):
        urllib_transport(forbidden_path, {}, "jwt")
    with pytest.raises(ValueError, match="authentication required"):
        urllib_transport("/api/Account/search", {}, None)
    with pytest.raises(ValueError, match="token"):
        urllib_transport("/api/Account/search", {}, "jwt\r\nunsafe")


def test_client_source_contains_only_read_surface() -> None:
    source_path = Path(__file__).parents[1] / "src" / "auctionflow" / "projectx_http.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    endpoint_strings = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/api/")
    }
    assert endpoint_strings == {
        "/api/Auth/loginKey",
        "/api/Account/search",
        "/api/Contract/search",
        "/api/Contract/available",
        "/api/History/retrieveBars",
        "/api/Order/search",
        "/api/Position/searchOpen",
        "/api/Trade/search",
    }
    public_methods = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProjectXHttp"
        for node in node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    forbidden_method_names = {
        "".join(chars)
        for chars in (("p", "l", "a", "c", "e"), ("m", "o", "d", "i", "f", "y"), ("c", "a", "n", "c", "e", "l"))
    }
    assert public_methods.isdisjoint(forbidden_method_names)

"""Read-only TopstepX REST boundary."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE_URL = "https://api.topstepx.com"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10.0

_AUTH_PATH = "/api/Auth/loginKey"
_READ_PATHS = frozenset(
    {
        _AUTH_PATH,
        "/api/Account/search",
        "/api/Contract/search",
        "/api/Contract/available",
        "/api/History/retrieveBars",
        "/api/Order/search",
        "/api/Position/searchOpen",
        "/api/Trade/search",
    }
)

Transport = Callable[[str, dict[str, Any], str | None], Mapping[str, Any]]


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamp must be an ISO-8601 UTC datetime") from exc
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(None)
    ):
        raise ValueError("timestamp must be UTC")
    return value.astimezone(UTC)


def _decimal_number(value: Any) -> Decimal:
    """Accept only numbers produced by lossless JSON decoding."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    raise ValueError("value must be a JSON number")


class _SwaggerModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", populate_by_name=True)


class Account(_SwaggerModel):
    id: int
    name: str
    balance: Decimal
    can_trade: bool = Field(alias="canTrade")
    is_visible: bool = Field(alias="isVisible")
    simulated: bool

    _balance = field_validator("balance", mode="before")(_decimal_number)


class Contract(_SwaggerModel):
    id: str
    name: str
    description: str
    tick_size: Decimal = Field(alias="tickSize")
    tick_value: Decimal = Field(alias="tickValue")
    active: bool = Field(alias="activeContract")
    symbol_id: str = Field(alias="symbolId")

    _decimals = field_validator("tick_size", "tick_value", mode="before")(_decimal_number)


class Bar(_SwaggerModel):
    timestamp: datetime = Field(alias="t")
    open: Decimal = Field(alias="o")
    high: Decimal = Field(alias="h")
    low: Decimal = Field(alias="l")
    close: Decimal = Field(alias="c")
    volume: int = Field(alias="v", ge=0)

    _timestamp = field_validator("timestamp", mode="before")(_utc_datetime)
    _prices = field_validator("open", "high", "low", "close", mode="before")(
        _decimal_number
    )


class Order(_SwaggerModel):
    id: int
    account_id: int = Field(alias="accountId")
    contract_id: str = Field(alias="contractId")
    symbol_id: str = Field(alias="symbolId")
    creation_timestamp: datetime = Field(alias="creationTimestamp")
    update_timestamp: datetime = Field(alias="updateTimestamp")
    status: int
    type: int
    side: int
    size: int
    limit_price: Decimal | None = Field(alias="limitPrice")
    stop_price: Decimal | None = Field(alias="stopPrice")
    fill_volume: int = Field(alias="fillVolume")
    filled_price: Decimal | None = Field(alias="filledPrice")
    custom_tag: str | None = Field(alias="customTag")

    _timestamps = field_validator(
        "creation_timestamp", "update_timestamp", mode="before"
    )(_utc_datetime)
    _prices = field_validator(
        "limit_price", "stop_price", "filled_price", mode="before"
    )(lambda value: None if value is None else _decimal_number(value))


class Position(_SwaggerModel):
    id: int
    account_id: int = Field(alias="accountId")
    contract_id: str = Field(alias="contractId")
    creation_timestamp: datetime = Field(alias="creationTimestamp")
    type: int
    size: int
    average_price: Decimal = Field(alias="averagePrice")

    _timestamp = field_validator("creation_timestamp", mode="before")(_utc_datetime)
    _price = field_validator("average_price", mode="before")(_decimal_number)


class Trade(_SwaggerModel):
    id: int
    account_id: int = Field(alias="accountId")
    contract_id: str = Field(alias="contractId")
    creation_timestamp: datetime = Field(alias="creationTimestamp")
    price: Decimal
    profit_and_loss: Decimal | None = Field(alias="profitAndLoss")
    fees: Decimal
    side: int
    size: int
    voided: bool
    order_id: int = Field(alias="orderId")

    _timestamp = field_validator("creation_timestamp", mode="before")(_utc_datetime)
    _decimals = field_validator("price", "profit_and_loss", "fees", mode="before")(
        lambda value: None if value is None else _decimal_number(value)
    )


def _validate_envelope(response: Mapping[str, Any]) -> None:
    if not isinstance(response, Mapping):
        raise TypeError("invalid TopstepX response")
    error_code = response.get("errorCode")
    error_message = response.get("errorMessage")
    if (
        response.get("success") is not True
        or not isinstance(error_code, int)
        or isinstance(error_code, bool)
        or not (error_message is None or isinstance(error_message, str))
    ):
        raise ValueError("invalid TopstepX response")


def _response_items(response: Mapping[str, Any], key: str) -> list[Any]:
    _validate_envelope(response)
    items = response.get(key)
    if not isinstance(items, list):
        raise TypeError("invalid TopstepX response")
    return items


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def urllib_transport(
    path: str, payload: dict[str, Any], token: str | None
) -> Mapping[str, Any]:
    """POST one allowlisted endpoint with fixed URL, timeout, and response bound."""
    if path not in _READ_PATHS:
        raise ValueError("invalid API endpoint")
    if path != _AUTH_PATH and (not isinstance(token, str) or not token):
        raise ValueError("authentication required")
    if token is not None and ("\r" in token or "\n" in token):
        raise ValueError("invalid authentication token")

    request = urllib.request.Request(
        BASE_URL + path,
        json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8"),
        method="POST",
    )
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise RuntimeError("TopstepX response too large")
                except ValueError as exc:
                    raise RuntimeError("invalid TopstepX response") from exc
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"TopstepX HTTP error {exc.code}") from None
    except (OSError, TimeoutError):
        raise RuntimeError("TopstepX request failed") from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("TopstepX response too large")
    try:
        decoded = json.loads(
            body,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RuntimeError("invalid TopstepX JSON response") from None
    if not isinstance(decoded, dict):
        raise TypeError("invalid TopstepX response")
    return decoded


class ProjectXHttp:
    """Narrow client exposing only the approved read-only REST surface."""

    def __init__(
        self,
        transport: Transport = urllib_transport,
        *,
        username: str | None = None,
        api_key: str | None = None,
        rate_limiter: Any = None,
    ) -> None:
        self._username = username if username is not None else os.getenv("TOPSTEPX_USERNAME")
        self._api_key = api_key if api_key is not None else os.getenv("TOPSTEPX_API_KEY")
        if not self._username or not self._api_key:
            raise ValueError("TopstepX credentials are required")
        self._transport = transport
        self._rate_limiter = rate_limiter
        self._token: str | None = None

    def authenticate(self) -> None:
        if self._rate_limiter is not None:
            self._rate_limiter.acquire("other")
        try:
            response = self._transport(
                _AUTH_PATH,
                {"userName": self._username, "apiKey": self._api_key},
                None,
            )
        except Exception:  # noqa: BLE001 - sanitize credential-bearing transport failures
            raise RuntimeError("TopstepX authentication request failed") from None
        try:
            _validate_envelope(response)
            token = response.get("token")
            if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ValueError("authentication failed") from None
        self._token = token

    def accounts(self, only_active: bool = True) -> tuple[Account, ...]:
        _require_bool(only_active, "only_active")
        response = self._call(
            "/api/Account/search", {"onlyActiveAccounts": only_active}
        )
        return tuple(Account.model_validate(item) for item in _response_items(response, "accounts"))

    def contracts(self, search_text: str = "", live: bool = False) -> tuple[Contract, ...]:
        if not isinstance(search_text, str):
            raise TypeError("search_text must be a string")
        _require_bool(live, "live")
        response = self._call(
            "/api/Contract/search", {"searchText": search_text, "live": live}
        )
        return tuple(
            Contract.model_validate(item) for item in _response_items(response, "contracts")
        )

    def available_contracts(self, live: bool = False) -> tuple[Contract, ...]:
        _require_bool(live, "live")
        response = self._call("/api/Contract/available", {"live": live})
        return tuple(
            Contract.model_validate(item) for item in _response_items(response, "contracts")
        )

    def bars(
        self,
        contract_id: str,
        start_time: datetime,
        end_time: datetime,
        *,
        live: bool = False,
        unit: int = 2,
        unit_number: int = 1,
        limit: int = 20_000,
        include_partial_bar: bool = False,
    ) -> tuple[Bar, ...]:
        if not isinstance(contract_id, str) or not contract_id:
            raise ValueError("contract_id is required")
        start = _utc_datetime(start_time)
        end = _utc_datetime(end_time)
        _require_bool(live, "live")
        _require_bool(include_partial_bar, "include_partial_bar")
        if start >= end:
            raise ValueError("invalid history window")
        if not isinstance(unit, int) or isinstance(unit, bool) or not 1 <= unit <= 6:
            raise ValueError("unit must be between 1 and 6")
        if not isinstance(unit_number, int) or isinstance(unit_number, bool):
            raise TypeError("unit_number must be an integer")
        if unit_number < 1:
            raise ValueError("unit_number must be positive")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("limit must be an integer")
        if not 1 <= limit <= 20_000:
            raise ValueError("limit must be between 1 and 20000")
        response = self._call(
            "/api/History/retrieveBars",
            {
                "contractId": contract_id,
                "live": live,
                "startTime": start.isoformat(),
                "endTime": end.isoformat(),
                "unit": unit,
                "unitNumber": unit_number,
                "limit": limit,
                "includePartialBar": include_partial_bar,
            },
            rate_kind="history",
        )
        return tuple(Bar.model_validate(item) for item in _response_items(response, "bars"))

    def orders(
        self,
        account_id: int,
        start_time: datetime,
        end_time: datetime | None = None,
    ) -> tuple[Order, ...]:
        response = self._window_call(
            "/api/Order/search", account_id, start_time, end_time
        )
        return tuple(Order.model_validate(item) for item in _response_items(response, "orders"))

    def positions(self, account_id: int) -> tuple[Position, ...]:
        _require_account_id(account_id)
        response = self._call("/api/Position/searchOpen", {"accountId": account_id})
        return tuple(
            Position.model_validate(item) for item in _response_items(response, "positions")
        )

    def trades(
        self,
        account_id: int,
        start_time: datetime,
        end_time: datetime | None = None,
    ) -> tuple[Trade, ...]:
        response = self._window_call(
            "/api/Trade/search", account_id, start_time, end_time
        )
        return tuple(Trade.model_validate(item) for item in _response_items(response, "trades"))

    def _window_call(
        self,
        path: str,
        account_id: int,
        start_time: datetime,
        end_time: datetime | None,
    ) -> Mapping[str, Any]:
        _require_account_id(account_id)
        start = _utc_datetime(start_time)
        payload: dict[str, Any] = {
            "accountId": account_id,
            "startTimestamp": start.isoformat(),
        }
        if end_time is not None:
            end = _utc_datetime(end_time)
            if start >= end:
                raise ValueError("invalid time window")
            payload["endTimestamp"] = end.isoformat()
        return self._call(path, payload)

    def _call(
        self, path: str, payload: dict[str, Any], *, rate_kind: str = "other"
    ) -> Mapping[str, Any]:
        if path not in _READ_PATHS or path == _AUTH_PATH:
            raise ValueError("invalid API endpoint")
        if self._token is None:
            raise RuntimeError("authentication required")
        if self._rate_limiter is not None:
            self._rate_limiter.acquire(rate_kind)
        try:
            response = self._transport(path, payload, self._token)
        except Exception:  # noqa: BLE001 - sanitize bearer-token-bearing transport failures
            raise RuntimeError("TopstepX request failed") from None
        _validate_envelope(response)
        return response

    @staticmethod
    def select_account(
        accounts: Sequence[Account], account_id: int, required_simulated: bool
    ) -> Account:
        _require_account_id(account_id)
        _require_bool(required_simulated, "required_simulated")
        matches = [
            account
            for account in accounts
            if account.id == account_id
            and account.can_trade
            and account.is_visible
            and account.simulated is required_simulated
        ]
        if len(matches) != 1:
            raise ValueError("required account not found")
        return matches[0]

    @staticmethod
    def select_contract(contracts: Sequence[Contract], contract_id: str) -> Contract:
        matches = [
            contract
            for contract in contracts
            if contract.id == contract_id
            and contract.active
            and contract.tick_size == Decimal("0.25")
            and contract.tick_value == Decimal("1.25")
        ]
        if len(matches) != 1:
            raise ValueError("required MES contract not found")
        return matches[0]


def _require_bool(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")


def _require_account_id(account_id: Any) -> None:
    if not isinstance(account_id, int) or isinstance(account_id, bool):
        raise TypeError("account_id must be an integer")
    if account_id <= 0:
        raise ValueError("account_id must be a positive integer")

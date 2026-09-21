"""Credentialed, read-only TopstepX shadow snapshot publication."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .projectx_http import ProjectXHttp
from .reconciliation import ExpectedAccountState, ObservedBrokerTelemetry, reconcile

ACTIVE_STATUSES = frozenset({1, 6})
PROTECTIVE_ORDER_TYPES = frozenset({3, 4, 5})


def _order_class(order: Any) -> str:
    if order.type in PROTECTIVE_ORDER_TYPES:
        return "protective_stop_orders"
    if order.type == 1 and isinstance(order.custom_tag, str):
        tag = order.custom_tag.strip().lower()
        if tag in {"target", "take-profit", "take_profit", "tp", "protective-target"}:
            return "protective_target_orders"
    return "working_entry_orders"


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _fixture_transport(path: Path):
    try:
        data = json.loads(
            path.read_text(),
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ValueError("invalid fixture JSON") from None
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise ValueError("fixture must be a JSON object")

    def transport(endpoint: str, payload: dict[str, Any], token: str | None) -> Any:
        response = data.get(endpoint)
        if not isinstance(response, dict):
            raise TypeError("fixture endpoint response must be an object")
        return response

    return transport


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _publish(path: Path, report: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    published_inode: int | None = None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.link(staged, path)
        published_inode = path.stat().st_ino
    except Exception:
        if published_inode is not None:
            try:
                if path.stat().st_ino == published_inode:
                    path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        staged.unlink(missing_ok=True)


def snapshot(
    client: ProjectXHttp,
    *, account_id: int,
    contract_id: str,
    clock: Callable[[], datetime],
    lookback_minutes: int = 60,
    output: Path,
) -> dict[str, Any]:
    if lookback_minutes < 1 or lookback_minutes > 1440:
        raise ValueError("lookback-minutes must be between 1 and 1440")
    observed_at = clock().astimezone(UTC)
    start = observed_at - timedelta(minutes=lookback_minutes)
    client.authenticate()
    account = client.select_account(client.accounts(), account_id, True)
    contract = client.select_contract(client.contracts("MES", live=False), contract_id)
    bars = client.bars(contract.id, start, observed_at, live=False, limit=min(20_000, lookback_minutes + 1))
    positions = tuple(item for item in client.positions(account.id) if item.contract_id == contract.id)
    orders = tuple(item for item in client.orders(account.id, start, observed_at)
                   if item.contract_id == contract.id and item.status in ACTIVE_STATUSES)
    trades = tuple(item for item in client.trades(account.id, start, observed_at) if item.contract_id == contract.id)
    counts = {name: sum(_order_class(order) == name for order in orders)
              for name in ("working_entry_orders", "protective_stop_orders", "protective_target_orders")}
    session = f"shadow-{account.id}"
    observed = ObservedBrokerTelemetry(
        account_id=str(account.id), session_id=session,
        position_quantity=sum(abs(item.size) for item in positions), position_known=True,
        working_entry_orders=counts["working_entry_orders"],
        protective_stop_orders=counts["protective_stop_orders"],
        protective_target_orders=counts["protective_target_orders"],
        orders_known=True, observed_at=observed_at,
    )
    result = reconcile(ExpectedAccountState(
        account_id=str(account.id), session_id=session, position_quantity=0,
        position_known=True, working_entry_orders=0, protective_stop_orders=0,
        protective_target_orders=0, orders_known=True, observed_at=observed_at,
    ), observed)
    report = {
        "schema": "auctionflow.shadow.v1", "status": "BLOCK_ACCOUNT" if result.status != "MATCH" else "NO_TRADE",
        "setup_evaluated": False, "candidate": None, "account": {"id": account.id, "simulated": account.simulated},
        "contract": {"id": contract.id, "symbol_id": contract.symbol_id, "name": contract.name},
        "window": {"start": start.isoformat(), "end": observed_at.isoformat()},
        "bars": [_json(item) for item in bars], "orders": [_json(item) for item in orders],
        "positions": [_json(item) for item in positions], "trades": [_json(item) for item in trades],
        "telemetry": observed.model_dump(mode="json"), "reconciliation": result.model_dump(mode="json"),
    }
    _publish(output, report)
    return report

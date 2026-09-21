from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auctionflow.projectx_http import ProjectXHttp
from auctionflow.shadow import _publish, snapshot

NOW = datetime(2025, 1, 20, 16, 0, tzinfo=UTC)
AUTH = {"token": "one-shot-token", "success": True, "errorCode": 0, "errorMessage": None}
ACCOUNT = {"accounts": [{"id": 101, "name": "SIM", "balance": 1, "canTrade": True, "isVisible": True, "simulated": True}], "success": True, "errorCode": 0, "errorMessage": None}
CONTRACT = {"contracts": [{"id": "MES-ID", "name": "MES", "description": "MES", "tickSize": Decimal("0.25"), "tickValue": Decimal("1.25"), "activeContract": True, "symbolId": "F.US.MES"}], "success": True, "errorCode": 0, "errorMessage": None}
EMPTY = {"success": True, "errorCode": 0, "errorMessage": None}


class FakeTransport:
    def __init__(self, *, positions: list[dict[str, Any]] | None = None, orders: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.responses = {
            "/api/Auth/loginKey": AUTH,
            "/api/Account/search": ACCOUNT,
            "/api/Contract/search": CONTRACT,
            "/api/History/retrieveBars": {"bars": [{"t": "2025-01-20T15:59:00Z", "o": 1, "h": 2, "l": 0, "c": 1, "v": 3}], **EMPTY},
            "/api/Position/searchOpen": {"positions": positions or [], **EMPTY},
            "/api/Order/search": {"orders": orders or [], **EMPTY},
            "/api/Trade/search": {"trades": [], **EMPTY},
        }

    def __call__(self, path: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
        self.calls.append((path, payload, token))
        return self.responses[path]


def client(transport: FakeTransport) -> ProjectXHttp:
    return ProjectXHttp(transport, username="fixed-user", api_key="fixed-secret")


def test_publish_race_preserves_external_destination_and_cleans_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from auctionflow import shadow

    output = tmp_path / "shadow.json"
    external = b"external\n"
    external_inode: list[int] = []

    def racing_link(staged: Path, destination: Path) -> None:
        destination.write_bytes(external)
        external_inode.append(destination.stat().st_ino)
        raise FileExistsError(destination)

    monkeypatch.setattr(shadow.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        _publish(output, {"status": "NO_TRADE"})

    assert output.read_bytes() == external
    assert output.stat().st_ino == external_inode[0]
    assert not list(tmp_path.glob(f".{output.name}.*"))


def test_simulated_mes_snapshot_is_bounded_read_only_and_repeat_is_exclusive(tmp_path: Path) -> None:
    transport = FakeTransport()
    output = tmp_path / "shadow.json"
    report = snapshot(client(transport), account_id=101, contract_id="MES-ID", clock=lambda: NOW, output=output, lookback_minutes=60)
    assert report["status"] == "NO_TRADE"
    assert report["setup_evaluated"] is False
    assert len(report["bars"]) <= 61
    assert len(report["orders"]) <= 60
    assert len(report["positions"]) <= 1
    assert len(report["trades"]) <= 60
    assert report["orders"] == report["positions"] == report["trades"] == []
    assert {path for path, _, _ in transport.calls} == {"/api/Auth/loginKey", "/api/Account/search", "/api/Contract/search", "/api/History/retrieveBars", "/api/Position/searchOpen", "/api/Order/search", "/api/Trade/search"}
    first = output.read_bytes()
    with pytest.raises(FileExistsError):
        snapshot(client(FakeTransport()), account_id=101, contract_id="MES-ID", clock=lambda: NOW, output=output)
    assert output.read_bytes() == first
    assert "fixed-secret" not in output.read_text()


@pytest.mark.parametrize("status", ["MISMATCH", "UNKNOWN"])
def test_nonmatching_reconciliation_blocks_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str) -> None:
    from auctionflow import shadow

    class Result:
        blocking = True
        def __init__(self, value: str) -> None:
            self.status = value
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            return {"status": self.status, "blocking": True}

    monkeypatch.setattr(shadow, "reconcile", lambda expected, observed: Result(status))
    report = snapshot(client(FakeTransport()), account_id=101, contract_id="MES-ID", clock=lambda: NOW, output=tmp_path / f"{status}.json")
    assert report["status"] == "BLOCK_ACCOUNT"


def _order(order_id: int, *, contract_id: str = "MES-ID", status: int = 1, order_type: int = 1, tag: str | None = None) -> dict[str, Any]:
    return {"id": order_id, "accountId": 101, "contractId": contract_id, "symbolId": "F.US.MES", "creationTimestamp": "2025-01-20T15:00:00Z", "updateTimestamp": "2025-01-20T15:00:00Z", "status": status, "type": order_type, "side": 1, "size": 1, "limitPrice": 1, "stopPrice": None, "fillVolume": 0, "filledPrice": None, "customTag": tag}


def test_reconciliation_selects_only_active_contract_orders_and_classifies_them(tmp_path: Path) -> None:
    orders = [_order(1, status=1, order_type=3), _order(2, status=6, order_type=4), _order(3, status=1, order_type=5), _order(4, status=1, tag="take-profit"), _order(5, status=1), _order(6, status=1, order_type=2), _order(7, status=2), _order(8, status=3), _order(9, status=4), _order(10, status=5), _order(11, contract_id="OTHER", status=1, order_type=3)]
    report = snapshot(client(FakeTransport(orders=orders)), account_id=101, contract_id="MES-ID", clock=lambda: NOW, output=tmp_path / "orders.json")
    assert report["status"] == "BLOCK_ACCOUNT"
    assert [order["id"] for order in report["orders"]] == [1, 2, 3, 4, 5, 6]
    telemetry = report["telemetry"]
    assert telemetry["protective_stop_orders"] == 3
    assert telemetry["protective_target_orders"] == 1
    assert telemetry["working_entry_orders"] == 2


def test_every_active_selected_contract_order_class_blocks_empty_reconciliation(tmp_path: Path) -> None:
    for order in (_order(1, order_type=3), _order(2, tag="target"), _order(3)):
        report = snapshot(client(FakeTransport(orders=[order])), account_id=101, contract_id="MES-ID", clock=lambda: NOW, output=tmp_path / f"{order['id']}.json")
        assert report["status"] == "BLOCK_ACCOUNT"


def test_missing_credentials_fail_without_output_or_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TOPSTEPX_USERNAME", raising=False)
    monkeypatch.delenv("TOPSTEPX_API_KEY", raising=False)
    output = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="credentials") as exc:
        snapshot(ProjectXHttp(lambda *_: {}, username=None, api_key=None), account_id=101, contract_id="MES-ID", clock=lambda: NOW, output=output)
    assert not output.exists()
    assert "secret" not in str(exc.value).lower()


def test_shadow_source_has_no_mutation_surface() -> None:
    source = Path(__file__).parents[1] / "src/auctionflow"
    text = (source / "shadow.py").read_text() + (source / "projectx_http.py").read_text()
    tree = ast.parse(text)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr.lower() in {"place", "modify", "cancel", "submit"} for node in ast.walk(tree))
    assert "/api/Order/place" not in text and "/api/Order/modify" not in text and "/api/Order/cancel" not in text

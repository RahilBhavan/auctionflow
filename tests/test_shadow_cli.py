from __future__ import annotations

import json
from pathlib import Path

import pytest

from auctionflow.cli import main

CREATED_AT = "2025-01-20T16:00:00+00:00"


def fixture(path: Path) -> None:
    empty = {"success": True, "errorCode": 0, "errorMessage": None}
    data = {
        "/api/Auth/loginKey": {"token": "fixture-token", **empty},
        "/api/Account/search": {"accounts": [{"id": 101, "name": "SIM", "balance": 1, "canTrade": True, "isVisible": True, "simulated": True}], **empty},
        "/api/Contract/search": {"contracts": [{"id": "MES-ID", "name": "MES", "description": "MES", "tickSize": 0.25, "tickValue": 1.25, "activeContract": True, "symbolId": "F.US.MES"}], **empty},
        "/api/History/retrieveBars": {"bars": [], **empty},
        "/api/Position/searchOpen": {"positions": [], **empty},
        "/api/Order/search": {"orders": [], **empty},
        "/api/Trade/search": {"trades": [], **empty},
    }
    path.write_text(json.dumps(data, sort_keys=True))


def args(tmp_path: Path, fixture_path: Path, *extra: str) -> list[str]:
    return ["shadow-snapshot", "--account-id", "101", "--contract-id", "MES-ID", "--output", str(tmp_path / "shadow.json"), "--transport", "fixture", "--fixture", str(fixture_path), "--created-at", CREATED_AT, *extra]


def test_fixture_shadow_is_deterministic_and_credential_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "fixture.json"
    fixture(path)
    monkeypatch.setenv("TOPSTEPX_USERNAME", "secret")
    monkeypatch.setenv("TOPSTEPX_API_KEY", "secret")
    assert main(args(tmp_path, path)) == 0
    first = (tmp_path / "shadow.json").read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    assert main(args(other, path)) == 0
    assert first == (other / "shadow.json").read_bytes()
    assert b"secret" not in first


@pytest.mark.parametrize("argv", [("--transport", "fixture", "--created-at", CREATED_AT), ("--transport", "fixture", "--fixture", "missing.json")])
def test_fixture_mode_requires_fixture_and_created_at(tmp_path: Path, argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit):
        main(["shadow-snapshot", "--account-id", "101", "--contract-id", "MES-ID", "--output", str(tmp_path / "out"), *argv])
    assert not (tmp_path / "out").exists()


def test_shadow_cli_rejects_invalid_combinations_and_malformed_fixture(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    fixture(path)
    for extra in (("--transport", "projectx", "--fixture", str(path)), ("--transport", "projectx", "--created-at", CREATED_AT)):
        with pytest.raises(SystemExit):
            main(["shadow-snapshot", "--account-id", "101", "--contract-id", "MES-ID", "--output", str(tmp_path / "out"), *extra])
    path.write_text("not json")
    with pytest.raises((SystemExit, ValueError, json.JSONDecodeError)):
        main(args(tmp_path, path))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "content",
    [
        '{"/api/Auth/loginKey": {"success": NaN}}',
        '{"/api/Auth/loginKey": {"success": Infinity}}',
        '{"/api/Auth/loginKey": {"success": -Infinity}}',
        '{"/api/Auth/loginKey": {"nested": {"key": 1, "key": 2}}}',
        '{"/api/Auth/loginKey": 1}',
    ],
)
def test_shadow_fixture_rejects_unsafe_json_and_endpoint_mappings(
    tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "fixture.json"
    path.write_text(content)
    with pytest.raises((SystemExit, RuntimeError)) as raised:
        main(args(tmp_path, path))
    error = capsys.readouterr().err + str(raised.value)
    assert "invalid fixture JSON" in error or "TopstepX authentication request failed" in error
    assert "NaN" not in error and "Infinity" not in error and "duplicate" not in error
    assert not (tmp_path / "shadow.json").exists()


def test_created_at_must_be_utc(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    fixture(path)
    with pytest.raises(SystemExit):
        main(args(tmp_path, path, "--created-at", "2025-01-20T16:00:00"))
    assert not (tmp_path / "shadow.json").exists()


def test_committed_shadow_fixture_gives_no_trade_snapshot(tmp_path: Path) -> None:
    committed = Path(__file__).parent / "fixtures/projectx/shadow_snapshot.json"
    assert main(args(tmp_path, committed)) == 0
    report = json.loads((tmp_path / "shadow.json").read_text())
    assert report["status"] == "NO_TRADE"
    assert report["account"] == {"id": 101, "simulated": True}

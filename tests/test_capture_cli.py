import json
import socket
import urllib.request
from pathlib import Path

import pytest

from auctionflow.cli import main

FIXTURE = Path(__file__).parent / "fixtures/projectx/basic.json"


def invoke(tmp_path, *extra):
    return main(["capture", "--output", str(tmp_path / "capture.jsonl"), "--manifest", str(tmp_path / "manifest.json"),
                 "--transport", "fixture", "--fixture", str(FIXTURE), *extra])


def test_capture_is_deterministic_and_uses_explicit_manifest(tmp_path):
    assert invoke(tmp_path, "--streams", "trades", "--contract-id", "MES") == 0
    first = (tmp_path / "capture.jsonl").read_bytes(), (tmp_path / "manifest.json").read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    assert main(["capture", "--output", str(other / "capture.jsonl"), "--manifest", str(other / "manifest.json"),
                 "--transport", "fixture", "--fixture", str(FIXTURE), "--streams", "trades", "--contract-id", "MES"]) == 0
    assert first == ((other / "capture.jsonl").read_bytes(), (other / "manifest.json").read_bytes())
    assert json.loads((tmp_path / "manifest.json").read_text())["status"] == "CLEAN_SHUTDOWN"


@pytest.mark.parametrize("streams,selector", [("nope", "--contract-id"), ("trades", "--contract-id")])
def test_capture_rejects_invalid_or_missing_selector(tmp_path, streams, selector):
    args = ("--streams", streams)
    if streams == "nope":
        args += (selector, "MES")
    with pytest.raises(SystemExit):
        invoke(tmp_path, *args)
    assert not (tmp_path / "capture.jsonl").exists()


def test_capture_requires_account_for_user_stream(tmp_path):
    with pytest.raises(SystemExit):
        invoke(tmp_path, "--streams", "accounts")
    assert not (tmp_path / "capture.jsonl").exists()


def test_capture_rejects_existing_artifact(tmp_path):
    (tmp_path / "capture.jsonl").write_text("existing")
    with pytest.raises(SystemExit):
        invoke(tmp_path, "--streams", "trades", "--contract-id", "MES")
    assert (tmp_path / "capture.jsonl").read_text() == "existing"


def test_fixture_capture_uses_no_network_or_credentials(tmp_path, monkeypatch):
    secret = "fixture-secret-value"
    monkeypatch.setenv("PROJECTX_USERNAME", secret)
    monkeypatch.setenv("PROJECTX_API_KEY", secret)
    monkeypatch.setenv("PROJECTX_JWT", secret)

    def blocked(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    assert invoke(tmp_path, "--streams", "trades", "--contract-id", "MES") == 0
    for artifact in (tmp_path / "capture.jsonl", tmp_path / "manifest.json"):
        assert secret.encode() not in artifact.read_bytes()


def test_capture_help_is_fixture_only(capsys):
    with pytest.raises(SystemExit):
        main(["capture", "--help"])
    help_text = capsys.readouterr().out.lower()
    assert "fixture" in help_text
    assert "credential" not in help_text
    assert "live" not in help_text
    assert "broker" not in help_text

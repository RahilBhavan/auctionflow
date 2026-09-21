from datetime import UTC, datetime

import pytest

from auctionflow.collector import CollectorRawEnvelope, RawCaptureWriter


def envelope(sequence=0, payload=None, received_at=None):
    return CollectorRawEnvelope(source="x", transport="ws", hub="h", method="m", stream="trades",
        received_at=received_at or datetime(2026, 1, 1, tzinfo=UTC), monotonic_ns=sequence, local_sequence=sequence,
        payload=payload if payload is not None else {"price": 1}, health={"ok": True})


def test_contiguous_and_unknown_payload(tmp_path):
    path = tmp_path / "capture.jsonl"
    with RawCaptureWriter(path, selected_streams=("trades",)) as writer:
        writer.append(envelope(payload=["unknown", {"x": object().__str__()}]))
        writer.append(envelope(1, {"malformed": "retained"}))
    assert writer.count == 2
    assert path.read_text().count("\n") == 2


def test_recursive_secret_redaction():
    item = envelope(payload={"nested": [{"username": "alice", "JWT": "abc", "safe": 4}], "api-key": "x",
                            "clientId": "id", "clientSecret": "secret", "text": "Bearer abc Basic xyz"})
    dumped = item.model_dump_json()
    assert "alice" not in dumped and "abc" not in dumped and '"safe":4' in dumped
    assert "Bearer [REDACTED]" in dumped and "Basic [REDACTED]" in dumped


def test_timezone_validation():
    with pytest.raises(ValueError, match="timezone-aware"):
        envelope(received_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None))


def test_existing_file_refused(tmp_path):
    path = tmp_path / "capture.jsonl"
    path.touch()
    with pytest.raises(FileExistsError): RawCaptureWriter(path)


def test_closed_capture_cannot_be_reused(tmp_path):
    path = tmp_path / "capture.jsonl"
    manifest = tmp_path / "manifest.json"
    with RawCaptureWriter(path, manifest_path=manifest) as writer:
        writer.append(envelope())
    before = path.read_bytes(), manifest.read_bytes()
    inodes = path.stat().st_ino, manifest.stat().st_ino
    with pytest.raises(FileExistsError): RawCaptureWriter(path, manifest_path=manifest)
    assert (path.read_bytes(), manifest.read_bytes()) == before
    assert (path.stat().st_ino, manifest.stat().st_ino) == inodes


def test_append_failure_is_fatal(tmp_path, monkeypatch):
    writer = RawCaptureWriter(tmp_path / "capture.jsonl")
    monkeypatch.setattr(writer._file, "write", lambda _: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(RuntimeError, match="fatal"): writer.append(envelope())
    assert not writer._stage_path.exists()


def test_manifest_publication_failure_rolls_back_capture(tmp_path, monkeypatch):
    import auctionflow.collector as module
    writer = RawCaptureWriter(tmp_path / "capture.jsonl", manifest_path=tmp_path / "manifest.json")
    writer.append(envelope())
    original = module.os.link
    calls = [0]
    def fail_manifest(source, dest):
        calls[0] += 1
        if calls[0] == 2: raise FileExistsError(dest)
        return original(source, dest)
    monkeypatch.setattr(module.os, "link", fail_manifest)
    with pytest.raises(RuntimeError): writer.close()
    assert not (tmp_path / "capture.jsonl").exists() and not (tmp_path / "manifest.json").exists()


def test_same_path_rejected(tmp_path):
    path = tmp_path / "same"
    with pytest.raises(ValueError): RawCaptureWriter(path, manifest_path=path)

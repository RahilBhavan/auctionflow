from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .replay import canonical

SCHEMA = "projectx.raw.v1"
_SECRET_PARTS = ("user", "api", "authorization", "access-token", "access_token", "jwt", "token", "password", "secret", "credential", "clientid", "clientsecret")


def _secret(key: str) -> bool:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in key)
    return any(part in normalized for part in _SECRET_PARTS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 32:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {k: ("[REDACTED]" if _secret(str(k)) else redact(v, _depth=_depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, _depth=_depth + 1) for v in value]
    if isinstance(value, tuple):
        return [redact(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        import re
        value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        return re.sub(r"(?i)basic\s+[A-Za-z0-9._~+/=-]+", "Basic [REDACTED]", value)
    return value


class CollectorRawEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True)
    schema_: str = Field(default=SCHEMA, alias="schema", pattern=r"^projectx\.raw\.v1$")
    source: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    hub: str = Field(min_length=1)
    method: str = Field(min_length=1)
    stream: str = Field(min_length=1)
    received_at: datetime
    monotonic_ns: int = Field(ge=0)
    local_sequence: int = Field(ge=0)
    payload: Any
    health: dict[str, Any] = Field(default_factory=dict)
    @property
    def schema(self) -> str: return self.schema_
    @field_validator("received_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("received_at must be timezone-aware UTC")
        return value
    @field_validator("payload", "health", mode="before")
    @classmethod
    def sanitize(cls, value: Any) -> Any: return redact(value)
    @field_validator("health")
    @classmethod
    def bound_health(cls, value: dict[str, Any]) -> dict[str, Any]: return dict(list(value.items())[:32])

RawCollectorEnvelope = CollectorRawEnvelope


class RawCaptureWriter:
    def __init__(self, path: str | Path, *, selected_streams: tuple[str, ...] = (), manifest_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else self.path.with_suffix(self.path.suffix + ".manifest.json")
        if self.path.resolve() == self.manifest_path.resolve(): raise ValueError("output and manifest must differ")
        if self.path.exists() or self.manifest_path.exists(): raise FileExistsError(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True); self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        self._stage_path = Path(name); self._file = os.fdopen(fd, "wb")
        manifest_fd, manifest_name = tempfile.mkstemp(prefix=f".{self.manifest_path.name}.", dir=self.manifest_path.parent)
        self._stage_manifest = Path(manifest_name); os.close(manifest_fd)
        self._hash = hashlib.sha256(); self.count = 0; self.selected_streams = tuple(sorted(selected_streams)); self._closed = False; self._next_sequence = 0

    def append(self, envelope: CollectorRawEnvelope | Mapping[str, Any]) -> None:
        if self._closed: raise RuntimeError("writer is closed")
        item = CollectorRawEnvelope.model_validate(envelope)
        if item.local_sequence != self._next_sequence: raise ValueError(f"local_sequence must be contiguous at {self._next_sequence}")
        line = (canonical(item.model_dump(mode="json")) + "\n").encode()
        try: self._file.write(line); self._file.flush(); os.fsync(self._file.fileno())
        except Exception as exc: self._abort(); raise RuntimeError("fatal raw capture persistence failure") from exc
        self._hash.update(line); self.count += 1; self._next_sequence += 1

    def _abort(self) -> None:
        try: self._file.close()
        except OSError: pass
        self._stage_path.unlink(missing_ok=True); self._stage_manifest.unlink(missing_ok=True)
        self._closed = True

    def close(self) -> dict[str, Any]:
        if self._closed: raise RuntimeError("writer is closed")
        manifest = {"schema": SCHEMA, "record_count": self.count, "capture_sha256": self._hash.hexdigest(), "selected_streams": list(self.selected_streams), "status": "CLEAN_SHUTDOWN"}
        published: list[tuple[Path, int]] = []
        try:
            self._file.flush(); os.fsync(self._file.fileno()); self._file.close()
            self._stage_manifest.write_text(canonical(manifest) + "\n"); mf = self._stage_manifest.open("rb"); os.fsync(mf.fileno()); mf.close()
            os.link(self._stage_path, self.path); published.append((self.path, self.path.stat().st_ino)); self._stage_path.unlink()
            os.link(self._stage_manifest, self.manifest_path); published.append((self.manifest_path, self.manifest_path.stat().st_ino)); self._stage_manifest.unlink()
            self._closed = True
            return manifest
        except Exception as exc:
            self._abort()
            for path, inode in published:
                try:
                    if path.stat().st_ino == inode: path.unlink()
                except FileNotFoundError: pass
            raise RuntimeError("fatal raw capture persistence failure") from exc

    def __enter__(self) -> Self: return self
    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None: self.close()
        else: self._abort()

CaptureWriter = RawCaptureWriter

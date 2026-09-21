from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "auctionflow.simulator-state.v1"


class StateStoreError(RuntimeError):
    pass


class StateConflict(StateStoreError):
    pass


class StaleState(StateConflict):
    pass


class InvalidPersistence(StateStoreError):
    pass


class SimulatorState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: str = SCHEMA_VERSION
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    token_consumed: bool = False
    opening_attempts: int = Field(default=0, ge=0)
    kill_switch: bool = False
    health_known: bool
    open_position: int = Field(ge=0)
    protective_orders_known: bool
    last_reconciliation_fingerprint: str = ""
    consumed_decision_ids: tuple[str, ...] = ()
    updated_at: datetime

    @field_validator("consumed_decision_ids")
    @classmethod
    def decision_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("consumed decision IDs must be nonempty and unique")
        return value

    @field_validator("updated_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("updated_at must be timezone-aware UTC")
        return value

    @field_validator("account_id", "session_id")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must be nonempty")
        return value


class StateStore:
    def __init__(self, path: str | Path, account_id: str, session_id: str) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.account_id, self.session_id = account_id, session_id

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock_path.open("a+") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                except (AttributeError, OSError) as exc:
                    raise InvalidPersistence("exclusive flock is unsupported") from exc
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise InvalidPersistence("cannot open state lock") from exc

    def initialize(self, *, updated_at: datetime) -> SimulatorState:
        state = SimulatorState(account_id=self.account_id, session_id=self.session_id, revision=0,
            health_known=False, open_position=0, protective_orders_known=False, updated_at=updated_at)
        with self._lock():
            if self.path.exists():
                raise StateConflict("state already initialized")
            self._persist(state)
        return state

    def load(self) -> SimulatorState:
        try:
            value = json.loads(self.path.read_bytes())
            if (not isinstance(value, dict) or "schema_version" not in value
                    or value["schema_version"] != SCHEMA_VERSION
                    or set(value) != set(SimulatorState.model_fields)):
                raise ValueError("state schema is not exact")
            state = SimulatorState.model_validate_json(self.path.read_bytes())
        except Exception as exc:
            raise InvalidPersistence("state is missing, corrupt, or invalid") from exc
        if state.account_id != self.account_id or state.session_id != self.session_id:
            raise InvalidPersistence("account/session mismatch")
        if state.schema_version != SCHEMA_VERSION:
            raise InvalidPersistence("unsupported state schema")
        return state

    def authorize_once(self, decision_id: str, expected_revision: int,
                       expected_reconciliation_fingerprint: str, updated_at: datetime) -> SimulatorState:
        if not decision_id.strip() or not expected_reconciliation_fingerprint.strip():
            raise StateConflict("decision_id and reconciliation fingerprint are required")
        with self._lock():
            state = self.load()
            self._check_revision(state, expected_revision)
            if state.last_reconciliation_fingerprint != expected_reconciliation_fingerprint:
                raise StaleState("reconciliation fingerprint is stale")
            if decision_id in state.consumed_decision_ids: raise StateConflict("duplicate decision id")
            if state.kill_switch: raise StateConflict("kill switch is set")
            if not state.health_known or not state.protective_orders_known: raise StateConflict("reconciliation is unknown")
            if state.open_position != 0: raise StateConflict("open position is nonzero")
            if state.token_consumed or state.opening_attempts: raise StateConflict("one-shot token already consumed")
            new = self._updated(state, revision=state.revision + 1, token_consumed=True,
                opening_attempts=state.opening_attempts + 1, consumed_decision_ids=(*state.consumed_decision_ids, decision_id), updated_at=updated_at)
            self._persist(new)
            return new

    def reconcile(self, *, expected_revision: int, health_known: bool, open_position: int,
                  protective_orders_known: bool, fingerprint: str, updated_at: datetime) -> SimulatorState:
        if not fingerprint.strip(): raise StateConflict("reconciliation fingerprint is required")
        with self._lock():
            state = self.load(); self._check_revision(state, expected_revision)
            new = self._updated(state, revision=state.revision + 1, health_known=health_known,
                open_position=open_position, protective_orders_known=protective_orders_known,
                last_reconciliation_fingerprint=fingerprint, updated_at=updated_at)
            self._persist(new); return new

    def set_kill_switch(self, *, expected_revision: int, updated_at: datetime) -> SimulatorState:
        return self._kill(expected_revision, True, updated_at)

    def clear_kill_switch(self, *, expected_revision: int, updated_at: datetime) -> SimulatorState:
        return self._kill(expected_revision, False, updated_at)

    def _kill(self, expected_revision: int, value: bool, updated_at: datetime) -> SimulatorState:
        with self._lock():
            state = self.load(); self._check_revision(state, expected_revision)
            if state.kill_switch == value:
                raise StateConflict("kill switch transition is already in requested state")
            new = self._updated(state, revision=state.revision + 1, kill_switch=value, updated_at=updated_at)
            self._persist(new); return new

    @staticmethod
    def _updated(state: SimulatorState, **changes: object) -> SimulatorState:
        value = state.model_dump(mode="python")
        value.update(changes)
        return SimulatorState.model_validate(value)

    @staticmethod
    def _check_revision(state: SimulatorState, expected: int) -> None:
        if state.revision != expected: raise StaleState("revision is stale")

    def _persist(self, state: SimulatorState) -> None:
        data = json.dumps(state.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = self.path.read_bytes() if self.path.exists() else None
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        replaced = False
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(name, self.path); replaced = True
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        except Exception as exc:
            try: os.unlink(name)
            except OSError: pass
            if not replaced:
                raise InvalidPersistence("atomic state persistence failed") from exc
            try:
                if previous is None:
                    self.path.unlink(missing_ok=True)
                else:
                    rfd, restore = tempfile.mkstemp(prefix=f".{self.path.name}.rollback.", dir=self.path.parent)
                    with os.fdopen(rfd, "wb") as f:
                        f.write(previous); f.flush(); os.fsync(f.fileno())
                    os.replace(restore, self.path)
                    dfd = os.open(self.path.parent, os.O_RDONLY)
                    try: os.fsync(dfd)
                    finally: os.close(dfd)
            except Exception as rollback_exc:
                raise InvalidPersistence("state outcome is uncertain; fail closed") from rollback_exc
            raise InvalidPersistence("atomic state persistence failed; previous state restored") from exc
        finally:
            try: os.unlink(name)
            except OSError: pass

import json
import multiprocessing
from datetime import UTC, datetime, timedelta, timezone

import pytest

from auctionflow.state_store import InvalidPersistence, StaleState, StateConflict, StateStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ready(store):
    state = store.initialize(updated_at=T0)
    return store.reconcile(expected_revision=state.revision, health_known=True, open_position=0,
                           protective_orders_known=True, fingerprint="r1", updated_at=T0)


def worker(path, result):
    store = StateStore(path, "a", "s")
    try:
        store.authorize_once("worker", 1, "r1", T0)
        result.put(True)
    except StateConflict:
        result.put(False)


def test_initialize_load_and_one_shot(tmp_path):
    store = StateStore(tmp_path / "state.json", "a", "s")
    state = ready(store)
    assert store.load() == state
    done = store.authorize_once("d1", state.revision, "r1", T0)
    assert done.token_consumed and done.opening_attempts == 1
    with pytest.raises(StateConflict): store.authorize_once("d2", done.revision, "r1", T0)
    assert StateStore(store.path, "a", "s").load().token_consumed


def test_conflicts_and_kill_switch(tmp_path):
    store = StateStore(tmp_path / "state.json", "a", "s")
    state = ready(store)
    with pytest.raises(StaleState): store.reconcile(expected_revision=0, health_known=True, open_position=0, protective_orders_known=True, fingerprint="x", updated_at=T0)
    with pytest.raises(StaleState): store.authorize_once("x", state.revision, "wrong", T0)
    killed = store.set_kill_switch(expected_revision=state.revision, updated_at=T0)
    with pytest.raises(StateConflict): store.authorize_once("x", killed.revision, "r1", T0)
    with pytest.raises(StaleState): store.clear_kill_switch(expected_revision=state.revision, updated_at=T0)
    cleared = store.clear_kill_switch(expected_revision=killed.revision, updated_at=T0)
    assert not cleared.kill_switch


@pytest.mark.parametrize("stamp", [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))])  # noqa: DTZ001
def test_naive_or_non_utc_rejected(tmp_path, stamp):
    store = StateStore(tmp_path / "state.json", "a", "s")
    with pytest.raises((TypeError, ValueError)):
        store.initialize(updated_at=stamp)


@pytest.mark.parametrize("field,bad", [("revision", 1.0), ("revision", "0"), ("token_consumed", "true"), ("health_known", 1), ("open_position", "0"), ("opening_attempts", "0"), ("updated_at", 7)])
def test_tampered_strict_types_fail_closed(tmp_path, field, bad):
    store = StateStore(tmp_path / "state.json", "a", "s"); ready(store)
    value = json.loads(store.path.read_text()); value[field] = bad; store.path.write_text(json.dumps(value))
    with pytest.raises(InvalidPersistence): store.load()


def test_kill_switch_noop_rejected(tmp_path):
    store = StateStore(tmp_path / "state.json", "a", "s"); state = ready(store)
    with pytest.raises(StateConflict): store.clear_kill_switch(expected_revision=state.revision, updated_at=T0)
    state = store.set_kill_switch(expected_revision=state.revision, updated_at=T0)
    with pytest.raises(StateConflict): store.set_kill_switch(expected_revision=state.revision, updated_at=T0)


def test_invalid_persisted_schema_and_identity(tmp_path):
    path = tmp_path / "state.json"; store = StateStore(path, "a", "s"); ready(store)
    value = json.loads(path.read_text())
    for bad in ({k: v for k, v in value.items() if k != "schema_version"}, {**value, "schema_version": "wrong"}, {**value, "account_id": "other"}, {**value, "consumed_decision_ids": ["", ""]}, {**value, "open_position": -1}):
        path.write_text(json.dumps(bad))
        with pytest.raises(InvalidPersistence): store.load()
        path.write_bytes(json.dumps(value).encode())


def test_unknown_and_nonzero_position(tmp_path):
    store = StateStore(tmp_path / "state.json", "a", "s"); state = store.initialize(updated_at=T0)
    with pytest.raises(StateConflict): store.authorize_once("x", state.revision, "", T0)
    state = store.reconcile(expected_revision=state.revision, health_known=False, open_position=0, protective_orders_known=False, fingerprint="u", updated_at=T0)
    with pytest.raises(StateConflict): store.authorize_once("x", state.revision, "u", T0)
    state = store.reconcile(expected_revision=state.revision, health_known=True, open_position=1, protective_orders_known=True, fingerprint="p", updated_at=T0)
    with pytest.raises(StateConflict): store.authorize_once("x", state.revision, "p", T0)


def test_duplicate_ids_and_corrupt_fail_closed(tmp_path):
    store = StateStore(tmp_path / "state.json", "a", "s"); state = ready(store)
    with pytest.raises(StateConflict): store.authorize_once(" ", state.revision, "r1", T0)
    done = store.authorize_once("d", state.revision, "r1", T0)
    with pytest.raises(StateConflict): store.authorize_once("d", done.revision, "r1", T0)
    store.path.write_bytes(b"{")
    with pytest.raises(InvalidPersistence): store.load()


def test_multiprocess_exactly_one(tmp_path):
    store = StateStore(tmp_path / "state.json", "a", "s"); ready(store)
    result = multiprocessing.Queue(); processes = [multiprocessing.Process(target=worker, args=(store.path, result)) for _ in range(2)]
    for process in processes: process.start()
    for process in processes: process.join(5)
    assert all(not process.is_alive() for process in processes)
    assert [result.get(timeout=1) for _ in processes].count(True) == 1


def test_pre_replace_failure_preserves_state(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json", "a", "s"); old = ready(store); before = store.path.read_bytes()
    monkeypatch.setattr("auctionflow.state_store.os.fsync", lambda _: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(InvalidPersistence): store.set_kill_switch(expected_revision=old.revision, updated_at=T0)
    assert store.path.read_bytes() == before

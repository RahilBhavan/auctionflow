from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import TradingConfig
from .decision import evaluate
from .domain import AccountState, StrategyState
from .session import session_id
from .strategies import RESEARCH_DEFAULT_STRATEGY_CONFIG


class ReplayError(ValueError):
    """Input cannot be replayed safely."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def snapshot_ref(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def replay(input_path: str | Path, decisions_path: str | Path, account: AccountState | None = None,
           config: TradingConfig | None = None, strategy_config=None,
           raw_path: str | Path | None = None, parquet_path: str | Path | None = None,
           normalized_parquet_path: str | Path | None = None) -> str:
    config = config or TradingConfig()
    strategy_config = strategy_config or RESEARCH_DEFAULT_STRATEGY_CONFIG
    lines = Path(input_path).read_text().splitlines()
    if account is None:
        try:
            first = json.loads(lines[0])
            from datetime import datetime
            sid = session_id(datetime.fromisoformat(first["timestamp"]), config)
        except (ValueError, TypeError, KeyError, IndexError):
            from datetime import UTC, datetime
            sid = session_id(datetime(1970, 1, 1, tzinfo=UTC), config)
        account = AccountState(account_id="replay", session_id=sid,
                               balance_cents=config.account_size_cents,
                               mll_floor_cents=config.account_size_cents - config.mll_trailing_cushion_cents)
    strategy = StrategyState()
    observations: list[Any] = []
    output: list[str] = []
    raw: list[str] = []
    normalized: list[dict[str, Any]] = []
    previous_sequence: int | None = None
    for number, line in enumerate(lines, 1):
        raw.append(line)
        payload: Any = None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("event must be a JSON object")
            sequence = payload.get("sequence")
            if previous_sequence is not None and (sequence is None or sequence != previous_sequence + 1):
                raise ReplayError(f"unresolved sequence gap after {previous_sequence}")
            result = evaluate(payload, account, config, strategy=strategy,
                              strategy_config=strategy_config)
            account, strategy = result.account, result.strategy
            observations.append(payload)
            record = result.record.model_dump(mode="json")
            record.update(account_snapshot_ref=snapshot_ref(account.model_dump(mode="json")),
                          strategy_snapshot=strategy.model_dump(mode="json"), checkpoint=number)
            output.append(canonical(record))
            normalized.append({**payload, "checkpoint": number, "strategy_state": strategy.state})
            if sequence is not None:
                previous_sequence = sequence
        except Exception as exc:
            event_id = payload.get("event_id") if isinstance(payload, dict) else None
            failure = {"status": "FAILED", "line": number, "event_id": event_id,
                       "error": str(exc), "prior_decisions": len(output),
                       "raw_input": line}
            output.append(canonical(failure))
            Path(decisions_path).write_text("\n".join(output) + "\n")
            if raw_path and Path(raw_path).resolve() != Path(input_path).resolve():
                Path(raw_path).write_text("\n".join(raw) + "\n")
            raise ReplayError(f"line {number}, event_id {event_id}: {exc}") from exc
    decision_bytes = ("\n".join(output) + ("\n" if output else "")).encode()
    Path(decisions_path).write_bytes(decision_bytes)
    if raw_path and Path(raw_path).resolve() != Path(input_path).resolve():
        Path(raw_path).write_text("\n".join(raw) + ("\n" if raw else ""))
    if parquet_path or normalized_parquet_path:
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.Table.from_pylist([json.loads(row) for row in output])
        if parquet_path:
            pq.write_table(table, parquet_path, compression="zstd")
        if normalized_parquet_path:
            pq.write_table(pa.Table.from_pylist(normalized), normalized_parquet_path, compression="zstd")
    return hashlib.sha256(decision_bytes).hexdigest()


def verify_determinism(input_path: str | Path, decisions_path: str | Path, **kwargs) -> bool:
    replay(input_path, decisions_path, **kwargs)
    expected = Path(decisions_path).read_bytes()
    other = Path(str(decisions_path) + ".determinism")
    try:
        replay(input_path, other, **kwargs)
        if expected != other.read_bytes():
            raise ReplayError("determinism mismatch")
    finally:
        other.unlink(missing_ok=True)
    return True

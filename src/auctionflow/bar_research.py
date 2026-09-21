from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import TradingConfig
from .databento import file_sha256, load_databento_mes, read_databento_mes_bars
from .historical import (
    group_bars_by_et_date,
    read_firstrate_bars,
    read_portara_bars,
    read_portara_trade_ticks_as_minute_bars,
)
from .ib_breakout import (
    BarStrategyConfig,
    FailedIBBreakoutConfig,
    LiquiditySweepConfig,
    evaluate_failed_ib_breakout,
    evaluate_ib_session,
    evaluate_liquidity_sweep_reclaim,
)
from .research import canonical_json, sha256

SCHEMA_VERSION = "1"
MODE = "BAR_OHLC_RESEARCH_ONLY"


def _created(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("created-at must be ISO 8601") from exc
    if dt.tzinfo is None:
        raise ValueError("created-at must include timezone")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _partition(i: int, n: int) -> str:
    if n < 3:
        return "TRAIN" if i < n else "TEST"
    a, b = max(1, int(n * .6)), max(2, int(n * .8))
    b = min(n - 1, b)
    a = min(a, b - 1)
    return "TRAIN" if i < a else "VALIDATION" if i < b else "TEST"


def _aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"sessions": len(items), "valid": sum(x["status"] != "DATA_INVALID" for x in items),
            "gross_cents": sum(x["gross_cents"] for x in items), "costs_cents": sum(x["costs_cents"] for x in items),
            "net_cents": sum(x["net_cents"] for x in items),
            "status": dict(sorted(Counter(x["status"] for x in items).items()))}


def _metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [x for x in items if x["status"] == "ACCEPTED"]
    wins = sum(x["label"] == "TARGET_FIRST" for x in accepted)
    gross_wins = sum(x["gross_cents"] for x in accepted if x["gross_cents"] > 0)
    gross_losses = -sum(x["gross_cents"] for x in accepted if x["gross_cents"] < 0)
    equity = peak = drawdown = 0
    for row in accepted:
        equity += row["net_cents"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "accepted_trades": len(accepted), "no_trade_sessions": sum(x["status"] == "NO_TRADE" for x in items),
        "outcome_counts": dict(sorted(Counter(x["label"] for x in accepted).items())),
        "expectancy_per_accepted_trade_cents": (sum(x["net_cents"] for x in accepted) / len(accepted) if accepted else None),
        "win_rate": (wins / len(accepted) if accepted else None),
        "profit_factor": (gross_wins / gross_losses if gross_losses else None),
        "maximum_cumulative_drawdown_cents": drawdown,
        "double_cost_net_cents": sum(x["gross_cents"] - 2 * x["costs_cents"] for x in accepted),
    }


def run_bar_backtest(input_path: str | Path, report_path: str | Path, sessions_path: str | Path,
                     experiment_id: str, created_at: str, commission_cents_per_contract: int = 122,
                     slippage_cents_per_contract: int = 125, format: str = "firstrate-mes-1m",
                     strategy: str = "ib_acceptance", evaluation_ceiling: str = "ALL", **strategy_params) -> dict[str, Any]:
    if commission_cents_per_contract < 0 or slippage_cents_per_contract < 0:
        raise ValueError("costs must be nonnegative")
    if evaluation_ceiling not in {"TRAIN", "VALIDATION", "ALL"}:
        raise ValueError("evaluation ceiling must be TRAIN, VALIDATION, or ALL")
    report_path, sessions_path, input_path = map(Path, (report_path, sessions_path, input_path))
    if report_path.resolve() == sessions_path.resolve():
        raise ValueError("report and sessions must differ")
    if report_path.exists() or sessions_path.exists():
        raise ValueError("outputs must not already exist")
    created = _created(created_at)
    readers = {
        "firstrate-mes-1m": read_firstrate_bars,
        "portara-mes-1m": read_portara_bars,
        "portara-mes-trades": read_portara_trade_ticks_as_minute_bars,
        "databento-glbx-mes-ohlcv-1m": read_databento_mes_bars,
    }
    databento_load = load_databento_mes(input_path) if format == "databento-glbx-mes-ohlcv-1m" else None
    databento_info = databento_load.inspection if databento_load is not None else None
    try:
        bars = (databento_load.bars if databento_load is not None else readers[format](input_path))
    except KeyError as exc:
        raise ValueError(f"unsupported bar format: {format}") from exc
    tc = TradingConfig(commission_cents_per_contract=commission_cents_per_contract,
                       slippage_cents_per_contract=slippage_cents_per_contract)
    config_types = {"ib_acceptance": BarStrategyConfig, "failed_ib": FailedIBBreakoutConfig,
                    "liquidity_sweep": LiquiditySweepConfig}
    if strategy not in config_types:
        raise ValueError(f"unsupported strategy: {strategy}")
    allowed = set(config_types[strategy].model_fields)
    unknown = set(strategy_params) - allowed
    if unknown:
        raise ValueError(f"unsupported strategy parameters: {sorted(unknown)}")
    config = config_types[strategy](**strategy_params)
    groups = sorted(group_bars_by_et_date(bars).items())
    references = databento_load.session_references if databento_load is not None else {}
    if strategy == "ib_acceptance":
        evaluate = lambda group, day: evaluate_ib_session(group, tc, config)
    elif strategy == "failed_ib":
        evaluate = lambda group, day: evaluate_failed_ib_breakout(group, tc, config)
    else:
        evaluate = lambda group, day: evaluate_liquidity_sweep_reclaim(group, references.get(day), tc, config)
    results = [evaluate(group, day) for day, group in groups]
    valid = [r for r in results if r.status != "DATA_INVALID"]
    records = []
    for i, result in enumerate(results):
        d = result.model_dump(mode="json")
        d["partition"] = "DATA_INVALID" if result.status == "DATA_INVALID" else _partition(sum(x.status != "DATA_INVALID" for x in results[:i]), len(valid))
        records.append(d)
    visible_partitions = {"TRAIN"} if evaluation_ceiling == "TRAIN" else {"TRAIN", "VALIDATION"} if evaluation_ceiling == "VALIDATION" else {"TRAIN", "VALIDATION", "TEST"}
    records = [x for x in records if x["partition"] == "DATA_INVALID" or x["partition"] in visible_partitions]
    lines = b"".join(canonical_json(x) + b"\n" for x in records)
    sessions_hash = sha256(lines)
    by_partition = {p: {**_aggregate([x for x in records if x["partition"] == p]), **_metrics([x for x in records if x["partition"] == p])} for p in ("TRAIN", "VALIDATION", "TEST", "DATA_INVALID") if p in visible_partitions or p == "DATA_INVALID"}
    years = {x["session"][:4] for x in records if x["status"] != "DATA_INVALID"}
    per_year = {year: _metrics([x for x in records if x["status"] != "DATA_INVALID" and x["session"][:4] == year]) for year in sorted(years)}
    net_total = sum(x["net_cents"] for x in records if x["status"] != "DATA_INVALID")
    concentration = {"year_net_share": {year: (sum(x["net_cents"] for x in records if x["status"] != "DATA_INVALID" and x["session"][:4] == year) / net_total if net_total else None) for year in sorted(years)}}
    source = {"path": input_path.name, "format": format, "size_bytes": input_path.stat().st_size}
    if format == "databento-glbx-mes-ohlcv-1m":
        meta = input_path.with_name("metadata.json")
        if meta.exists():
            source["metadata"] = json.loads(meta.read_text(encoding="utf-8"))
        info = databento_info
        source.update({"input_sha256": info["sha256"], "counts": info["counts"],
                       "date_coverage": [d.isoformat() for d in info["dates"]],
                       "selection_policy": "highest outright volume on immediately previous represented America/New_York date; symbol tie-break; first date excluded"})
    report = {"schema_version": SCHEMA_VERSION, "experiment_id": experiment_id, "created_at": created,
              "mode": MODE, "disclaimer": "BAR_OHLC_RESEARCH_ONLY; pipeline evidence only, not promotion or profitability evidence.",
              "continuous_series_caveat": "Continuous FirstRate series are included when contract_id is absent; no contract-roll adjustment is applied, and bar OHLC cannot establish intrabar ordering.",
              "data_warning": {"continuous_series_sessions": sum(x["continuous"] for x in records), "contract_roll_adjustment": False},
              "source": source, "input_sha256": (databento_info["sha256"] if databento_info is not None else file_sha256(input_path)),
              "strategy_name": strategy, "evaluation_ceiling": evaluation_ceiling,
              "config_revisions": {"trading": tc.revision, "strategy": config.revision},
              "reference_policy": "databento_session_references_only_for_liquidity_sweep",
              "reference_missing_counts": {"sessions": sum(1 for day, _ in groups if day not in references), "fields": sum(sum(v is None for v in (ref.prior_rth_high_ticks, ref.prior_rth_low_ticks, ref.overnight_high_ticks, ref.overnight_low_ticks)) for ref in references.values())},

              "cost_assumptions": {"commission_cents_per_contract": tc.commission_cents_per_contract, "slippage_cents_per_contract": tc.slippage_cents_per_contract, "description": "Commission and slippage are round-trip costs per contract; explicitly set both to zero for a frictionless control."},
              "sessions_sha256": sessions_hash,
              "content_hash_scheme": "SHA256 canonical JSON (sorted keys, compact separators); sessions hash covers canonical JSONL; report hash excludes itself",
              "counts": {"sessions": len(results), "valid_sessions": len(valid), "invalid_sessions": len(results)-len(valid),
                         "status": dict(sorted(Counter(x["status"] for x in records).items())),
                         "reason": dict(sorted(Counter(x["reason"] for x in records).items())),
                         "label": dict(sorted(Counter(x["label"] for x in records).items())),
                         "direction": dict(sorted(Counter(str(x["direction"]) for x in records).items())),
                         "quantity": dict(sorted(Counter(str(x["quantity"]) for x in records).items()))},
              "totals": {**_aggregate(records), **_metrics(records)}, "partitions": by_partition, "chronological": [x["session"] for x in records],
              "per_year": per_year, "concentration": concentration,
              "negative_controls": {"NO_TRADE": {"description": "Safety/reference control with zero openings; not evidence of edge.", "valid_sessions": len(valid), "gross_cents": 0, "costs_cents": 0, "net_cents": 0}},
              "strategy": {"net_cents": sum(x["net_cents"] for x in records if x["status"] != "DATA_INVALID"), "net_delta_cents": sum(x["net_cents"] for x in records if x["status"] != "DATA_INVALID")},
              "sensitivity": {"transaction_cost_multiplier": 2, "description": "Sensitivity analysis, not a negative control.", "gross_cents": sum(x["gross_cents"] for x in records if x["status"] != "DATA_INVALID"), "costs_cents": sum(x["costs_cents"] for x in records if x["status"] != "DATA_INVALID") * 2, "net_cents": sum(x["gross_cents"] - 2 * x["costs_cents"] for x in records if x["status"] != "DATA_INVALID")},
              "strategy_config_revision": config.revision, "trading_config_revision": tc.revision}
    report["report_sha256"] = sha256(canonical_json(report))
    report_bytes = canonical_json(report) + b"\n"
    for dest, data in ((sessions_path, lines), (report_path, report_bytes)):
        dest.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    published = []
    try:
        for dest, data in ((sessions_path, lines), (report_path, report_bytes)):
            fd, name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            staged.append((Path(name), dest))
        for tmp, dest in staged:
            os.link(tmp, dest); published.append((dest, dest.stat().st_ino)); tmp.unlink()
    except Exception:
        for tmp, _ in staged: tmp.unlink(missing_ok=True)
        for dest, inode in published:
            try:
                if dest.stat().st_ino == inode: dest.unlink()
            except FileNotFoundError: pass
        raise
    return report

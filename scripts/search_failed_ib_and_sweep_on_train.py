"""Train-split parameter search for the failed_ib and liquidity_sweep strategies (licensed Databento input)."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from auctionflow.bar_research import _metrics
from auctionflow.config import TradingConfig
from auctionflow.databento import load_databento_mes
from auctionflow.historical import group_bars_by_et_date
from auctionflow.ib_breakout import (
    FailedIBBreakoutConfig,
    LiquiditySweepConfig,
    evaluate_failed_ib_breakout,
    evaluate_liquidity_sweep_reclaim,
)
from auctionflow.research import canonical_json, sha256

# Deliberately small, predeclared grids: 16 configs per strategy, 32 total.
CONFIGS = {
    "failed_ib": [
        FailedIBBreakoutConfig(reclaim_window=w, excursion_ticks=e, stop_offset_ticks=s)
        for w in (2, 3, 4, 5)
        for e in (1, 2)
        for s in (1, 2)
    ],
    "liquidity_sweep": [
        LiquiditySweepConfig(reclaim_window=w, excursion_ticks=e, stop_offset_ticks=s)
        for w in (2, 3, 4, 5)
        for e in (1, 2)
        for s in (1, 2)
    ],
}


def train_groups(bars):
    groups = sorted(group_bars_by_et_date(bars).items())
    valid = [item for item in groups if item[1]]
    cutoff = max(1, int(len(valid) * 0.6))
    return valid[:cutoff]


def evaluate(strategy, config, groups, references, trading):
    rows = []
    for day, bars in groups:
        if strategy == "failed_ib":
            result = evaluate_failed_ib_breakout(bars, trading, config)
        else:
            result = evaluate_liquidity_sweep_reclaim(bars, references.get(day), trading, config)
        rows.append(result.model_dump(mode="json"))
    metrics = _metrics(rows)
    return {
        "strategy": strategy,
        "config": config.model_dump(mode="json"),
        "strategy_config_revision": config.revision,
        "metrics": metrics,
        "concentration": {
            "year_net_share": {
                year: sum(r["net_cents"] for r in rows if r["session"][:4] == year)
                / (sum(r["net_cents"] for r in rows) or 1)
                for year in sorted({r["session"][:4] for r in rows})
            }
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)

    loaded = load_databento_mes(args.input)
    groups = train_groups(loaded.bars)
    # TRAIN is the only partition materialized for evaluation; later partitions
    # are intentionally not grouped, inspected, or passed to strategy evaluators.
    trading = TradingConfig(commission_cents_per_contract=122, slippage_cents_per_contract=125)
    results = [
        evaluate(strategy, config, groups, loaded.session_references, trading)
        for strategy, configs in CONFIGS.items()
        for config in configs
    ]
    assert len(results) <= 64
    eligible = [
        r for r in results
        if r["metrics"]["accepted_trades"] >= 20
        and r["metrics"]["double_cost_net_cents"] > 0
        and (r["metrics"]["profit_factor"] or 0) > 1
        and r["metrics"]["maximum_cumulative_drawdown_cents"] <= 200_000
    ]
    eligible.sort(key=lambda r: (r["metrics"]["double_cost_net_cents"], r["metrics"]["profit_factor"] or 0), reverse=True)
    summary = {
        "schema_version": "task30-train-summary-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "input_sha256": loaded.inspection["sha256"],
        "evaluation_ceiling": "TRAIN",
        "cost_assumptions": {"commission_cents_per_contract": 122, "slippage_cents_per_contract": 125},
        "search": {"strategies": list(CONFIGS), "combinations": len(results), "eligible": len(eligible)},
        "train_sessions": len(groups),
        "top_train_candidates": eligible[:10],
    }
    payload = canonical_json(summary)
    summary["summary_sha256"] = sha256(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json(summary) + b"\n")
    print(json.dumps({"output": str(args.output), "summary_sha256": summary["summary_sha256"], "top_train_candidates": eligible[:10]}, sort_keys=True))


if __name__ == "__main__":
    main()

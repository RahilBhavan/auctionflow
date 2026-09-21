from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from auctionflow.bar_research import _metrics
from auctionflow.config import TradingConfig
from auctionflow.databento import load_databento_mes
from auctionflow.historical import group_bars_by_et_date
from auctionflow.research import canonical_json, sha256


@dataclass(frozen=True)
class DriveConfig:
    range_minutes: int
    body_fraction: float
    target_r: float


@dataclass(frozen=True)
class VwapConfig:
    displacement_ticks: int
    reclaim_window: int
    quiet_gate: bool
    target_r: float


@dataclass(frozen=True)
class PriorRangeConfig:
    displacement_ticks: int
    confirmation_bars: int
    entry_cutoff: int
    body_fraction: float
    vwap_alignment: bool
    volume_fraction: float
    direction_mode: str
    width_gate: str
    target_r: float


DRIVE_CONFIGS = [DriveConfig(m, f, 1.5) for m in (5, 10, 15) for f in (.5, .7)]
VWAP_CONFIGS = [VwapConfig(d, w, q, 1.5) for d in (2, 4) for w in (1, 3) for q in (False, True)]
PRIOR_RANGE_CONFIGS = [PriorRangeConfig(1, 2, 10, .5, False, volume, direction, width, target) for direction in ("both", "long", "short") for width in ("all", "below_median", "above_median") for volume in (1.0, 1.5) for target in (1.5, 2.5)]
CONFIGS = {"prior_rth_continuation": PRIOR_RANGE_CONFIGS}
ET = ZoneInfo("America/New_York")
TRAIN_END = date(2023, 9, 6)
VALIDATION_END = date(2025, 2, 19)


def partition_groups(bars):
    groups = [(day, rows) for day, rows in sorted(group_bars_by_et_date(bars).items()) if rows]
    train = [(day, rows) for day, rows in groups if day <= TRAIN_END]
    validation = [(day, rows) for day, rows in groups if TRAIN_END < day <= VALIDATION_END]
    test = [(day, rows) for day, rows in groups if day > VALIDATION_END]
    return train, validation, test


def train_groups(bars):
    return partition_groups(bars)[0]


def expanding_prior_width_median(widths):
    return sorted(widths)[len(widths) // 2] if widths else 0


def _trade(day, bars, direction, signal_i, stop, target_r, tc, cfg):
    entry_i = signal_i + 1
    if entry_i >= len(bars):
        return {"session": day.isoformat(), "status": "NO_TRADE", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}
    entry = bars[entry_i].open_ticks
    if (direction == "long" and stop >= entry) or (direction == "short" and stop <= entry):
        return {"session": day.isoformat(), "status": "REJECTED", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}
    risk = abs(entry - stop)
    target = round(entry + target_r * risk) if direction == "long" else round(entry - target_r * risk)
    label, gross = "SESSION_EXIT", (bars[-1].close_ticks - entry if direction == "long" else entry - bars[-1].close_ticks)
    for bar in bars[entry_i:]:
        hit_stop = bar.low_ticks <= stop if direction == "long" else bar.high_ticks >= stop
        hit_target = bar.high_ticks >= target if direction == "long" else bar.low_ticks <= target
        if hit_stop and hit_target:
            label, gross = "AMBIGUOUS", 0
            break
        if hit_target:
            label, gross = "TARGET_FIRST", abs(target - entry)
            break
        if hit_stop:
            label, gross = "STOP_FIRST", -risk
            break
    costs = tc.commission_cents_per_contract + tc.slippage_cents_per_contract
    return {"session": day.isoformat(), "status": "ACCEPTED", "label": label, "gross_cents": gross * tc.mes_tick_value_cents, "costs_cents": costs, "net_cents": 0 if label == "AMBIGUOUS" else gross * tc.mes_tick_value_cents - costs}


def opening_range_drive(bars, cfg, tc):
    start = [b for b in bars if time(9, 30) <= b.timestamp.astimezone(ET).time() < time(11, 30)]
    if len(start) < 16: return {"session": bars[0].et_date.isoformat(), "status": "DATA_INVALID", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}
    n = cfg.range_minutes; opening = start[:n]; hi, lo = max(x.high_ticks for x in opening), min(x.low_ticks for x in opening)
    for i, b in enumerate(start[n:-1], n):
        tr = max(b.high_ticks - b.low_ticks, 1); body = abs(b.close_ticks - b.open_ticks)
        if body / tr < cfg.body_fraction: continue
        if b.close_ticks > hi: return _trade(b.et_date, start, "long", i, min(x.low_ticks for x in start[max(n, i-2):i+1]), cfg.target_r, tc, cfg)
        if b.close_ticks < lo: return _trade(b.et_date, start, "short", i, max(x.high_ticks for x in start[max(n, i-2):i+1]), cfg.target_r, tc, cfg)
    return {"session": bars[0].et_date.isoformat(), "status": "NO_TRADE", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}


def session_vwap_reversion(bars, cfg, tc):
    start = [b for b in bars if time(9, 30) <= b.timestamp.astimezone(ET).time() < time(11, 30)]
    if len(start) < 3: return {"session": bars[0].et_date.isoformat(), "status": "DATA_INVALID", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}
    pv = vol = 0.0; prior_vwap = None; prior_tr = []
    for i, b in enumerate(start[:-1]):
        if prior_vwap is not None:
            disp = abs(b.close_ticks - prior_vwap)
            quiet = not cfg.quiet_gate or (len(prior_tr) >= 3 and b.high_ticks - b.low_ticks <= sorted(prior_tr)[len(prior_tr)//2])
            if disp >= cfg.displacement_ticks and quiet:
                direction = "short" if b.close_ticks > prior_vwap and b.close_ticks < start[i-1].close_ticks else "long" if b.close_ticks < prior_vwap and b.close_ticks > start[i-1].close_ticks else None
                if direction:
                    stop = b.high_ticks if direction == "short" else b.low_ticks
                    return _trade(b.et_date, start, direction, i, stop, cfg.target_r, tc, cfg)
        prior_vwap = (pv + (b.high_ticks + b.low_ticks + b.close_ticks) / 3) / (vol + 1)
        pv += (b.high_ticks + b.low_ticks + b.close_ticks) / 3; vol += 1; prior_tr.append(b.high_ticks - b.low_ticks)
    return {"session": bars[0].et_date.isoformat(), "status": "NO_TRADE", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}


def prior_rth_continuation(bars, reference, cfg, tc, width_median=None):
    start = [b for b in bars if time(9, 30) <= b.timestamp.astimezone(ET).time() < time(11, 30)]
    if not reference or len(start) < 5:
        return {"session": bars[0].et_date.isoformat(), "status": "DATA_INVALID", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}
    width = reference.prior_rth_high_ticks - reference.prior_rth_low_ticks if reference.prior_rth_high_ticks is not None and reference.prior_rth_low_ticks is not None else None
    if width is None or width_median is None or (cfg.width_gate == "below_median" and width >= width_median) or (cfg.width_gate == "above_median" and width < width_median):
        return {"session": bars[0].et_date.isoformat(), "status": "NO_TRADE", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}
    levels = [("long", reference.prior_rth_high_ticks), ("short", reference.prior_rth_low_ticks)]
    cutoff = int(cfg.entry_cutoff * 60 - 570)
    for i, b in enumerate(start[:-cfg.confirmation_bars]):
        if b.timestamp.astimezone(ET).hour * 60 + b.timestamp.astimezone(ET).minute >= 570 + cutoff:
            break
        body = abs(b.close_ticks - b.open_ticks)
        for direction, level in levels:
            if cfg.direction_mode != "both" and direction != cfg.direction_mode:
                continue
            beyond = level is not None and ((direction == "long" and b.close_ticks >= level + cfg.displacement_ticks) or (direction == "short" and b.close_ticks <= level - cfg.displacement_ticks))
            if not beyond:
                continue
            confirms = start[i + 1:i + 1 + cfg.confirmation_bars]
            if len(confirms) < cfg.confirmation_bars or any((x.close_ticks <= level if direction == "long" else x.close_ticks >= level) for x in confirms):
                continue
            signal_i = i + cfg.confirmation_bars - 1
            completed = start[:signal_i + 1]
            median_range = sorted(x.high_ticks - x.low_ticks for x in completed)[len(completed) // 2]
            median_volume = sorted(x.volume for x in completed)[len(completed) // 2]
            vwap = sum((x.high_ticks + x.low_ticks + x.close_ticks) / 3 for x in completed) / len(completed)
            aligned = not cfg.vwap_alignment or (direction == "long" and b.close_ticks >= vwap) or (direction == "short" and b.close_ticks <= vwap)
            if body < cfg.body_fraction * median_range or b.volume < cfg.volume_fraction * median_volume or not aligned:
                continue
            stop = min(x.low_ticks for x in start[i:signal_i + 1]) if direction == "long" else max(x.high_ticks for x in start[i:signal_i + 1])
            return _trade(b.et_date, start, direction, signal_i, stop, cfg.target_r, tc, cfg)
    return {"session": bars[0].et_date.isoformat(), "status": "NO_TRADE", "label": "UNKNOWN", "net_cents": 0, "gross_cents": 0, "costs_cents": 0}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--frozen-config", type=Path)
    p.add_argument("--evaluation-ceiling", choices=("TRAIN", "VALIDATION"), default="TRAIN")
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError(a.output)
    loaded = load_databento_mes(a.input)
    train, validation, _test = partition_groups(loaded.bars)
    groups = train if a.evaluation_ceiling == "TRAIN" else train + validation
    frozen = None
    if a.frozen_config:
        frozen = json.loads(a.frozen_config.read_text())
        frozen = frozen.get("passing_train_candidates", frozen.get("config", frozen))
        if isinstance(frozen, dict):
            frozen = [frozen]
    tc = TradingConfig(commission_cents_per_contract=122, slippage_cents_per_contract=125)
    results = []
    for name, configs in CONFIGS.items():
        configs = [type(CONFIGS[name][0])(**(x.get("config", x))) for x in frozen] if frozen else configs
        for cfg in configs:
            rows_by_partition = {"TRAIN": [], "VALIDATION": []}
            prior_widths = []
            for day, b in groups:
                partition = "TRAIN" if (day, b) in train else "VALIDATION"
                ref = loaded.session_references.get(day)
                width = ref.prior_rth_high_ticks - ref.prior_rth_low_ticks if ref and ref.prior_rth_high_ticks is not None and ref.prior_rth_low_ticks is not None else None
                median = expanding_prior_width_median(prior_widths)
                rows_by_partition[partition].append(prior_rth_continuation(b, ref, cfg, tc, median))
                if width is not None:
                    prior_widths.append(width)
            rows = rows_by_partition[a.evaluation_ceiling]
            metrics = _metrics(rows)
            years = sorted({r["session"][:4] for r in rows})
            net = sum(r["net_cents"] for r in rows)
            results.append({"strategy": name, "config": asdict(cfg), "config_sha256": sha256(canonical_json(asdict(cfg))), "trade_count": metrics["accepted_trades"], "profit_factor": metrics["profit_factor"], "drawdown_cents": metrics["maximum_cumulative_drawdown_cents"], "one_cost_net_cents": sum(r["net_cents"] for r in rows), "double_cost_net_cents": metrics["double_cost_net_cents"], "metrics": metrics, "concentration": {"year_net_share": {y: sum(r["net_cents"] for r in rows if r["session"].startswith(y)) / (net or 1) for y in years}}})
    ranked = sorted(results, key=lambda r: r["double_cost_net_cents"], reverse=True)
    eligible = [r for r in ranked if r["trade_count"] >= 20 and r["one_cost_net_cents"] > 0 and r["double_cost_net_cents"] > 0 and (r["profit_factor"] or 0) > 1 and r["drawdown_cents"] <= 200_000 and max((abs(v) for v in r["concentration"]["year_net_share"].values()), default=0) <= 1.0]
    summary = {"schema_version": "task32-validation-v1", "created_at": datetime.now(UTC).isoformat(), "input_sha256": loaded.inspection["sha256"], "evaluation_ceiling": a.evaluation_ceiling, "cost_assumptions": {"commission_cents_per_contract": 122, "slippage_cents_per_contract": 125}, "search": {"combinations": len(results), "eligible": len(eligible)}, "train_sessions": len(train), "validation_sessions": len(validation), "top_validation_candidates": ranked, "passing_validation_candidates": eligible}
    summary["summary_sha256"] = sha256(canonical_json(summary)); a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_bytes(canonical_json(summary) + b"\n"); print(json.dumps({"output": str(a.output), "top_validation_candidates": ranked, "passing_validation_candidates": eligible}, sort_keys=True))


if __name__ == "__main__": main()

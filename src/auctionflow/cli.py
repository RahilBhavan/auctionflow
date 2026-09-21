from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from .bar_research import run_bar_backtest
from .collector import RawCaptureWriter
from .config import TradingConfig
from .decision import _event
from .domain import TradeCandidate
from .historical import HistoricalDataError, normalize_firstrate_mes, normalize_portara_mes
from .projectx import FixtureTransport, ProjectXCollector
from .projectx_http import ProjectXHttp
from .replay import ReplayError, replay, verify_determinism
from .research import (
    DECISION_SCHEMA,
    NORMALIZED_EVENT_SCHEMA,
    OUTCOME_SCHEMA,
    DecisionRow,
    ExperimentManifest,
    NormalizedEventRow,
    append_manifest,
    build_report,
    canonical_json,
    label_outcome,
    sha256,
    write_parquet_atomic,
)
from .session import session_id
from .shadow import _fixture_transport, snapshot


def main(argv: list[str] | None = None, *, monotonic=None, utc_clock=None) -> int:
    parser = argparse.ArgumentParser(prog="auctionflow")
    if monotonic is None:
        monotonic = time.monotonic if argv is None else lambda: 0.0
    if utc_clock is None:
        utc_clock = (lambda: datetime.now(UTC)) if argv is None else lambda: datetime(2026, 1, 1, tzinfo=UTC)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--output", required=True, type=Path)
    capture.add_argument("--manifest", required=True, type=Path)
    capture.add_argument("--transport", choices=("fixture",), required=True)
    capture.add_argument("--fixture", required=True, type=Path)
    capture.add_argument("--streams", required=True)
    capture.add_argument("--account-id", action="append", default=[])
    capture.add_argument("--contract-id", action="append", default=[])
    capture.add_argument("--duration-seconds", type=float)
    normalize = sub.add_parser("normalize-bars", help="normalize vendor bars as BAR_CLOSE_PROXY events")
    normalize.add_argument("input", type=Path)
    normalize.add_argument("--output", required=True, type=Path)
    normalize.add_argument("--format", required=True, choices=("firstrate-mes-1m", "portara-mes-1m"))
    normalize.add_argument("--strategy-window-only", action="store_true")
    command = sub.add_parser("replay")
    command.add_argument("input", type=Path)
    command.add_argument("--decisions", required=True, type=Path)
    command.add_argument("--raw", type=Path)
    command.add_argument("--parquet", type=Path)
    command.add_argument("--normalized-parquet", type=Path)
    command.add_argument("--verify-determinism", action="store_true")
    research = sub.add_parser("research")
    research.add_argument("input", type=Path)
    for name in ("decisions", "events_parquet", "decisions_parquet", "outcomes_parquet", "shadow_parquet", "report", "ledger"):
        research.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    for name in ("experiment_id", "author", "hypothesis", "created_at"):
        research.add_argument("--" + name.replace("_", "-"), required=True)
    research.add_argument("--overwrite", action="store_true")
    shadow = sub.add_parser("shadow-snapshot", help="write a read-only TopstepX shadow report")
    shadow.add_argument("--account-id", required=True, type=int)
    shadow.add_argument("--contract-id", required=True)
    shadow.add_argument("--output", required=True, type=Path)
    shadow.add_argument("--lookback-minutes", type=int, default=60)
    shadow.add_argument("--transport", choices=("projectx", "fixture"), default="projectx")
    shadow.add_argument("--fixture", type=Path)
    shadow.add_argument("--created-at")
    bars = sub.add_parser("backtest-bars")
    bars.add_argument("input", type=Path)
    bars.add_argument("--report", required=True, type=Path)
    bars.add_argument("--sessions-jsonl", required=True, type=Path)
    bars.add_argument("--experiment-id", required=True)
    bars.add_argument("--created-at", required=True)
    bars.add_argument("--commission-cents-per-contract", type=int, default=122)
    bars.add_argument("--slippage-cents-per-contract", type=int, default=125)
    bars.add_argument("--format", choices=("firstrate-mes-1m", "portara-mes-1m", "portara-mes-trades", "databento-glbx-mes-ohlcv-1m"), default="firstrate-mes-1m")
    bars.add_argument("--strategy", choices=("ib_acceptance", "failed_ib", "liquidity_sweep"), default="ib_acceptance")
    bars.add_argument("--evaluation-ceiling", choices=("TRAIN", "VALIDATION", "ALL"), default="ALL")
    for name in ("excursion_ticks", "reclaim_window", "confirmation_bars", "stop_offset_ticks", "target_numerator", "target_denominator"):
        bars.add_argument("--" + name.replace("_", "-"), type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            allowed = {"quotes", "trades", "depth", "accounts", "positions", "orders", "fills"}
            streams = tuple(dict.fromkeys(part.strip() for part in args.streams.split(",") if part.strip()))
            if not streams or any(stream not in allowed for stream in streams):
                raise ValueError("invalid stream; choose quotes,trades,depth,accounts,positions,orders,fills")
            user_streams = {"accounts", "positions", "orders", "fills"}
            market_streams = {"quotes", "trades", "depth"}
            if user_streams.intersection(streams) and not args.account_id:
                raise ValueError("account selector required for user streams")
            if market_streams.intersection(streams) and not args.contract_id:
                raise ValueError("contract selector required for market streams")
            if args.duration_seconds is not None and args.duration_seconds <= 0:
                raise ValueError("duration-seconds must be greater than zero")
            if args.output.resolve() == args.manifest.resolve():
                raise ValueError("output and manifest must differ")
            if args.output.exists() or args.manifest.exists():
                raise ValueError("output and manifest must not already exist")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            transport = FixtureTransport(args.fixture)
            with RawCaptureWriter(args.output, selected_streams=streams, manifest_path=args.manifest) as writer:
                ProjectXCollector(transport, subscriptions=streams, writer=writer,
                                  account_ids=tuple(args.account_id), contract_ids=tuple(args.contract_id),
                                  duration_seconds=args.duration_seconds, monotonic=monotonic,
                                  utc_clock=utc_clock).run()
            return 0
        if args.command == "shadow-snapshot":
            if args.transport == "fixture":
                if args.fixture is None or args.created_at is None:
                    raise ValueError("fixture transport requires --fixture and --created-at")
                observed_at = datetime.fromisoformat(args.created_at)
                if observed_at.tzinfo is None or observed_at.utcoffset() != UTC.utcoffset(None):
                    raise ValueError("created-at must be UTC")
                client = ProjectXHttp(_fixture_transport(args.fixture), username="fixture", api_key="fixture")
                clock = lambda: observed_at
            else:
                if args.fixture is not None or args.created_at is not None:
                    raise ValueError("fixture and created-at require fixture transport")
                client = ProjectXHttp()
                clock = utc_clock
            snapshot(client, account_id=args.account_id, contract_id=args.contract_id,
                     clock=clock, lookback_minutes=args.lookback_minutes, output=args.output)
            return 0
        if args.command == "normalize-bars":
            (normalize_firstrate_mes if args.format == "firstrate-mes-1m" else normalize_portara_mes)(args.input, args.output, args.strategy_window_only)
            return 0
        if args.command == "backtest-bars":
            strategy_params = {name: getattr(args, name) for name in
                               ("excursion_ticks", "reclaim_window", "confirmation_bars", "stop_offset_ticks", "target_numerator", "target_denominator")
                               if getattr(args, name) is not None}
            run_bar_backtest(args.input, args.report, args.sessions_jsonl, args.experiment_id, args.created_at,
                             args.commission_cents_per_contract, args.slippage_cents_per_contract, args.format,
                             args.strategy, args.evaluation_ceiling, **strategy_params)
            return 0
        if args.command == "research":
            args.ledger.parent.mkdir(parents=True, exist_ok=True)
            input_bytes = args.input.read_bytes()
            with tempfile.TemporaryDirectory(dir=args.ledger.parent) as td:
                stage = Path(td)
                staged_decisions = stage / "decisions.jsonl"
                decision_hash = replay(args.input, staged_decisions)
                events = [_event(json.loads(line)) for line in args.input.read_text().splitlines()]
                decisions=[]; candidates=[]
                for line in staged_decisions.read_text().splitlines():
                    row=json.loads(line)
                    if row.get("status")=="FAILED": continue
                    decisions.append(DecisionRow.model_validate({k: row[k] for k in DecisionRow.model_fields if k in row}))
                    if row.get("candidate"): candidates.append(TradeCandidate.model_validate(row["candidate"]))
                outcomes, shadow = [], []
                trading_config = TradingConfig()
                for c in candidates:
                    out=label_outcome(c, events, tick_value_cents=trading_config.mes_tick_value_cents, commission_cents_per_contract=trading_config.commission_cents_per_contract, slippage_cents_per_contract=trading_config.slippage_cents_per_contract); (outcomes if c.status=="accepted" else shadow).append(out)
                sid = session_id(events[0].timestamp, trading_config) if events else ""
                normalized=[NormalizedEventRow(event_id=e.event_id,timestamp=e.timestamp,symbol=e.symbol,session_id=sid,kind=e.kind,sequence=e.sequence,price_ticks=getattr(e,"price_ticks",None),quantity=getattr(e,"quantity",None)) for e in events]
                targets = {"decisions": args.decisions, "events": args.events_parquet, "decisions_parquet": args.decisions_parquet, "outcomes": args.outcomes_parquet, "shadow": args.shadow_parquet, "report": args.report}
                for name, rows, schema, model in [("events",normalized,NORMALIZED_EVENT_SCHEMA,NormalizedEventRow),("decisions_parquet",decisions,DECISION_SCHEMA,DecisionRow),("outcomes",outcomes,OUTCOME_SCHEMA,__import__("auctionflow.research",fromlist=["OutcomeRow"]).OutcomeRow),("shadow",shadow,OUTCOME_SCHEMA,__import__("auctionflow.research",fromlist=["OutcomeRow"]).OutcomeRow)]:
                    write_parquet_atomic(stage / name, rows, schema, model, overwrite=True)
                shutil.copyfile(staged_decisions, stage / "decisions")
                input_hash = sha256(input_bytes)
                hashes={"input": input_hash, "decision": decision_hash}
                hashes.update({name: sha256((stage / name).read_bytes()) for name in ("events","decisions_parquet","outcomes","shadow")})
                report=build_report(decisions,candidates,outcomes+shadow,input_hash=input_hash,decision_hash=decision_hash,output_hash=sha256(canonical_json(hashes)))
                (stage / "report").write_bytes(canonical_json(report)+b"\n")
                hashes.update({"report": sha256((stage / "report").read_bytes()), "decisions": sha256((stage / "decisions").read_bytes())})
                output_hash = sha256(canonical_json(hashes))
                manifest=ExperimentManifest(experiment_id=args.experiment_id,author=args.author,created_at=args.created_at,hypothesis=args.hypothesis,input_hash=input_hash,decision_hash=decision_hash,output_hash=output_hash,artifact_hashes=hashes,artifact_paths={k: Path(v).name for k, v in targets.items()},summary=report["counts"])
                manifest_line = canonical_json(manifest.model_dump(mode="json"))
                ledger_before = args.ledger.read_bytes() if args.ledger.exists() else None
                existing = []
                if ledger_before:
                    existing = [json.loads(old) for old in ledger_before.splitlines()]
                same = next((item for item in existing if item.get("experiment_id") == args.experiment_id), None)
                if same is not None and canonical_json(same) != manifest_line.rstrip():
                    raise ValueError("conflicting experiment_id")
                if same is not None and not args.overwrite:
                    if any(not p.exists() or sha256(p.read_bytes()) != hashes[name] for name, p in targets.items()):
                        raise ValueError("existing experiment artifacts differ")
                    return 0
                prior = {dest: dest.read_bytes() if dest.exists() else None for dest in targets.values()}
                try:
                    for name, dest in targets.items():
                        dest.parent.mkdir(parents=True,exist_ok=True); os.replace(stage / name, dest)
                    append_manifest(args.ledger,manifest)
                except Exception:
                    for dest, data in prior.items():
                        if data is None:
                            dest.unlink(missing_ok=True)
                        else:
                            dest.write_bytes(data)
                    if ledger_before is None: args.ledger.unlink(missing_ok=True)
                    else: args.ledger.write_bytes(ledger_before)
                    raise
            return 0
        options = {"raw_path": args.raw, "parquet_path": args.parquet, "normalized_parquet_path": args.normalized_parquet}
        if args.verify_determinism: verify_determinism(args.input, args.decisions, **options)
        else: replay(args.input, args.decisions, **options)
    except (OSError, ReplayError, HistoricalDataError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

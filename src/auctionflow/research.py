from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from .domain import Event, ResetEvent, TradeCandidate, TradeEvent

RESEARCH_SCHEMA_VERSION = "1"
GENERATION_MODE = "OFFLINE_RESEARCH_ONLY"
T = TypeVar("T", bound=BaseModel)

class ResearchModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: str = RESEARCH_SCHEMA_VERSION

class NormalizedEventRow(ResearchModel):
    event_id: str; timestamp: datetime; symbol: str; session_id: str
    sequence: int | None = None; kind: str; price_ticks: int | None = None; quantity: int | None = None
class DecisionRow(ResearchModel):
    event_id: str; timestamp: datetime; session_id: str; decision: str
    reason_codes: tuple[str, ...] = (); account_snapshot_hash: str = ""
    config_revision: str = ""; strategy_config_revision: str = ""
class CandidateRow(ResearchModel):
    candidate_id: str; event_id: str; session_id: str; strategy: str; direction: str
    entry_ticks: int; stop_ticks: int; status: str; quantity: int | None = None
    risk_budget_cents: int | None = None; rejection_reasons: tuple[str, ...] = ()
    trading_config_revision: str = ""; strategy_config_revision: str = ""
class OutcomeLabel(StrEnum):
    TARGET_FIRST="TARGET_FIRST"; STOP_FIRST="STOP_FIRST"; AMBIGUOUS="AMBIGUOUS"; UNKNOWN="UNKNOWN"
class OutcomeRow(ResearchModel):
    candidate_id: str; decision_event_id: str; session_id: str; direction: str; label: OutcomeLabel
    entry_ticks: int; stop_ticks: int; target_ticks: int; mfe_ticks: int = 0; mae_ticks: int = 0
    mfe_r: float = 0.0; mae_r: float = 0.0; events_to_resolution: int | None = None
    seconds_to_resolution: float | None = None; gross_cents: int = 0; net_cents: int = 0
    reason: str; confidence: str; start_event_id: str; end_event_id: str | None = None
    quantity: int | None = None; trading_config_revision: str = ""; strategy_config_revision: str = ""
class AcceptedOutcomeRow(OutcomeRow): pass
class RejectedShadowOutcomeRow(OutcomeRow): pass
class ExperimentManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: str = RESEARCH_SCHEMA_VERSION; experiment_id: str; author: str; created_at: str
    hypothesis: str; package_version: str = "0.1.0"; input_hash: str; decision_hash: str = ""
    output_hash: str; artifact_hashes: dict[str, str] = {}; artifact_paths: dict[str, str] = {}
    config_revisions: dict[str, str] = {}; label_parameters: dict[str, Any] = {}
    summary: dict[str, Any] = {}; disposition: str = "RESEARCH_ONLY"

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
def sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()
def rows_to_table[T: BaseModel](rows: Iterable[BaseModel | dict[str, Any]], schema: pa.Schema, model: type[T]) -> pa.Table:
    values = []
    for row in rows:
        value = model.model_validate(row).model_dump(mode="python")
        for field in schema:
            if pa.types.is_string(field.type) and isinstance(value.get(field.name), (dict, list, tuple)):
                value[field.name] = canonical_json(value[field.name]).decode()
        values.append(value)
    return pa.Table.from_pylist(values, schema=schema)
def _schema(fields): return pa.schema(fields, metadata={b"schema_version": RESEARCH_SCHEMA_VERSION.encode()})
NORMALIZED_EVENT_SCHEMA=_schema([(n,t) for n,t in [("schema_version",pa.string()),("event_id",pa.string()),("timestamp",pa.timestamp("us",tz="UTC")),("symbol",pa.string()),("session_id",pa.string()),("sequence",pa.int64()),("kind",pa.string()),("price_ticks",pa.int64()),("quantity",pa.int64())]])
DECISION_SCHEMA=_schema([(n,t) for n,t in [("schema_version",pa.string()),("event_id",pa.string()),("timestamp",pa.timestamp("us",tz="UTC")),("session_id",pa.string()),("decision",pa.string()),("reason_codes",pa.list_(pa.string())),("account_snapshot_hash",pa.string()),("config_revision",pa.string()),("strategy_config_revision",pa.string())]])
CANDIDATE_SCHEMA=_schema([(n,t) for n,t in [("schema_version",pa.string()),("candidate_id",pa.string()),("event_id",pa.string()),("session_id",pa.string()),("strategy",pa.string()),("direction",pa.string()),("entry_ticks",pa.int64()),("stop_ticks",pa.int64()),("status",pa.string()),("quantity",pa.int64()),("risk_budget_cents",pa.int64()),("rejection_reasons",pa.list_(pa.string())),("trading_config_revision",pa.string()),("strategy_config_revision",pa.string())]])
OUTCOME_SCHEMA=_schema([(n,t) for n,t in [("schema_version",pa.string()),("candidate_id",pa.string()),("decision_event_id",pa.string()),("session_id",pa.string()),("direction",pa.string()),("label",pa.string()),("entry_ticks",pa.int64()),("stop_ticks",pa.int64()),("target_ticks",pa.int64()),("mfe_ticks",pa.int64()),("mae_ticks",pa.int64()),("mfe_r",pa.float64()),("mae_r",pa.float64()),("events_to_resolution",pa.int64()),("seconds_to_resolution",pa.float64()),("gross_cents",pa.int64()),("net_cents",pa.int64()),("reason",pa.string()),("confidence",pa.string()),("start_event_id",pa.string()),("end_event_id",pa.string()),("quantity",pa.int64()),("trading_config_revision",pa.string()),("strategy_config_revision",pa.string())]])
ACCEPTED_OUTCOME_SCHEMA=OUTCOME_SCHEMA; REJECTED_SHADOW_OUTCOME_SCHEMA=OUTCOME_SCHEMA; SHADOW_OUTCOME_SCHEMA=OUTCOME_SCHEMA
EVENT_SCHEMA=NORMALIZED_EVENT_SCHEMA; EVENT_ROW=NormalizedEventRow; DECISION_ROW=DecisionRow; CANDIDATE_ROW=CandidateRow; OUTCOME_ROW=OutcomeRow
EXPERIMENT_MANIFEST_SCHEMA = pa.schema([
    ("schema_version", pa.string()), ("experiment_id", pa.string()), ("author", pa.string()),
    ("created_at", pa.string()), ("hypothesis", pa.string()), ("package_version", pa.string()),
    ("input_hash", pa.string()), ("decision_hash", pa.string()), ("output_hash", pa.string()),
    ("artifact_hashes", pa.string()), ("artifact_paths", pa.string()),
    ("config_revisions", pa.string()), ("label_parameters", pa.string()),
    ("summary", pa.string()), ("disposition", pa.string()),
], metadata={b"schema_version": RESEARCH_SCHEMA_VERSION.encode()})

def write_parquet_atomic(path, rows, schema, model, *, overwrite=False):
    destination=Path(path)
    if destination.exists() and not overwrite: raise FileExistsError(destination)
    table=rows_to_table(rows,schema,model); destination.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=f".{destination.name}.",suffix=".tmp",dir=destination.parent); os.close(fd)
    try: pq.write_table(table,name); os.replace(name,destination)
    finally:
        if os.path.exists(name): os.unlink(name)

def label_outcome(candidate: TradeCandidate, events: Iterable[Event], *, session_end=None, tick_value_cents=125, commission_cents_per_contract=0, slippage_cents_per_contract=0):
    """Label a candidate; commission and slippage are total modeled round-trip costs per contract."""
    if commission_cents_per_contract < 0 or slippage_cents_per_contract < 0:
        raise ValueError("commission and slippage costs must be nonnegative")
    ordered=sorted(events,key=lambda e:(e.timestamp,e.sequence if e.sequence is not None else -1)); start=next((i for i,e in enumerate(ordered) if e.event_id==candidate.event_id),None); distance=abs(candidate.entry_ticks-candidate.stop_ticks)
    target_distance = (distance * 3 + 1) // 2
    target=candidate.entry_ticks+target_distance*(1 if candidate.direction=="long" else -1); label=OutcomeLabel.UNKNOWN; reason="no_resolution_before_horizon"; end=None; mfe=mae=0
    future=[(i,e) for i,e in enumerate(ordered) if start is not None and i>start and (session_end is None or e.timestamp<=session_end)]
    previous_sequence = ordered[start].sequence if start is not None else None
    for i,e in future:
        if isinstance(e,ResetEvent) or getattr(e,"session_id",candidate.session_id)!=candidate.session_id:
            reason="reset_or_session_mismatch"; break
        if e.sequence is not None and previous_sequence is not None and e.sequence != previous_sequence + 1:
            reason="unresolved_sequence_gap"; break
        if e.sequence is not None: previous_sequence = e.sequence
        if not isinstance(e,TradeEvent): continue
        move=(e.price_ticks-candidate.entry_ticks)*(1 if candidate.direction=="long" else -1); mfe=max(mfe,min(move,target_distance)); mae=min(mae,max(move,-distance)); ht=move>=target_distance; hs=move<=-distance
        same_time = [(j, other) for j, other in future if other.timestamp == e.timestamp and isinstance(other, TradeEvent)]
        touches = [(j, other, (other.price_ticks-candidate.entry_ticks)*(1 if candidate.direction=="long" else -1)) for j, other in same_time]
        target_touch = [item for item in touches if item[2] >= target_distance]
        stop_touch = [item for item in touches if item[2] <= -distance]
        simultaneous = target_touch and stop_touch
        if simultaneous:
            touched = target_touch + stop_touch
            sequences = [item[1].sequence for item in touched]
            ordered_touches = all(sequence is not None for sequence in sequences) and len(sequences) == len(set(sequences))
            if ordered_touches:
                first = min(touched, key=lambda item: item[1].sequence)
                label = OutcomeLabel.TARGET_FIRST if first in target_touch else OutcomeLabel.STOP_FIRST
                reason = label.value.lower()
            else:
                label = OutcomeLabel.AMBIGUOUS
                reason = "both_thresholds_same_time"
            end = min(touched, key=lambda item: item[0])[1].event_id
            break
        if ht or hs:
            label=OutcomeLabel.TARGET_FIRST if ht else OutcomeLabel.STOP_FIRST
            reason=label.value.lower(); end=e.event_id; break
    effective_quantity = candidate.quantity or 1
    gross=((target_distance if label==OutcomeLabel.TARGET_FIRST else -distance if label==OutcomeLabel.STOP_FIRST else 0)*effective_quantity*tick_value_cents)
    costs=(commission_cents_per_contract + slippage_cents_per_contract) * effective_quantity if label in {OutcomeLabel.TARGET_FIRST, OutcomeLabel.STOP_FIRST} else 0
    net = gross - costs
    resolving = next((event for _, event in future if event.event_id == end), None)
    event_count = (ordered.index(resolving) - start) if resolving is not None and start is not None else None
    elapsed = (resolving.timestamp - ordered[start].timestamp).total_seconds() if resolving is not None and start is not None else None
    return OutcomeRow(candidate_id=candidate.candidate_id,decision_event_id=candidate.event_id,session_id=candidate.session_id,direction=candidate.direction,label=label,entry_ticks=candidate.entry_ticks,stop_ticks=candidate.stop_ticks,target_ticks=target,mfe_ticks=mfe,mae_ticks=mae,mfe_r=mfe/(distance or 1),mae_r=mae/(distance or 1),events_to_resolution=event_count,seconds_to_resolution=max(0.0, elapsed) if elapsed is not None else None,gross_cents=gross,net_cents=net,reason=reason,confidence="high" if label!=OutcomeLabel.UNKNOWN else "low",start_event_id=candidate.event_id,end_event_id=end,quantity=candidate.quantity,trading_config_revision=candidate.trading_config_revision,strategy_config_revision=candidate.strategy_config_revision)

def label_candidates(candidates, events, *, session_end=None, tick_value_cents=125, commission_cents_per_contract=0, slippage_cents_per_contract=0):
    accepted=[]; shadow=[]
    for c in candidates:
        if c.status in {"accepted","rejected"}: (accepted if c.status=="accepted" else shadow).append(label_outcome(c,events,session_end=session_end,tick_value_cents=tick_value_cents,commission_cents_per_contract=commission_cents_per_contract,slippage_cents_per_contract=slippage_cents_per_contract))
    return accepted,shadow

def build_report(decisions, candidates, outcomes, *, input_hash="", decision_hash="", output_hash="", config_revisions=None):
    ds=list(decisions); cs=list(candidates); os_=list(outcomes); labels=Counter(o.label.value for o in os_)
    report={"schema_version":RESEARCH_SCHEMA_VERSION,"generation_mode":GENERATION_MODE,"input_hash":input_hash,"decision_hash":decision_hash,"config_revisions":config_revisions or {}}
    if output_hash: report["data_output_hash"] = output_hash
    report.update({"decision_distribution":dict(sorted(Counter(d.decision for d in ds).items())),"reason_distribution":dict(sorted((k,v) for d in ds for k,v in Counter(d.reason_codes).items())),"counts":{"decisions":len(ds),"candidates":len(cs),"accepted":sum(c.status=="accepted" for c in cs),"shadow":sum(c.status=="rejected" for c in cs),"unknown":labels.get("UNKNOWN",0)},"outcome_rates":{k:labels[k]/len(os_) if os_ else 0.0 for k in ["TARGET_FIRST","STOP_FIRST","AMBIGUOUS","UNKNOWN"]},"synthetic_aggregates":{"mfe_ticks":sum(o.mfe_ticks for o in os_),"mae_ticks":sum(o.mae_ticks for o in os_),"net_cents":sum(o.net_cents for o in os_)},"validation":{"deterministic":True,"leakage_safe":True,"sequence_validated":True,"schema_validated":True},"disclaimer":"RESEARCH_ONLY; synthetic outcomes are not profitability evidence or promotion authority."})
    return report

def append_manifest(path, manifest):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); line=canonical_json(manifest.model_dump(mode="json"))+b"\n"; existing=p.read_bytes() if p.exists() else b""
    for old in existing.splitlines():
        item=json.loads(old)
        if item.get("experiment_id")==manifest.experiment_id:
            if old==line.rstrip(): return False
            raise ValueError("conflicting experiment_id")
    with p.open("ab") as f: f.write(line)
    return True

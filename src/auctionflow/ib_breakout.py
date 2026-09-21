from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bars import OHLCVBar
from .config import TradingConfig
from .databento import SessionReferences
from .risk import propose_risk

NY = ZoneInfo("America/New_York")

class BarStrategyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ib_start: time = time(9, 30); ib_end: time = time(10, 30); entry_end: time = time(11, 30)
    displacement_ticks: int = Field(default=1, gt=0); acceptance_bars: int = Field(default=2, gt=0); acceptance_window: int = Field(default=3, gt=0)
    retest_tolerance_ticks: int = Field(default=1, ge=0); retest_window: int = Field(default=3, gt=0); continuation_ticks: int = Field(default=1, gt=0); stop_offset_ticks: int = Field(default=1, gt=0)
    target_numerator: int = Field(default=3, gt=0); target_denominator: int = Field(default=2, gt=0)
    @model_validator(mode="after")
    def valid(self):
        if not self.ib_start < self.ib_end < self.entry_end or (self.ib_end.hour * 60 + self.ib_end.minute) - (self.ib_start.hour * 60 + self.ib_start.minute) != 60: raise ValueError("invalid strategy times")
        if self.acceptance_bars > self.acceptance_window or self.target_numerator < self.target_denominator: raise ValueError("invalid strategy geometry")
        return self
    @property
    def revision(self): return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class FailedIBBreakoutConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reclaim_window: int = Field(default=3, gt=0)
    confirmation_bars: int = Field(default=0, ge=0)
    excursion_ticks: int = Field(default=1, gt=0)
    stop_offset_ticks: int = Field(default=1, gt=0)
    target_numerator: int = Field(default=3, gt=0)
    target_denominator: int = Field(default=2, gt=0)

    @model_validator(mode="after")
    def valid(self):
        if self.target_numerator < self.target_denominator:
            raise ValueError("invalid strategy geometry")
        if self.confirmation_bars >= self.reclaim_window:
            raise ValueError("invalid strategy geometry: confirmation bars must be less than reclaim window")
        return self

    @property
    def revision(self): return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class LiquiditySweepConfig(FailedIBBreakoutConfig):
    pass

class IBSessionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session: date; state: str; status: str; reason: str; ib_high_ticks: int|None = None; ib_low_ticks: int|None = None
    direction: Literal["long", "short"]|None = None; signal_id: str|None = None; entry_id: str|None = None; entry_ticks: int|None = None; stop_ticks: int|None = None; target_ticks: int|None = None
    exit_id: str|None = None; exit_ticks: int|None = None; quantity: int = 0; risk_cents: int = 0; risk_budget_cents: int = 0; costs_cents: int = 0
    label: str = "UNKNOWN"; gross_cents: int = 0; net_cents: int = 0; contract_id: str|None = None; continuous: bool = False
    data_warning: Literal["CONTINUOUS_SERIES_NO_CONTRACT_ROLL_ADJUSTMENT"]|None = None
    trading_config_revision: str; strategy_config_revision: str; mode: Literal["BAR_OHLC_RESEARCH_ONLY", "CONTINUOUS_SERIES"] = "BAR_OHLC_RESEARCH_ONLY"

def _result(day, status, reason, tc, sc, **kw): return IBSessionResult(session=day, status=status, state=status, reason=reason, trading_config_revision=tc.revision, strategy_config_revision=sc.revision, **kw)

def _common(bars, tc):
    if not bars: return date.min, [], {}
    day=bars[0].et_date; ids={b.contract_id for b in bars}; concrete=ids-{None}; common={"contract_id":next(iter(concrete)) if len(concrete)==1 else None,"continuous":ids=={None},"data_warning":"CONTINUOUS_SERIES_NO_CONTRACT_ROLL_ADJUSTMENT" if ids=={None} else None,"mode":"BAR_OHLC_RESEARCH_ONLY"}
    if len(concrete)>1 or (None in ids and concrete) or any(b.et_date!=day for b in bars) or any(b.timestamp>=bars[i+1].timestamp or b.sequence+1!=bars[i+1].sequence for i,b in enumerate(bars[:-1])): return day, [], common
    span=[b for b in bars if time(9,30)<=b.timestamp.astimezone(NY).time()<time(11,30)]
    if len(span)!=120 or any(b.timestamp!=span[0].timestamp+timedelta(minutes=i) or b.sequence!=span[0].sequence+i for i,b in enumerate(span)): return day, [], common
    return day, span, common

def _trade(day, post, direction, signal, stop, tc, sc, common, hi=None, lo=None, remaining=None):
    entry=post[0]; ep=entry.open_ticks
    if (direction=="long" and stop>=ep) or (direction=="short" and stop<=ep): return _result(day,"REJECTED","INVALID_GEOMETRY",tc,sc,direction=direction,signal_id=signal.event_id,**common)
    dist=abs(ep-stop); move=(dist*sc.target_numerator+sc.target_denominator-1)//sc.target_denominator; target=ep+move if direction=="long" else ep-move
    proposal=propose_risk(ep,stop,tc.mll_trailing_cushion_cents,remaining,tc); base=dict(direction=direction,signal_id=signal.event_id,entry_id=entry.event_id,entry_ticks=ep,stop_ticks=stop,target_ticks=target,quantity=proposal.quantity,risk_cents=proposal.stop_risk_cents,risk_budget_cents=proposal.risk_budget_cents,ib_high_ticks=hi,ib_low_ticks=lo,**common)
    if not proposal.quantity:return _result(day,"REJECTED","INSUFFICIENT_RISK",tc,sc,**base)
    label="UNKNOWN"; exitbar=None; gross=0
    for b in post:
        s=b.low_ticks<=stop if direction=="long" else b.high_ticks>=stop; t=b.high_ticks>=target if direction=="long" else b.low_ticks<=target
        if s and t: label="AMBIGUOUS"; exitbar=b; break
        if t: label="TARGET_FIRST"; gross=abs(target-ep)*tc.mes_tick_value_cents; exitbar=b; break
        if s: label="STOP_FIRST"; gross=-abs(ep-stop)*tc.mes_tick_value_cents; exitbar=b; break
    if exitbar is None: label="SESSION_EXIT"; exitbar=post[-1]; gross=exitbar.close_ticks-ep if direction=="long" else ep-exitbar.close_ticks
    costs=(tc.commission_cents_per_contract+tc.slippage_cents_per_contract)*proposal.quantity; base.update(exit_id=exitbar.event_id,exit_ticks=target if label=="TARGET_FIRST" else stop if label=="STOP_FIRST" else exitbar.close_ticks,costs_cents=costs,label=label,gross_cents=0 if label=="AMBIGUOUS" else gross*proposal.quantity,net_cents=0 if label=="AMBIGUOUS" else gross*proposal.quantity-costs)
    return _result(day,"ACCEPTED","SIGNAL",tc,sc,**base)

def evaluate_ib_session(bars: Iterable[OHLCVBar], trading_config=None, strategy_config=None, mll_buffer_cents=None, remaining_daily_budget_cents=None, remaining_data_cap_cents=None, remaining_setup_cap_cents=None):
    tc, sc, ordered = trading_config or TradingConfig(), strategy_config or BarStrategyConfig(), list(bars)
    if not ordered: return _result(date.min, "DATA_INVALID", "NO_BARS", tc, sc)
    day, span, common = _common(ordered, tc)
    ids={b.contract_id for b in ordered}; concrete=ids-{None}
    if len(concrete)>1 or (None in ids and concrete): return _result(day,"DATA_INVALID","SESSION_CONTRACT_IDENTITY_INVALID",tc,sc,**common)
    if not span: return _result(day, "DATA_INVALID", "EVALUATION_SPAN_REQUIRES_120_CONTIGUOUS_BARS", tc, sc, **common)
    ib=span[:60]; hi=max(b.high_ticks for b in ib); lo=min(b.low_ticks for b in ib); post=span[60:]
    for i,b in enumerate(post):
        for direction, level in (("long",hi),("short",lo)):
            breakout=b.close_ticks >= level+sc.displacement_ticks if direction=="long" else b.close_ticks <= level-sc.displacement_ticks
            if not breakout: continue
            acc=post[i:i+sc.acceptance_window]
            if len(acc)<sc.acceptance_bars or not all((x.close_ticks >= level+sc.displacement_ticks if direction=="long" else x.close_ticks <= level-sc.displacement_ticks) if n==0 else (x.close_ticks>=level if direction=="long" else x.close_ticks<=level) for n,x in enumerate(acc[:sc.acceptance_bars])): return _result(day,"NO_TRADE","INVALIDATED_FIRST_BREAKOUT",tc,sc,ib_high_ticks=hi,ib_low_ticks=lo,direction=direction,**common)
            for j in range(i+sc.acceptance_bars,min(len(post),i+sc.acceptance_bars+sc.retest_window)):
                x=post[j]
                if (x.close_ticks<level if direction=="long" else x.close_ticks>level): return _result(day,"NO_TRADE","INVALIDATED_FIRST_BREAKOUT",tc,sc,**common)
                if (x.low_ticks<=level+sc.retest_tolerance_ticks if direction=="long" else x.high_ticks>=level-sc.retest_tolerance_ticks):
                    if j+2>=len(post): return _result(day,"NO_TRADE","INVALIDATED_FIRST_BREAKOUT",tc,sc,**common)
                    stop=min(z.low_ticks for z in post[i:j+1])-sc.stop_offset_ticks if direction=="long" else max(z.high_ticks for z in post[i:j+1])+sc.stop_offset_ticks
                    remaining=min((x for x in (remaining_daily_budget_cents,remaining_data_cap_cents,remaining_setup_cap_cents) if x is not None),default=None)
                    return _trade(day,post[j+2:],direction,x,stop,tc,sc,common,hi,lo,remaining)
            return _result(day,"NO_TRADE","INVALIDATED_FIRST_BREAKOUT",tc,sc,**common)
    return _result(day,"NO_TRADE","NO_QUALIFIED_BREAKOUT",tc,sc,ib_high_ticks=hi,ib_low_ticks=lo,**common)

def _evaluate_reclaim(bars, levels, cfg, tc):
    day,span,common=_common(list(bars),tc)
    if not span:return _result(day,"DATA_INVALID","EVALUATION_SPAN_REQUIRES_120_CONTIGUOUS_BARS",tc,cfg,**common)
    if not levels:return _result(day,"NO_TRADE","MISSING_SESSION_REFERENCES",tc,cfg,**common)
    post=span[60:]
    for i,b in enumerate(post):
        for direction,level in levels:
            beyond=b.high_ticks>=level+cfg.excursion_ticks if direction=="short" else b.low_ticks<=level-cfg.excursion_ticks
            reclaim=b.close_ticks<=level if direction=="short" else b.close_ticks>=level
            if not (beyond and reclaim): continue
            end=i+1+cfg.confirmation_bars
            if end>len(post) or not all((x.close_ticks<=level if direction=="short" else x.close_ticks>=level) for x in post[i+1:end]): continue
            if end>=len(post): return _result(day,"NO_TRADE","ENTRY_WINDOW_EXPIRED",tc,cfg,**common)
            stop=(max(x.high_ticks for x in post[i:end])+cfg.stop_offset_ticks if direction=="short" else min(x.low_ticks for x in post[i:end])-cfg.stop_offset_ticks)
            return _trade(day,post[end:],direction,b,stop,tc,cfg,common)
    return _result(day,"NO_TRADE","NO_QUALIFIED_RECLAIM",tc,cfg,**common)

def evaluate_failed_ib_breakout(bars: Iterable[OHLCVBar], trading_config=None, strategy_config=None):
    tc=trading_config or TradingConfig(); cfg=strategy_config or FailedIBBreakoutConfig(); ordered=list(bars); day,span,common=_common(ordered,tc)
    if not span:return _result(day,"DATA_INVALID","EVALUATION_SPAN_REQUIRES_120_CONTIGUOUS_BARS",tc,cfg,**common)
    ib=span[:60]; hi=max(b.high_ticks for b in ib); lo=min(b.low_ticks for b in ib); post=span[60:]
    first = next(((i, "short", hi) if b.close_ticks >= hi + cfg.excursion_ticks else (i, "long", lo)
                  for i, b in enumerate(post)
                  if b.close_ticks >= hi + cfg.excursion_ticks or b.close_ticks <= lo - cfg.excursion_ticks), None)
    if first is None:
        return _result(day,"NO_TRADE","NO_QUALIFIED_RECLAIM",tc,cfg,ib_high_ticks=hi,ib_low_ticks=lo,**common)
    i, d, l = first
    for j in range(i+1,min(len(post),i+1+cfg.reclaim_window)):
        b=post[j]; reclaimed=b.close_ticks<=l if d=="short" else b.close_ticks>=l
        if reclaimed:return _trade(day,post[j+1:],d,b,max(x.high_ticks for x in post[i:j+1])+cfg.stop_offset_ticks if d=="short" else min(x.low_ticks for x in post[i:j+1])-cfg.stop_offset_ticks,tc,cfg,common,hi,lo) if j+1<len(post) else _result(day,"NO_TRADE","ENTRY_WINDOW_EXPIRED",tc,cfg,**common)
    return _result(day,"NO_TRADE","NO_QUALIFIED_RECLAIM",tc,cfg,ib_high_ticks=hi,ib_low_ticks=lo,**common)

def evaluate_liquidity_sweep_reclaim(bars: Iterable[OHLCVBar], references: SessionReferences | None = None, trading_config=None, strategy_config=None):
    if references is None:return _result(date.min,"DATA_INVALID","MISSING_SESSION_REFERENCES",trading_config or TradingConfig(),strategy_config or LiquiditySweepConfig())
    levels=[]
    for v,d in ((references.prior_rth_high_ticks,"short"),(references.overnight_high_ticks,"short"),(references.prior_rth_low_ticks,"long"),(references.overnight_low_ticks,"long")):
        if v is not None and (d,v) not in levels: levels.append((d,v))
    return _evaluate_reclaim(bars,levels,strategy_config or LiquiditySweepConfig(),trading_config or TradingConfig())

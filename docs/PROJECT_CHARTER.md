# Project Charter

## Purpose
Build the smallest auditable deterministic replay path for MES research. The system validates recorded inputs, advances explicit state, applies risk gates, and writes a decision journal.

## Non-negotiable boundary
This milestone is local-only, offline, deterministic, and **alert-only**. It never places, modifies, or cancels an order and has no ProjectX connection, credentials, live feed, account reconciliation, UI, database, or execution authority. `AUTHORIZE_*` means only that a simulated alert passed internal gates.

## Operating limits
- One MES contract family and one Topstep session at a time.
- 9:35 ET inclusive through 11:30 ET exclusive.
- One simulated daily authorization token; journaled authorization consumes it.
- No scale-in, re-entry, reversal, or stop widening.
- Integer ticks/cents, timezone-aware timestamps, immutable raw JSONL, deterministic output.
- Strategies and thresholds are research-only until promotion policy is satisfied.

Topstep/platform rules are external claims and require dated verification; internal controls are this project's safety policy and do not claim to be official platform rules.

The fixture capture path is local-only and fixture-only; `capture` has no live or broker transport. `src/auctionflow/projectx.py` is fixture-only and has no live ProjectX or broker reconciliation path. The isolated `auctionflow.projectx_http` client is read-only, transport-injected, and has no decision or execution path. Credentials are constructor/environment-only and JWTs are memory-only. No network is used by implementation tests. Future networking requires separate approval, personal devices only (no VPS, VPN, or remote operation), and must not create an execution endpoint or collector-to-decision path.

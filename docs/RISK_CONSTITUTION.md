# Risk Constitution

## Authority boundary
This document distinguishes **official external rules** from **internal controls**. No live authority is invented here. Official Topstep rules must be rechecked against current official documentation before implementation or promotion.

## External rules ledger

| Rule | Official source | Effective date verified | Formula / interpretation | Matching test |
|---|---|---|---|---|
| Topstep 50K Maximum Loss Limit snapshot | [What is the Maximum Loss Limit](https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit) | 2026-07-01 (verified 2026-08-04) | Starts at $48,000 ($2,000 cushion); trails upward from prior EOD balance, never decreases, and permanently locks at the initial $50,000 account balance. Breach monitoring uses real-time realized plus unrealized P&L/equity. | `tests/test_rules_property.py` |
| Session/platform details | Topstep official documentation | NOT YET VERIFIED | No claim made in this repository | Required before implementation |

The dated snapshot is encoded as `TOPSTEP_50K_MLL_2026-07-01` with a deterministic SHA256 revision. Rules can change; live promotion requires re-verification against the current official source before use.

## Internal controls
These are project controls, stricter or different from any platform rule: alert-only/no-order scope; one MES family/session; one token; token consumed on journaled simulated authorization; maximum two MES in this phase and four as a hard invariant; risk budget `min($200, 10% of remaining MLL buffer, remaining daily budget, mode/data/setup caps)`; fixed structural stop; include fees and stress slippage; fail closed on uncertain state, open position, invalid data, or insufficient buffer.

Bracket intent contracts are simulation-only and deliberately stop at a validated immutable description: LIMIT entry, STOP stop, LIMIT target, DAY duration, OCO, and full-quantity reduce-only children. They have no transport or mutation path. A future production gateway must consume a token only when an entry order is accepted by the official API. That future behavior is not implemented here. No order endpoint methods or collector-to-decision path may be added by capture work. Future read-only networking and credentials require separate approval and personal-device-only operation; no VPS, VPN, or remote use.

### Durable simulator state

The local state store is simulator-only, offline, and never live authority. It uses an explicit JSON path, a stable sibling `fcntl.flock` lock, strict frozen versioned validation, revision-checked transitions, and atomic fsynced publication. A consumed token survives restart and is never auto-repaired or reset. Authorization requires known health and protective state, zero position, and an exact nonempty reconciliation fingerprint. Kill-switch clearing is an explicit revision-checked operator action. Unsupported locking and corrupt, truncated, or mismatched state fail closed. The fixture-compatible offline reconciliation comparator is likewise flat-only: missing or unknown observations block, working entries and orphan protective orders mismatch, and open expected positions are explicitly unsupported. It performs no persistence or broker interaction.

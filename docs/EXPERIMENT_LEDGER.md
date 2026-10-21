# Experiment ledger

This file defines the record that every research trial must leave, including failed trials.

Every threshold/configuration trial, including failures, is recorded here or in the equivalent append-only canonical JSONL ledger supplied to `auctionflow research --ledger PATH`. The command requires an explicit `--created-at` timestamp and appends only after all artifacts succeed; reruns with identical content are idempotent.

## Record format

- `trial_id`, UTC timestamp, author
- config revision/hash and all threshold values
- strategy card and fixture/data identifier
- hypothesis and rationale
- result, decision distribution, failure mode
- leakage/determinism checks
- disposition: `RESEARCH_ONLY`, rejected, or promoted under policy

## Seed revision 0

- Status: `RESEARCH_ONLY`; not validated, promoted, or live.
- Scope: normalized recorded events with explicit reference levels.
- Rationale: establishes deterministic contracts before captured ProjectX data exists.
- Deferred: TPO, volume profile, DOM reconstruction, calibrated OFI/impact residual, heatmap state, and GEX.
- Required next evidence: reproducible fixture outcomes, property tests, and dated rule verification.

A passing replay is not evidence of profitability or permission to execute. All outputs remain alert-only. Fixture capture checks deterministic local sequencing and persistence; it is not a future live smoke test. Raw local sequence has no upstream continuity guarantee, and recursive redaction plus fatal persistence behavior are mandatory.

Bar backtests use `BAR_OHLC_RESEARCH_ONLY`; FirstRate continuous sessions (missing contract identity) are included, with no contract-roll adjustment. Reports include a zero-opening `NO_TRADE` safety/reference control and deterministic 2x transaction-cost sensitivity; neither is evidence of an edge. Their reports are pipeline evidence only and must not be used as profitability or promotion evidence.

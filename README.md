# AuctionFlow OneShot

AuctionFlow OneShot is a Python 3.12 research and replay package for one MES contract family and one Topstep session at a time.

## Scope and safety

Milestone 0 is local-only, deterministic, offline, and **alert-only**. It can journal `NO_TRADE`, `WATCH`, `SETUP_ARMED`, `AUTHORIZE_1_MES`, `AUTHORIZE_2_MES`, or `BLOCK_ACCOUNT`; an authorization is not an order and no code may connect to a broker, ProjectX, or submit an order. There are no credentials, live collectors, dashboards, databases, or execution gateways in this milestone.

The simulated replay consumes its daily token when an authorization is journaled. Durable simulator-only account/session state now persists this one-shot transition with locked, atomic local-file publication; it is not live authority. A future production gateway, if separately approved, must consume a token only after the official API accepts an entry order. No scale-in, re-entry, reversal, or stop widening is allowed.

Offline reconciliation is fixture-only and does not reconcile any live account, broker, or ProjectX state. It is a flat-account startup gate that compares strict normalized expected state with fixture telemetry, fingerprints validated observations, and fails closed on missing or unknown data; it has no transport, credentials, order methods, or mutation. Bracket intents are frozen, deterministic, MES-only simulation contracts: LIMIT entry, STOP/LIMIT OCO children, DAY duration, full-quantity protective children, and no scale-in, re-entry, or stop widening. They are never submitted.

## Development

Create a Python 3.12 environment and install the project with `pip install -e '.[test,dev]'`. Run `python -m pytest` and `ruff check .` when source and tests exist.

## Rules and evidence

`docs/RISK_CONSTITUTION.md` separates official Topstep rules from internal controls. External rules are not treated as current authority until verified against dated official documentation. Research defaults are explicitly `RESEARCH_ONLY`; they are not validated, promoted, or live parameters.

The fixture-only `auctionflow capture` command writes deterministic local raw JSONL and an explicit manifest. It makes no network calls and accepts no credentials. `src/auctionflow/projectx.py` is fixture-only; it provides the offline collector and fixture transport, not a live or broker transport. `auctionflow.projectx_http` provides an isolated, read-only TopstepX REST boundary with injected transports for tests; its production HTTPS transport is never invoked by tests and is not connected to collector, decision, bracket, or state modules.

`auctionflow shadow-snapshot --account-id ID --contract-id MES --output REPORT` performs an opt-in, read-only snapshot. Live mode (the default, `--transport projectx`) reads only `TOPSTEPX_USERNAME` and `TOPSTEPX_API_KEY`, uses real UTC, and never creates or mutates orders. Credential-free local mode is `--transport fixture --fixture PATH --created-at 2025-01-20T16:00:00Z`; PATH is a JSON object keyed by the exact read-only REST endpoint paths, with each value the corresponding response envelope. Fixture mode makes no network calls and requires the explicit UTC clock. Reports are sanitized, exclusive, atomic, and contain local financial data (bars, positions, orders, and trades); they never create orders or bracket intent. A non-MATCH reconciliation is reported as `BLOCK_ACCOUNT`; otherwise the snapshot is `NO_TRADE` with no setup evaluated.

`auctionflow normalize-bars INPUT --output OUTPUT --format firstrate-mes-1m` ingests FirstRate MES 1-minute CSV into canonical JSONL. Portara downloads use `--format portara-mes-1m` and the exact `Date,Time,Open,High,Low,Close,Volume` schema; timestamps are interpreted as fixed UTC-06:00 per the source product page (not Chicago DST), with contract ID inferred from names such as `MES2026M`. This output is **BAR_CLOSE_PROXY**: real exchange-derived vendor bars for pipeline/infrastructure and bar-close research only, not tick/order-flow evidence, aggression, depth, LVN, or exact stop/target sequencing. Add `--strategy-window-only` to retain `[09:35,11:30)` New York time.

`auctionflow backtest-bars INPUT --report REPORT --sessions-jsonl SESSIONS --experiment-id ID --created-at ISO` evaluates sessions in chronological order as `BAR_OHLC_RESEARCH_ONLY`. `--strategy` selects the frozen `ib_acceptance` (default), `failed_ib`, or `liquidity_sweep` evaluator; sweep references are supplied only by the Databento session projection. Reports retain the chronological 60/20/20 valid-session partitions and include strategy revisions, reference-missing counts, reason counts, per-year metrics, and year concentration diagnostics. Use `--format firstrate-mes-1m`, `--format portara-mes-1m`, or `--format portara-mes-trades`; the latter aggregates Portara `Timestamp,Price,Volume` ticks into minute OHLCV bars using the historical reader. Continuous FirstRate rows (`contract_id` absent) are included without contract-roll adjustment. Results are pipeline evidence only, not profitability or promotion evidence. MES backtest defaults use explicit round-trip assumptions of 122 cents commission and 125 cents slippage per contract; pass zero values explicitly for a deliberate frictionless control.

Databento GLBX MDP3 1-minute CSV uses the exact header `ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol`. Rows must be UTC nanosecond timestamps, `rtype=33`, and `publisher_id=1`; prices are raw, unadjusted MES prices on 0.25-point ticks. Spread symbols are excluded. For each represented America/New_York ET date, the selected contract is the outright with highest volume on the immediately prior represented ET date; ties are resolved by symbol, and the first represented date is excluded. Only the 09:30–11:29 ET projection is evaluated. Missing or duplicate selected minutes invalidate evaluation, and contract rolls are not adjusted, so roll-day results require that caveat.

Run it with `auctionflow backtest-bars data/external/databento/glbx-mdp3-20190506-20260803.ohlcv-1m.csv --format databento-glbx-mes-ohlcv-1m --report data/external/databento/backtests/databento-mes-ib-2019-2026-v1.report.json --sessions-jsonl data/external/databento/backtests/databento-mes-ib-2019-2026-v1.sessions.jsonl --experiment-id databento-mes-ib-2019-2026-v1 --created-at 2026-08-05T15:00:00Z`.

## Results so far (research only)

The Databento GLBX MDP3 replay covers 1,870 MES sessions from 2019-05 to 2026-08 (1,859 valid, 11 invalidated by missing contiguous bars) under the frozen `ib_acceptance` strategy with 122 cents commission and 125 cents slippage per contract round trip. Sessions are partitioned chronologically 60/20/20 into train, validation and test. The strategy accepted 419 sessions and journaled `NO_TRADE` on 1,419; expectancy per accepted trade was negative on the train and validation partitions and positive only on the test partition, which is the pattern the promotion policy treats as no evidence. Nothing has been promoted from `RESEARCH_ONLY`. The report and session JSONL are reproducible from the command above; they are not committed because the underlying data is licensed.

## Status

Milestone 0. 341 tests, `ruff` clean, CI on push. MIT licensed. Not investment advice, not connected to any broker, not a trading system.

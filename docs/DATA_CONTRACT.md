# Data contract

This file defines the raw and canonical record formats and the rules for each vendor input.

Raw JSONL is immutable and preserved independently from normalized records. Collector envelopes use the frozen `projectx.raw.v1` contract, retain arbitrary payloads (including malformed or unknown values), redact credential-like keys recursively, and require contiguous local sequences. Each line is compact canonical JSON, flushed immediately; capture files are exclusive-create append-only artifacts and clean shutdown writes a deterministic manifest containing count, capture SHA-256, selected streams, and status. Persistence failures are fatal. Malformed lines are reported with line number and event ID and cannot authorize an alert. Replay is deterministic: identical raw input, initial state, and config produce byte-identical canonical output.

Canonical records use UTC timestamps with explicit `America/New_York` conversion, integer MES ticks, and integer cents for money, P&L, fees, slippage, and risk. Sequence/order fields are retained when present. Account and strategy state are explicit at checkpoints.

The first slice accepts recorded normalized trade/quote/depth/reset/account events only. No network, wall clock, random source, database, credential, broker, ProjectX, or order fields are required or permitted. Output is a decision journal and optional research Parquet export.

FirstRate MES 1-minute CSV bars may be normalized with `auctionflow normalize-bars INPUT --output OUTPUT --format firstrate-mes-1m`. The result is explicitly labeled **BAR_CLOSE_PROXY**: real exchange-derived vendor bars suitable for pipeline/infrastructure validation and bar-close research, but not tick/order-flow evidence, aggression, depth, LVN, or exact stop/target sequencing. Only close is emitted as MES ticks; OHLC intrabar order is not invented. Source timestamps are America/New_York local time and are converted to UTC; ambiguous or nonexistent local times are rejected. The optional strategy window is `[09:35,11:30)` New York time.

Portara MES 1-minute files use exactly `Date,Time,Open,High,Low,Close,Volume`, with `YYYYMMDD`/`HHMM`, quarter-tick prices, and positive integer volume. The product page identifies timestamps as GMT-6; we apply fixed UTC-06:00 (not America/Chicago DST), so this source contract retains that uncertainty. Files infer `contract_id` from an exact eight-character `MES` stem (for example `MES2026M`); ambiguous names are rejected. Timestamps must be minute-start and strictly increasing. Strategy windows are evaluated after conversion in America/New_York. Use `--format portara-mes-1m` for normalization or bar backtests. Native IB research bars remain `BAR_OHLC_RESEARCH_ONLY`; intrabar ordering is never inferred.

Databento MES loads select a contract from the preceding represented session's volume, then expose immutable per-session reference bounds for that selected contract: the preceding represented session's 09:30-16:00 RTH high/low and the prior evening through 09:29 ET overnight high/low. Unavailable bounds remain `None`; bars-only readers continue to return only projected bars.

Fixture capture validation is separate from any future live smoke test. A separately approved read-only collector may use networking and credentials only on a personal device, never a VPS, VPN, or remote host; no upstream continuity guarantee is implied by local sequence numbers.

Every decision records event/session identity, timestamp, state, reason codes, entry/stop where applicable, risk budget, requested quantity, account snapshot hash, and config revision. `AUTHORIZE_*` remains an alert-only simulated decision and never submits an order.

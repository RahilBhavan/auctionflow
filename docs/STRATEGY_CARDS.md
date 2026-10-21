# Strategy cards

This file describes each strategy evaluator and its states.

The two event-replay cards are deterministic, pure, and **research-only**. Their outputs feed an alert-only policy; neither can place an order. Defaults are hypotheses, not validated or live parameters.

## Mouse Trap
States: dormant, level approach, swept, reclaim pending, confirmation pending, armed/invalidated. A fixture supplies the reference level, volatility-normalized sweep bounds, aggressive-flow condition, poor-response/impact-residual condition, reclaim window, two confirmations, and structural stop beyond the sweep extreme.

## Acceptance Breakout
States: inside auction, broken, hold pending, retest pending, continuation confirmed/invalidated. A fixture supplies boundary, minimum displacement, acceptance event count, retest, continuation, and structural stop.

Incomplete sequences emit `WATCH` or `SETUP_ARMED`; completed candidates still pass session, account, data, and risk gates. Conflicting sleeves fail closed with `NO_TRADE`. No scale-in, re-entry, reversal, or stop widening is supported.

Seed values must be revisioned in the experiment ledger and explicitly overridden by tests or fixtures. They must never be described as promoted or live.

## Bar evaluators

`auctionflow backtest-bars --strategy` selects one of three 1-minute-bar evaluators in `src/auctionflow/ib_breakout.py`: `ib_acceptance` (the default), `failed_ib`, and `liquidity_sweep`. `liquidity_sweep` uses prior-session and overnight reference bounds, which only the Databento loader supplies. These evaluators are `BAR_OHLC_RESEARCH_ONLY` and are also research-only.

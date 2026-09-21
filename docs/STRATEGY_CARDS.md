# Strategy Cards

Both cards are deterministic, pure, and **research-only**. Their outputs feed an alert-only policy; neither can place an order. Defaults are hypotheses, not validated or live parameters.

## Mouse Trap
States: dormant, level approach, swept, reclaim pending, confirmation pending, armed/invalidated. A fixture supplies the reference level, volatility-normalized sweep bounds, aggressive-flow condition, poor-response/impact-residual condition, reclaim window, two confirmations, and structural stop beyond the sweep extreme.

## Acceptance Breakout
States: inside auction, broken, hold pending, retest pending, continuation confirmed/invalidated. A fixture supplies boundary, minimum displacement, acceptance event count, retest, continuation, and structural stop.

Incomplete sequences emit `WATCH` or `SETUP_ARMED`; completed candidates still pass session, account, data, and risk gates. Conflicting sleeves fail closed with `NO_TRADE`. No scale-in, re-entry, reversal, or stop widening is supported.

Seed values must be revisioned in the experiment ledger and explicitly overridden by tests or fixtures. They must never be described as promoted or live.

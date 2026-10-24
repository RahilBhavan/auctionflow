# AuctionFlow OneShot: deterministic, alert-only replay and research for MES futures sessions

[![ci](https://github.com/RahilBhavan/auctionflow/actions/workflows/ci.yml/badge.svg)](https://github.com/RahilBhavan/auctionflow/actions/workflows/ci.yml) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Research only. Alert only. This project never submits, modifies, or cancels an order.** An `AUTHORIZE_*` decision is a journal entry from a simulation, not an order. Not affiliated with CME Group or Topstep. Not investment advice.

AuctionFlow OneShot (repository `auctionflow`, package `auctionflow-oneshot`) is a Python 3.12 package that replays one Micro E-mini S&P 500 (MES) futures session at a time under Topstep-style account rules. It reads recorded events or vendor 1-minute bars, advances explicit account and strategy state, applies fail-closed risk gates, and writes a deterministic decision journal. The same inputs, initial state, and config produce byte-identical output, and `replay --verify-determinism` checks it.

## Results

These figures are carried over from the earlier README. The underlying Databento data is licensed, so the report is not committed and could not be re-run for this revision.

| Item | Value |
|---|---|
| Data | Databento GLBX MDP3 MES 1-minute bars, 2019-05 to 2026-08 |
| Sessions | 1,870 total, 1,859 valid (11 invalidated by missing contiguous bars) |
| Strategy | frozen `ib_acceptance` (initial-balance acceptance), 09:30–11:29 ET |
| Costs | 122 cents commission and 125 cents slippage per contract round trip |
| Split | chronological 60/20/20 train, validation, and test |
| Decisions | 419 sessions accepted, 1,419 journaled `NO_TRADE` (1,838 in total; see the note below) |
| Expectancy per accepted trade | negative on train and validation, positive only on test |
| Outcome | nothing promoted. The promotion policy treats a test-only positive as no evidence. |

**Unreconciled:** 1,859 valid sessions minus 419 accepted and 1,419 `NO_TRADE` leaves 21 sessions unaccounted for. The report and the licensed data are not available, so the category of those 21 sessions has not been found. Treat the decision counts as unverified until the report is re-run ([issue #2](https://github.com/RahilBhavan/auctionflow/issues/2)).

Source: `data/external/databento/backtests/databento-mes-ib-2019-2026-v1.report.json`, produced by the command in [Run it](#run-it). This file is not in the repository.

## How it works

Each module below is in `src/auctionflow/`. `cli.py` exposes them as the `auctionflow` command.

- `session.py`, `rules.py`, `risk.py`: the 09:35–11:30 ET window, a dated Topstep 50K Maximum Loss Limit snapshot, and a risk budget of `min($200, 10% of remaining MLL buffer, remaining daily budget, caps)`.
- `strategies.py`, `ib_breakout.py`: pure strategy evaluators. Event replay uses the Mouse Trap and Acceptance Breakout cards. Bar backtests use `ib_acceptance`, `failed_ib`, and `liquidity_sweep`.
- `decision.py`, `replay.py`: emit `NO_TRADE`, `WATCH`, `SETUP_ARMED`, `AUTHORIZE_1_MES`, `AUTHORIZE_2_MES`, or `BLOCK_ACCOUNT`, with reason codes, a config revision, and an account snapshot hash.
- `state_store.py`: simulator-only account state on local disk with a file lock and atomic writes. A journaled authorization consumes the one daily token, and the token survives a restart. Corrupt or uncertain state fails closed.
- `bracket.py`, `reconciliation.py`: frozen bracket-intent descriptions (LIMIT entry, STOP and LIMIT OCO children) and a flat-account startup check against fixture data. Neither has a transport.
- `historical.py`, `databento.py`, `bar_research.py`: load FirstRate, Portara, and Databento bars and run chronological bar backtests with explicit costs.
- `projectx_http.py`: the only network code. It is an opt-in, read-only TopstepX REST client with a fixed list of search and read endpoints. Only `shadow-snapshot` uses it. It is not connected to the decision, bracket, or state modules.

## Run it

Prerequisites: Python 3.12 and [uv](https://docs.astral.sh/uv/). One regression test runs `uv run`, so the test suite needs uv on `PATH`.

```bash
uv sync --locked --extra test --extra dev
uv run --locked ruff check .
uv run --locked --extra test --extra dev pytest
```

Observed on 2026-10-21: `341 passed` in about 3 seconds, and `ruff check` passed with the locked ruff 0.16.1. CI runs the same three commands (`.github/workflows/ci.yml`). CI passed on the latest `main` commit (fe3066a) on 2026-10-24.

Replay a committed fixture and check that two runs give the same output:

```bash
uv run auctionflow replay tests/fixtures/acceptance_breakout.jsonl --decisions decisions.jsonl --verify-determinism
```

The fixture is synthetic test data, not market data. The journal it writes has six lines: five `WATCH` entries with reason `SETUP`, then one `AUTHORIZE_2_MES` entry with reason `AUTHORIZATION` at 2025-01-02T14:41:00Z. That entry is a simulated alert, not an order.

Run a bar backtest on the synthetic FirstRate fixture:

```bash
uv run auctionflow backtest-bars tests/fixtures/historical/synthetic_firstrate.csv \
	--report report.json --sessions-jsonl sessions.jsonl \
	--experiment-id demo --created-at 2026-10-21T00:00:00Z
```

Reproduce the Databento result. This needs a licensed Databento GLBX MDP3 MES 1-minute CSV at the input path:

```bash
uv run auctionflow backtest-bars data/external/databento/glbx-mdp3-20190506-20260803.ohlcv-1m.csv \
	--format databento-glbx-mes-ohlcv-1m \
	--report data/external/databento/backtests/databento-mes-ib-2019-2026-v1.report.json \
	--sessions-jsonl data/external/databento/backtests/databento-mes-ib-2019-2026-v1.sessions.jsonl \
	--experiment-id databento-mes-ib-2019-2026-v1 --created-at 2026-08-05T15:00:00Z
```

Other commands:

- `auctionflow normalize-bars INPUT --output OUTPUT --format firstrate-mes-1m` (or `portara-mes-1m`) writes canonical JSONL labeled `BAR_CLOSE_PROXY`.
- `auctionflow research` writes Parquet exports, a report, and an append-only experiment ledger entry.
- `auctionflow capture --transport fixture` writes raw JSONL and a manifest from a fixture. It has no live transport.
- `auctionflow shadow-snapshot --account-id ID --contract-id MES --output REPORT` writes a read-only account snapshot. The default `--transport projectx` mode reads `TOPSTEPX_USERNAME` and `TOPSTEPX_API_KEY` from the environment and calls the TopstepX API. Use `--transport fixture --fixture PATH --created-at ISO` to run with no network and no credentials.

## Repository layout

- `src/auctionflow/`: the package, 24 modules and 3,210 lines.
- `tests/`: 341 pytest and hypothesis tests, with fixtures and golden reports.
- `scripts/`: one-off train-split parameter searches over the Databento data.
- `docs/`: the charter, risk rules, data contract, strategy cards, and research policy.
- `.github/workflows/ci.yml`: ruff and pytest on every push and pull request.

## Documentation

- [Project charter](docs/PROJECT_CHARTER.md): the purpose, the alert-only boundary, and the operating limits.
- [Risk constitution](docs/RISK_CONSTITUTION.md): official Topstep rules, with dates and sources, kept apart from this project's internal controls.
- [Data contract](docs/DATA_CONTRACT.md): raw and canonical record formats and the rules for each vendor input.
- [Strategy cards](docs/STRATEGY_CARDS.md): each strategy evaluator and its states.
- [Experiment ledger](docs/EXPERIMENT_LEDGER.md): the required record for every trial, including failures.
- [Promotion policy](docs/PROMOTION_POLICY.md): the evidence a strategy needs before it can leave `RESEARCH_ONLY`.

## Sources

- **Databento GLBX MDP3** 1-minute OHLCV for MES, 2019-05-06 to 2026-08-03 per the file name. The data is licensed and is not committed. `data/external/` is in `.gitignore`.
- **FirstRate Data** and **Portara** MES 1-minute bars are supported input formats. No vendor data is committed. Test fixtures are synthetic.
- **Topstep** rule snapshot: the Maximum Loss Limit help article, effective 2026-07-01 and checked 2026-08-04. See the [risk constitution](docs/RISK_CONSTITUTION.md).
- Each bar backtest report records SHA-256 hashes of the input file, the sessions JSONL, and the report, plus the config revisions.

## Scope and limits

- Independent research by Rahil Bhavan. Not affiliated with CME Group, Topstep, Databento, FirstRate Data, or Portara.
- Alert only. No code path places, modifies, or cancels an order. Nothing has been promoted from `RESEARCH_ONLY`.
- Bar backtests are `BAR_OHLC_RESEARCH_ONLY`. They do not show tick order flow, depth, or the exact order of a stop and a target inside one bar. Contract rolls are not adjusted.
- The Databento results above could not be re-run here, because the data is licensed.
- Only one Topstep rule (the 50K Maximum Loss Limit) has been checked against official documentation. Other platform rules are not verified.
- A passing replay is not evidence of profitability.

## License

MIT. See [LICENSE](LICENSE). The license covers this project's code, not third-party market data or Topstep documentation.

Part of [Rahil Bhavan's projects](https://github.com/RahilBhavan).

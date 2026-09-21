import json
import subprocess
from datetime import UTC, datetime

from auctionflow.config import TradingConfig
from auctionflow.decision import evaluate
from auctionflow.domain import AccountState, Decision


def event(n=1):
    return {"event_id": f"r{n}", "timestamp": datetime(2025, 1, 2, 14, 36, tzinfo=UTC),
            "symbol": "MES", "kind": "trade", "price_ticks": 100, "quantity": 1}


def test_session_identity_blocks_before_strategy():
    account = AccountState(account_id="a", session_id="wrong", balance_cents=5_000_000, mll_floor_cents=4_800_000)
    result = evaluate(event(), account)
    assert result.record.decision == Decision.BLOCK_ACCOUNT
    assert result.strategy.state == "dormant"


def test_replay_rejects_missing_sequence_with_partial_journal(tmp_path):
    source = tmp_path / "gap.jsonl"
    first, second = event(1), event(2)
    first["sequence"], second["sequence"] = 1, 3
    source.write_text(json.dumps(first, default=str) + "\n" + json.dumps(second, default=str) + "\n")
    decisions = tmp_path / "decisions.jsonl"
    proc = subprocess.run(["uv", "run", "auctionflow", "replay", str(source), "--decisions", str(decisions)], capture_output=True, text=True, check=False)
    assert proc.returncode != 0
    assert json.loads(decisions.read_text().splitlines()[-1])["status"] == "FAILED"


def test_invalid_mll_blocks_without_advancement():
    account = AccountState(account_id="a", session_id="2025-01-02", balance_cents=1, mll_floor_cents=1)
    result = evaluate(event(), account, TradingConfig())
    assert result.record.decision == Decision.BLOCK_ACCOUNT
    assert result.strategy.state == "dormant"

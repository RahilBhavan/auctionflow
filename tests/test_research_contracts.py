import json
from datetime import UTC, datetime

import pytest

from auctionflow.decision import evaluate
from auctionflow.domain import AccountState, Decision, StrategyState
from auctionflow.replay import replay
from auctionflow.strategies import RESEARCH_DEFAULT_STRATEGY_CONFIG, StrategyConfig

TS = datetime(2025, 1, 2, 14, 36, tzinfo=UTC)

def event(**updates):
    return {"event_id": "e1", "timestamp": TS, "symbol": "MES", "price_ticks": 100, "quantity": 1, **updates}

def account(**updates):
    values = {"account_id": "a", "session_id": "2025-01-02", "balance_cents": 5_000_000,
              "mll_floor_cents": 4_800_000}
    values.update(updates)
    return AccountState(**values)

def armed():
    return StrategyState(mouse=StrategyState(state="armed", direction="long", structural_stop_ticks=99),
                         breakout=StrategyState(state="inside_auction"))

def test_strategy_revision_is_stable_and_changes_with_parameters():
    config = StrategyConfig(reference_level=100)
    assert config.revision == StrategyConfig(reference_level=100).revision
    assert config.revision != StrategyConfig(reference_level=101).revision
    assert config.canonical_json() == StrategyConfig(reference_level=100).canonical_json()

def test_shared_default_identity():
    from auctionflow import decision
    from auctionflow import replay as replay_module
    assert decision.RESEARCH_DEFAULT_STRATEGY_CONFIG is RESEARCH_DEFAULT_STRATEGY_CONFIG
    assert replay_module.RESEARCH_DEFAULT_STRATEGY_CONFIG is RESEARCH_DEFAULT_STRATEGY_CONFIG

def test_direct_record_has_replay_contract_fields(tmp_path):
    direct = evaluate(event(), account(), strategy=armed()).record.model_dump(mode="json")
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps({**event(), "timestamp": TS.isoformat()}) + "\n")
    output = tmp_path / "decisions.jsonl"
    replay(source, output)
    replayed = json.loads(output.read_text())
    assert set(direct) <= set(replayed)
    assert {"schema_version", "session_id", "account_snapshot_hash", "config_revision", "strategy_config_revision", "candidate"} <= set(direct)

def test_candidate_accepted_and_rejected_geometry():
    accepted = evaluate(event(), account(), strategy=armed())
    assert accepted.record.decision in (Decision.AUTHORIZE_1_MES, Decision.AUTHORIZE_2_MES)
    assert accepted.candidate is not None and accepted.candidate.status == "accepted"
    rejected = evaluate(event(), account(mll_floor_cents=5_000_000), strategy=armed())
    assert rejected.record.decision == Decision.BLOCK_ACCOUNT
    assert rejected.candidate is None

def test_early_blocks_have_no_candidate():
    result = evaluate(event(), account(health_known=False), strategy=armed())
    assert result.record.decision == Decision.BLOCK_ACCOUNT
    assert result.candidate is None and result.record.candidate is None

def test_actionable_insufficient_risk_retains_rejection_candidate():
    result = evaluate(event(), account(mll_floor_cents=4_999_900), strategy=armed())
    assert result.candidate is not None
    assert result.candidate.status == "rejected"
    assert result.candidate.rejection_reasons

def test_flat_and_enveloped_events_are_equivalent():
    flat = event(timestamp=TS.isoformat())
    envelope = {"event_id": "e1", "timestamp": TS.isoformat(), "payload": flat}
    assert evaluate(flat, account(), strategy=armed()).record == evaluate(envelope, account(), strategy=armed()).record

def test_envelope_mismatch_and_malformed_payload_fail_closed():
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluate({"event_id": "wrong", "timestamp": TS.isoformat(), "payload": event(timestamp=TS.isoformat())}, account())
    with pytest.raises(ValueError):
        evaluate({"event_id": "e1", "timestamp": TS.isoformat(), "payload": {"event_id": "e1", "timestamp": TS.isoformat(), "symbol": "MES"}}, account())

import json
import subprocess
import sys
from datetime import UTC, datetime

from auctionflow.replay import replay, verify_determinism


def test_canonical_bytes_are_stable(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps({"event_id": "e1", "timestamp": datetime(2025, 1, 2, 14, 36, tzinfo=UTC).isoformat(),
                                  "symbol": "MES", "price_ticks": 100, "quantity": 1}) + "\n")
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    replay(source, first)
    replay(source, second)
    assert first.read_bytes() == second.read_bytes()
    assert verify_determinism(source, first)


def test_cli_returns_nonzero_for_malformed_and_gap(tmp_path):
    source = tmp_path / "bad.jsonl"
    source.write_text("{}\n")
    output = tmp_path / "out.jsonl"
    command = [sys.executable, "-m", "auctionflow.cli", "replay", str(source), "--decisions", str(output)]
    assert subprocess.run(command, capture_output=True, check=False).returncode != 0
    source.write_text("{\"event_id\":\"a\",\"timestamp\":\"2025-01-02T14:36:00Z\",\"symbol\":\"MES\",\"price_ticks\":100,\"quantity\":1,\"sequence\":1}\n"
                      "{\"event_id\":\"b\",\"timestamp\":\"2025-01-02T14:37:00Z\",\"symbol\":\"MES\",\"price_ticks\":100,\"quantity\":1,\"sequence\":3}\n")
    assert subprocess.run(command, capture_output=True, check=False).returncode != 0

import json

import pytest

from auctionflow.research import ExperimentManifest, append_manifest


def manifest(i='x', hypothesis='h', created_at='2025-01-01T00:00:00Z'):
 return ExperimentManifest(experiment_id=i,author='a',created_at=created_at,hypothesis=hypothesis,input_hash='i',output_hash='o')

def test_append_idempotency_conflict_and_prior_bytes(tmp_path):
 p=tmp_path/'ledger.jsonl'; m=manifest(); assert append_manifest(p,m); first=p.read_bytes()
 assert not append_manifest(p,m); assert p.read_bytes()==first
 assert append_manifest(p,manifest('y')); second=p.read_bytes(); assert second.startswith(first); assert len(second.splitlines())==2
 with pytest.raises(ValueError): append_manifest(p,manifest('x',hypothesis='changed'))
 assert p.read_bytes()==second

def test_manifest_is_canonical_jsonl(tmp_path):
 p=tmp_path/'l'; append_manifest(p,manifest()); row=json.loads(p.read_text())
 assert row['created_at']=='2025-01-01T00:00:00Z'; assert p.read_text().endswith('\n')

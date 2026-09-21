import json
from pathlib import Path

from auctionflow.cli import main


def test_research_workflow_and_replay_compatibility(tmp_path):
 src=Path(__file__).parent/'fixtures'/'acceptance_breakout.jsonl'; paths={n:tmp_path/n for n in ('decisions.jsonl','events.parquet','decisions.parquet','outcomes.parquet','shadow.parquet','report.json','ledger.jsonl')}
 args=[str(src),'--decisions',str(paths['decisions.jsonl']),'--events-parquet',str(paths['events.parquet']),'--decisions-parquet',str(paths['decisions.parquet']),'--outcomes-parquet',str(paths['outcomes.parquet']),'--shadow-parquet',str(paths['shadow.parquet']),'--report',str(paths['report.json']),'--ledger',str(paths['ledger.jsonl']),'--experiment-id','e1','--author','test','--hypothesis','h','--created-at','2025-01-01T00:00:00Z']
 assert main(['research',*args])==0
 before=paths['ledger.jsonl'].read_bytes(); assert main(['research',*args,'--overwrite'])==0; assert paths['ledger.jsonl'].read_bytes()==before
 report=json.loads(paths['report.json'].read_text()); assert report['generation_mode']=='OFFLINE_RESEARCH_ONLY'
 assert main(['replay',str(src),'--decisions',str(tmp_path/'replay.jsonl')])==0

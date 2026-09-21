import json
from datetime import UTC, datetime

from auctionflow.research import (
 CandidateRow,
 DecisionRow,
 OutcomeLabel,
 OutcomeRow,
 build_report,
 canonical_json,
 sha256,
)


def rows():
 d=[DecisionRow(event_id='e',timestamp=datetime(2025,1,1,tzinfo=UTC),session_id='s',decision='WATCH',reason_codes=('x',))]
 c=[CandidateRow(candidate_id='c',event_id='e',session_id='s',strategy='x',direction='long',entry_ticks=10,stop_ticks=8,status='accepted')]
 o=[OutcomeRow(candidate_id='c',decision_event_id='e',session_id='s',direction='long',label=OutcomeLabel.UNKNOWN,entry_ticks=10,stop_ticks=8,target_ticks=13,reason='end',confidence='low',start_event_id='e')]
 return d,c,o

def test_report_is_canonical_deterministic_and_disclaimed():
 d,c,o=rows(); a=build_report(d,c,o,input_hash='i',decision_hash='d',output_hash='o')
 b=build_report(list(reversed(d)),list(reversed(c)),list(reversed(o)),input_hash='i',decision_hash='d',output_hash='o')
 assert canonical_json(a)==canonical_json(b)
 assert json.loads(canonical_json(a))['counts']=={'decisions':1,'candidates':1,'accepted':1,'shadow':0,'unknown':1}
 assert 'not profitability evidence' in a['disclaimer']
 assert sha256(canonical_json(a)) == sha256(canonical_json(b))

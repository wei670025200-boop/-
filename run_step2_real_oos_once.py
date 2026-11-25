"""One-shot local CLI: requires a durable GitHub claim and validated real CSV.
Never downloads or publishes tickets. The claim must be committed BEFORE invocation.
"""
import argparse
import json
import os
from pathlib import Path
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.history_validation import validate_history
from dlt_v5.frozen_protocol import canonical_json,sha256
from dlt_v5.research_pipeline import run_research,spec_bytes

ROOT=Path(__file__).resolve().parent
SPEC_FILES=('docs/DLT_V5_PREREG_CLEANROOM_1.0.md','docs/DLT_V5_PREREG_CLEANROOM_1.0_ADDENDUM.md','docs/DLT_V5_PREREG_EXECUTION_CONVENTIONS.md')

def load_spec(root=ROOT):return spec_bytes(*(Path(root,p).read_bytes() for p in SPEC_FILES))

def verify_claim(claim,csv_raw,prereg_raw,env):
    expected={'schema':'DLT_REAL_OOS_ONCE_CLAIM_V1','run_id':env.get('GITHUB_RUN_ID'),'run_attempt':'1','csv_sha256':sha256(csv_raw),'prereg_spec_sha256':sha256(prereg_raw),'source_commit_sha':env.get('DLT_SOURCE_COMMIT_SHA')}
    if claim!=expected or not isinstance(expected['run_id'],str) or not expected['run_id'].isdigit() or env.get('GITHUB_RUN_ATTEMPT')!='1' or not isinstance(expected['source_commit_sha'],str) or len(expected['source_commit_sha'])!=40:
        raise ValueError('OOS_ONCE_CLAIM_INVALID')
    return expected

def consume_once(path,claim):
    # Local O_EXCL is an additional guard; the branch claim is the durable cloud guard.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(canonical_json(claim));f.flush();os.fsync(f.fileno())

def main():
    p=argparse.ArgumentParser();p.add_argument('--csv',default='data/dlt_history.csv');p.add_argument('--claim',default='data/dlt_real_oos_once_claim.json');p.add_argument('--schedule',default='data/dlt_local_schedule_manifest.json');p.add_argument('--out',default='dlt_oos_artifacts');a=p.parse_args()
    out=Path(a.out);out.mkdir(exist_ok=True)
    raw=Path(a.csv).read_bytes();spec=load_spec();claim=verify_claim(json.loads(Path(a.claim).read_bytes()),raw,spec,os.environ)
    rows,summary=validate_history(raw,LocalSchedule.load(a.schedule))
    if (out/'diagnostics.json').exists():raise ValueError('OOS_OUTPUT_ALREADY_EXISTS')
    consume_once(out/'execution_consumed.json',claim)
    print('REAL_OOS_EXECUTION_CONSUMED=TRUE',flush=True)
    report=run_research(rows,raw,(ROOT/'data/dlt_statutory_rule.json').read_bytes(),spec,claim['source_commit_sha']);report['data_summary']=summary;report['run_claim']=claim
    (out/'diagnostics.json').write_bytes(canonical_json(report));(out/'data_summary.json').write_bytes(canonical_json(summary));(out/'twin_artifact.json').write_bytes(canonical_json(report['twin']));(out/'oos_trace.json').write_bytes(canonical_json(report['trace']));(out/'integrity_summary.json').write_bytes(canonical_json(report['integrity']))
    print(json.dumps({'phase4_status':report['phase4_status'],'data_summary':summary,'diagnostics':report['diagnostics'],'gates':report['gates'],'integrity':report['integrity']},indent=2),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())

"""Offline production CLI for the approved DLT V5 route and fail-closed V4 fallback."""
import argparse
import json
from pathlib import Path

from dlt_v5.engine_v4 import DLTV4Engine
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.prereg_core import front_exclusion_reasons
from dlt_v5.production_v5 import load_runtime
from dlt_v5.runner_v5 import GATES,execute_verified

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True,help='Durable transaction root (dry-run never writes it).')
    p.add_argument('--issue',required=True);p.add_argument('--date',required=True);p.add_argument('--tickets',required=True,type=int)
    p.add_argument('--additional',action='store_true');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--repo-root',default='.');p.add_argument('--schedule',default='data/dlt_local_schedule_manifest.json')
    p.add_argument('--test-fail-gate',choices=GATES,help='Dry-run-only fault injection for the gate fallback test.')
    a=p.parse_args();repo=Path(a.repo_root).resolve()
    if a.test_fail_gate and not a.dry_run:p.error('--test-fail-gate requires --dry-run')
    schedule=LocalSchedule.load(repo/a.schedule);artifact,factory,validators=load_runtime(repo,schedule,a.tickets)
    if a.test_fail_gate:validators[a.test_fail_gate]=lambda:'FAIL'
    engine=DLTV4Engine(a.root,schedule,factory)
    result=execute_verified(engine,a.issue,a.date,a.tickets,validators,additional=a.additional,dry_run=a.dry_run)
    payload=result.get('artifacts',{}).get('canonical_commitment.json',{}).get('payload',{})
    slips=result.get('artifacts',{}).get('physical_slip_plan.json',{})
    tickets=payload.get('tickets',[]);strange=sum(bool(front_exclusion_reasons(tuple(x['front']))) for x in tickets)
    result['production_summary']={'approved_run_id':artifact['authorization']['approved_run_id'],'trained_through_issue':artifact['trained_through_issue'],'candidate':artifact['candidate'],'selected_strategy':result['selected_strategy'],'route_reason':result['gate_decision']['reason'],'ticket_count':len(tickets),'additional':a.additional,'total_rmb':slips.get('total_rmb'),'reflexive_strange_front_hits':strange,'production_commit':result.get('production_commit')}
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

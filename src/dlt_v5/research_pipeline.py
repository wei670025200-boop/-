"""Pure local research assembly. The caller must separately enforce once-only execution.
All rows must first pass the local ledger validator. Never unlocks production.
"""
from dataclasses import asdict
from .prereg_core import PARAMS,FRONT_NAMES,BACK_NAMES,POLICY_GRID
from .digital_twin import feature_history_frozen,oos_arrays,fit_ridge,fit_back,latest_back_rankings
from .frozen_protocol import official_oos_gate,canonical_json,sha256,make_integrity
from .training_prefix import prefix_receipt
from .prequential_rank import rank_trace
from .policy_selector import nested_select
from .cusum_monitor import errors,reference,evaluate

# Prior-defined split and batch-size domain; never selected using data.
REFERENCE_OOS_ROWS=PARAMS['inner_warmup']
REFERENCE_TICKET_COUNT=18

def spec_bytes(original,addendum,conventions):
    return canonical_json({'original_sha256':sha256(original),'addendum_sha256':sha256(addendum),'conventions_sha256':sha256(conventions)})

def run_research(rows,csv_raw,statutory_raw,prereg_raw,git_commit_sha):
    features=feature_history_frozen(rows);initial=PARAMS['initial_train_size']
    a=oos_arrays(features['xf'],features['yf'],features['xb'],features['yb'],features['pairs'],initial)
    d=official_oos_gate(a);ranks=rank_trace(features,[r['date'] for r in rows],initial)
    issues=features['issues'][initial:]
    if [r['issue'] for r in ranks]!=issues:raise ValueError('TRACE_ISSUE_MISMATCH')
    pol=nested_select(a['actual_front'],a['actual_back'],[x['front_rank'] for x in ranks],[x['back_rank'] for x in ranks],REFERENCE_TICKET_COUNT,[x['front_eligible'] for x in ranks])
    e=errors(a['actual_front'],a['prediction_front'],a['actual_back'],a['prediction_back'])
    cutoff=REFERENCE_OOS_ROWS;ref=reference(e[:cutoff],issues[cutoff-1]);drift=evaluate(e[cutoff:],ref)
    f=fit_ridge(features['xf'],features['yf'],PARAMS['l2_alpha_front']);b=fit_back(features['xb'],features['yb'],features['pairs'])
    prefix=prefix_receipt(csv_raw,rows[-1]['issue']);twin={'trained_through_issue':rows[-1]['issue'],'front':asdict(f),'back':asdict(b),'drift_reference':ref,'policy_reference_ticket_count':REFERENCE_TICKET_COUNT,'phase4_status':'LOCKED'}
    trace={'issues':issues,'oos_arrays':a,'ranks':ranks,'policy_trace':pol['trace']}
    digests={'history_prefix_sha256':prefix['prefix_sha256'],'twin_artifact_sha256':sha256(canonical_json(twin)),'statutory_rule_sha256':sha256(statutory_raw),'prereg_spec_sha256':sha256(prereg_raw),'candidate_grid_sha256':sha256(canonical_json(POLICY_GRID)),'oos_trace_sha256':sha256(canonical_json(trace))}
    integrity=make_integrity(digests,git_commit_sha)
    return {'schema':'DLT_CLEANROOM_RESEARCH_V1','phase4_status':'LOCKED','prediction_advantage_verified':False,'profit_guarantee':'NONE','diagnostics':d,'policy':pol,'drift':drift,'rankings':latest_back_rankings(b,features['back_history']),'front_weights':dict(zip(FRONT_NAMES,f.weights)),'back_weights':dict(zip(BACK_NAMES,b.ridge.weights)),'prefix':prefix,'twin':twin,'trace':trace,'integrity':integrity,'gates':[['DATA_GATE','PASS'],['OOS_GATE',d['oos_gate']],['DRIFT_GATE',drift['drift_gate']],['POLICY_GATE',pol['policy_gate']]]}

"""Unlocked V5 router. Each gate is evaluated independently; all routes use execute."""
from .engine_v4 import FALLBACK,V5_ROUTE
GATES=('DATA_GATE','OOS_GATE','DRIFT_GATE','POLICY_GATE')
PHASE4_STATUS='UNLOCKED'
MAX_VALIDATED_TICKETS=18
SCOPE_FALLBACK_REASON='POLICY_SCOPE_EXCEEDED_FALLBACK_V4'

def route_gates(gates):
    if not isinstance(gates,(list,tuple)) or any(not isinstance(v,(list,tuple)) or len(v)!=2 or not isinstance(v[0],str) or not isinstance(v[1],str) for v in gates):return {'valid':False,'gate_states':{},'reason':'MALFORMED_GATES'}
    names=[v[0] for v in gates]
    if names!=list(GATES):return {'valid':False,'gate_states':{},'reason':'MISSING_DUPLICATE_OR_OUT_OF_ORDER_GATE'}
    states=dict(gates);failed=[n for n in GATES if states[n]!='PASS']
    return {'valid':not failed,'gate_states':states,'reason':','.join(failed) if failed else 'ALL_GATES_PASS'}

def execute_unlocked(engine,issue,day,ticket_count,gates,additional=False,dry_run=False):
    if type(ticket_count) is not int or ticket_count<1:raise ValueError('INVALID_EXECUTION_REQUEST')
    if ticket_count>MAX_VALIDATED_TICKETS:
        result=engine.execute(issue,day,ticket_count,additional=additional,route=FALLBACK,dry_run=dry_run)
        decision={'valid':False,'gate_states':{},'reason':SCOPE_FALLBACK_REASON}
        return {**result,'route':FALLBACK,'selected_strategy':FALLBACK,'gate_decision':decision,'phase4_status':PHASE4_STATUS}
    decision=route_gates(gates)
    route=V5_ROUTE if decision['valid'] else FALLBACK
    result=engine.execute(issue,day,ticket_count,additional=additional,route=route,dry_run=dry_run)
    return {**result,'route':route,'selected_strategy':route,'gate_decision':decision,'phase4_status':PHASE4_STATUS}

# Compatibility name retained for callers compiled against the pre-unlock API.
execute_locked=execute_unlocked

def evaluate_validators(validators):
    """Each independent check yields a named status; errors fail closed, never skip routing."""
    out=[];detail={}
    for gate in GATES:
        try:
            state=validators[gate]()
            if state not in ('PASS','FAIL','TRIPPED'):raise ValueError('INVALID_GATE_STATE')
            out.append((gate,state));detail[gate]={'status':state}
        except Exception as exc:
            out.append((gate,'FAIL'));detail[gate]={'status':'FAIL','error':type(exc).__name__+': '+str(exc)}
    return out,detail

def research_validators(csv_raw,schedule,report,statutory_raw,prereg_raw,accepted_integrity,ticket_count):
    """Verify independently from local artifacts; no OOS refitting or network.
    accepted_integrity must be the separately retained reviewed six-digest receipt.
    A hash is not a signature; production authorization is the separately frozen artifact.
    """
    from .history_validation import validate_history
    from .training_prefix import verify_prefix
    from .frozen_protocol import official_oos_gate,canonical_json,sha256,verify_integrity
    from .prereg_core import POLICY_GRID
    from .policy_selector import nested_select
    from .cusum_monitor import errors,reference,evaluate
    from .digital_twin import feature_history_frozen,Ridge,BackModel
    from .research_pipeline import REFERENCE_OOS_ROWS,REFERENCE_TICKET_COUNT
    def data():
        validate_history(csv_raw,schedule);prefix=verify_prefix(csv_raw,report['prefix'])
        if prefix['prefix_sha256']!=accepted_integrity['history_prefix_sha256']:raise ValueError('TRAINING_PREFIX_MISMATCH')
        return 'PASS'
    def oos():
        if sha256(canonical_json(report['trace']))!=accepted_integrity['oos_trace_sha256']:raise ValueError('OOS_TRACE_MISMATCH')
        return official_oos_gate(report['trace']['oos_arrays'])['oos_gate']
    def drift():
        trace=report['trace'];a=trace['oos_arrays'];e=errors(a['actual_front'],a['prediction_front'],a['actual_back'],a['prediction_back']);cut=REFERENCE_OOS_ROWS
        ref=reference(e[:cut],trace['issues'][cut-1]);twin=report['twin']
        if ref!=twin['drift_reference'] or sha256(canonical_json(twin))!=accepted_integrity['twin_artifact_sha256'] or sha256(canonical_json(trace))!=accepted_integrity['oos_trace_sha256']:raise ValueError('DRIFT_REFERENCE_MISMATCH')
        rows,_=validate_history(csv_raw,schedule);verify_prefix(csv_raw,report['prefix']);features=feature_history_frozen(rows)
        import numpy as np
        fm=Ridge(**twin['front']);bm=BackModel(Ridge(**twin['back']['ridge']),tuple(twin['back']['ball_bias']));idx=[i for i,issue in enumerate(features['issues']) if int(issue)>int(twin['trained_through_issue'])]
        later=[]
        if idx:
            xf=np.asarray(features['xf'])[idx];xb=np.asarray(features['xb'])[idx];yf=np.asarray(features['yf'])[idx];yb=np.asarray(features['yb'])[idx];pairs=[features['pairs'][i] for i in idx]
            later=errors(yf,fm.predict(xf),yb,bm.predict(xb,pairs)).tolist()
        return evaluate([*e[cut:].tolist(),*later],ref)['drift_gate']
    def policy():
        if type(ticket_count) is not int or not 1<=ticket_count<=REFERENCE_TICKET_COUNT:raise ValueError('POLICY_TICKET_DOMAIN_UNVALIDATED')
        prefix=verify_prefix(csv_raw,report['prefix']);trace=report['trace'];twin=report['twin'];a=trace['oos_arrays'];ranks=trace['ranks']
        actual={'history_prefix_sha256':prefix['prefix_sha256'],'twin_artifact_sha256':sha256(canonical_json(twin)),'statutory_rule_sha256':sha256(statutory_raw),'prereg_spec_sha256':sha256(prereg_raw),'candidate_grid_sha256':sha256(canonical_json(POLICY_GRID)),'oos_trace_sha256':sha256(canonical_json(trace))}
        verify_integrity(accepted_integrity,actual)
        if report['integrity']!=accepted_integrity or twin['trained_through_issue']!=prefix['trained_through_issue'] or twin['policy_reference_ticket_count']!=REFERENCE_TICKET_COUNT:raise ValueError('POLICY_BINDING_MISMATCH')
        p=nested_select(a['actual_front'],a['actual_back'],[x['front_rank'] for x in ranks],[x['back_rank'] for x in ranks],REFERENCE_TICKET_COUNT,[x['front_eligible'] for x in ranks])
        if canonical_json(p['trace'])!=canonical_json(trace['policy_trace']) or canonical_json(p['final_candidate_research_only'])!=canonical_json(twin['policy_selection_research_only']):raise ValueError('POLICY_TRACE_MISMATCH')
        return p['policy_gate']
    return {'DATA_GATE':data,'OOS_GATE':oos,'DRIFT_GATE':drift,'POLICY_GATE':policy}

def execute_verified(engine,issue,day,ticket_count,validators,additional=False,dry_run=False):
    if type(ticket_count) is int and ticket_count>MAX_VALIDATED_TICKETS:
        return execute_unlocked(engine,issue,day,ticket_count,(),additional,dry_run)
    gates,detail=evaluate_validators(validators)
    return {**execute_unlocked(engine,issue,day,ticket_count,gates,additional,dry_run),'gate_detail':detail}

"""Locked V5 router. Each gate is evaluated independently; all routes use execute."""
from .engine_v4 import FALLBACK
GATES=('DATA_GATE','OOS_GATE','DRIFT_GATE','POLICY_GATE')
PHASE4_STATUS='LOCKED'

def route_gates(gates):
    if not isinstance(gates,(list,tuple)) or any(not isinstance(v,(list,tuple)) or len(v)!=2 or not isinstance(v[0],str) or not isinstance(v[1],str) for v in gates):return {'valid':False,'gate_states':{},'reason':'MALFORMED_GATES'}
    names=[v[0] for v in gates]
    if names!=list(GATES):return {'valid':False,'gate_states':{},'reason':'MISSING_DUPLICATE_OR_OUT_OF_ORDER_GATE'}
    states=dict(gates);failed=[n for n in GATES if states[n]!='PASS']
    return {'valid':not failed,'gate_states':states,'reason':','.join(failed) if failed else 'PHASE4_LOCKED'}

def execute_locked(engine,issue,day,ticket_count,gates,additional=False,dry_run=False):
    decision=route_gates(gates)
    result=engine.execute(issue,day,ticket_count,additional=additional,route=FALLBACK,dry_run=dry_run)
    return {**result,'route':FALLBACK,'gate_decision':decision,'phase4_status':PHASE4_STATUS}

"""Nested selection on prequential rank traces; no current-row fitting or lookahead."""
import numpy as np
from .prereg_core import POLICY_GRID,effective_back_k,PARAMS
from .frozen_protocol import block_indices,POLICY_SEED,make_integrity,verify_integrity

def _trace(yf,yb,front_rank,back_rank):
    a,b,f,r=(np.asarray(x,dtype=float) for x in (yf,yb,front_rank,back_rank))
    if a.ndim!=1 or not len(a) or any(x.shape!=a.shape or not np.isfinite(x).all() for x in (a,b,f,r)) or np.any((f<0)|(f>1)) or np.any((r<1)|(r>66)|(r!=np.floor(r))): raise ValueError('INVALID_POLICY_TRACE')
    return a,b,f,r

def _score(a,b,hf,hb,minf,minb):
    nf,nb=int(hf.sum()),int(hb.sum())
    if nf<minf or nb<minb: return None
    sf=float(a.mean()-a[hf].mean());sb=float(b.mean()-b[hb].mean())
    return {'front_hits':nf,'back_hits':nb,'score_front':sf,'score_back':sb,'primary':.5*(sf+sb)}

def nested_select(yf,yb,front_rank,back_rank,ticket_count,front_eligible=None):
    a,b,f,r=_trace(yf,yb,front_rank,back_rank);n=len(a);warm=PARAMS['inner_warmup']
    if n<warm+12: raise ValueError('INSUFFICIENT_POLICY_ROWS')
    eligible=np.ones(n,dtype=bool) if front_eligible is None else np.asarray(front_eligible,dtype=bool)
    if eligible.shape!=(n,): raise ValueError('INVALID_MASK')
    masks=[(eligible&(f>=lo)&(f<=hi),r<=effective_back_k(k,ticket_count)) for (lo,hi),k in POLICY_GRID]
    out_f=np.zeros(n-warm,dtype=bool);out_b=out_f.copy(); trace=[]; expected_f=[];expected_b=[]
    for t in range(warm,n):
        best=None
        for i,(hf,hb) in enumerate(masks):
            score=_score(a[:t],b[:t],hf[:t],hb[:t],5,15)
            if score is not None and (best is None or score['primary']>best[1]['primary']): best=(i,score)
        if best is None:
            trace.append({'index':t,'candidate':None});expected_f.append(0.);expected_b.append(0.);continue
        i,score=best;hf,hb=masks[i];(lo,hi),k=POLICY_GRID[i]
        out_f[t-warm]=hf[t];out_b[t-warm]=hb[t];expected_f.append(hi-lo);expected_b.append(effective_back_k(k,ticket_count)/66)
        trace.append({'index':t,'candidate':i,'inner':score,'front_hit':bool(hf[t]),'back_hit':bool(hb[t])})
    aa,bb=a[warm:],b[warm:];score=_score(aa,bb,out_f,out_b,10,30)
    result={'policy_gate':'FAIL','outer_score':score,'front_hits':int(out_f.sum()),'back_hits':int(out_b.sum()),'valid_resample_fraction':0.,'mbb_ci95':None,'B':5000,'block_length':12,'seed':POLICY_SEED,'trace':trace,'primary_objective':PARAMS['primary'],'sparse_capture_lift':{'front':float(out_f.sum()/sum(expected_f)) if sum(expected_f)>0 else None,'back':float(out_b.sum()/sum(expected_b)) if sum(expected_b)>0 else None},'lift_note':'Diagnostic nominal band width / K/66; not a decision input.'}
    if score is None: return result
    ix=block_indices(len(aa),POLICY_SEED); draws=[]
    for sample in ix:
        value=_score(aa[sample],bb[sample],out_f[sample],out_b[sample],10,30)
        if value is not None: draws.append(value['primary'])
    result['valid_resample_fraction']=len(draws)/len(ix)
    if draws: result['mbb_ci95']=list(map(float,np.quantile(draws,[.025,.975])))
    if result['valid_resample_fraction']>=.95 and result['mbb_ci95'][0]>0: result['policy_gate']='PASS'
    return result

"""Prior-only Phase 3 residual reference and single-sided Page CUSUM."""
import numpy as np

def errors(actual_front,prediction_front,actual_back,prediction_back):
    a,p,b,q=(np.asarray(x,dtype=float) for x in (actual_front,prediction_front,actual_back,prediction_back))
    if a.ndim!=1 or not len(a) or any(x.shape!=a.shape or not np.isfinite(x).all() for x in (a,p,b,q)): raise ValueError('INVALID_RESIDUAL_ARRAYS')
    return .5*(np.abs(a-p)+np.abs(b-q))

def _residuals(values):
    x=np.asarray(values,dtype=float)
    if x.ndim!=1 or not len(x) or not np.isfinite(x).all() or np.any(x<0): raise ValueError('INVALID_RESIDUALS')
    return x

def reference(values,trained_through_issue):
    x=_residuals(values)
    if len(x)<2 or not isinstance(trained_through_issue,str) or len(trained_through_issue)!=5 or not trained_through_issue.isascii() or not trained_through_issue.isdigit(): raise ValueError('INVALID_REFERENCE')
    return {'mu0':float(x.mean()),'sigma0':max(float(x.std(ddof=0)),1e-6),'reference_through_issue':trained_through_issue,'reference_rows':len(x),'k':.5,'h':5.,'window':20}

def evaluate(values,ref):
    x=_residuals(values)
    if len(x)<20: raise ValueError('INSUFFICIENT_DRIFT_ROWS')
    mu,sigma=float(ref['mu0']),float(ref['sigma0'])
    if not np.isfinite([mu,sigma]).all() or mu<0 or sigma<1e-6 or (ref['k'],ref['h'],ref['window'])!=(.5,5.,20): raise ValueError('INVALID_REFERENCE')
    s=0.; trace=[]
    for e in x:
        s=max(0.,s+(float(e)-mu)/sigma-.5);trace.append(s)
    recent=trace[-20:]
    return {'drift_gate':'TRIPPED' if max(recent)>=5. else 'PASS','recent_trajectory':recent,'last_statistic':trace[-1],'reference':dict(ref)}

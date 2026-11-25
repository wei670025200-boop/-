"""Final pre-data statistical and integrity rules. No data retrieval or I/O."""
from datetime import date
import hashlib
import json
import math
import re
import numpy as np
from .prereg_core import PARAMS
from .digital_twin import mse_diagnostic

HOLIDAYS={(1,1):(1,), (2,14):(2,14), (5,1):(5,1), (6,1):(6,1), (7,1):(7,1), (8,1):(8,1), (10,1):(10,1), (11,11):(11,), (12,25):(12,25)}
DIGEST_KEYS=('history_prefix_sha256','twin_artifact_sha256','statutory_rule_sha256','prereg_spec_sha256','candidate_grid_sha256','oos_trace_sha256')
COMBINED_SEED=2026112503
POLICY_SEED=2026112504

def calendar_pulse(day):
    if type(day) is not date: raise ValueError('INVALID_DRAW_DATE')
    values={day.month,day.day}
    for year in (day.year-1,day.year,day.year+1):
        if not 1<=year<=9999: continue
        for (m,d),numbers in HOLIDAYS.items():
            if abs((day-date(year,m,d)).days)<=3: values.update(numbers)
    return frozenset(x for x in values if 1<=x<=35)

def block_indices(n,seed):
    length=PARAMS['block_length']; B=PARAMS['bootstrap_B']
    if type(n) is not int or n<length: raise ValueError('INSUFFICIENT_MBB_ROWS')
    rng=np.random.default_rng(seed)
    starts=rng.integers(0,n-length+1,size=(B,math.ceil(n/length)))
    return (starts[:,:,None]+np.arange(length)).reshape(B,-1)[:,:n]

def official_oos_gate(arrays):
    from .digital_twin import oos_diagnostics
    report=oos_diagnostics(arrays)
    errors=[]
    for zone in ('front','back'):
        y,b,m=(np.asarray(arrays[k+'_'+zone],dtype=float) for k in ('actual','baseline','prediction'))
        errors.append(((y-b)**2,(y-m)**2))
    if len(errors[0][0])!=len(errors[1][0]): raise ValueError('OOS_ALIGNMENT')
    ix=block_indices(len(errors[0][0]),COMBINED_SEED); draws=[]
    for eb,em in errors:
        den=eb[ix].mean(axis=1)
        if np.any(den<=0): raise ValueError('DEGENERATE_BOOTSTRAP_MSE')
        draws.append((den-em[ix].mean(axis=1))/den)
    f,b=report['front'],report['back']
    lo,hi=np.quantile(.5*(draws[0]+draws[1]),[.025,.975])
    passed=lo>0 and min(f['relative_improvement'],b['relative_improvement'])>0 and max(f['mbb_ci95'][0],b['mbb_ci95'][0])>0
    report.pop('provisional_oos_gate',None)
    report.update(combined={'relative_improvement':.5*(f['relative_improvement']+b['relative_improvement']),'mbb_ci95':[float(lo),float(hi)],'block_length':12,'B':5000,'seed':COMBINED_SEED},oos_gate='PASS' if passed else 'FAIL',gate_rule='COMBINED_CI_POSITIVE_BOTH_MEANS_POSITIVE_AT_LEAST_ONE_INDIVIDUAL_CI_POSITIVE')
    return report

def canonical_json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

def sha256(raw):
    if type(raw) is not bytes: raise TypeError('BYTES_REQUIRED')
    return hashlib.sha256(raw).hexdigest()

def make_integrity(digests,git_commit_sha):
    if set(digests)!=set(DIGEST_KEYS) or any(not isinstance(x,str) or re.fullmatch('[0-9a-f]{64}',x) is None for x in digests.values()): raise ValueError('INVALID_INTEGRITY_DIGESTS')
    if not isinstance(git_commit_sha,str) or re.fullmatch('[0-9a-f]{40}',git_commit_sha) is None: raise ValueError('INVALID_GIT_PROVENANCE')
    return {**digests,'integrity_summary_sha256':sha256(canonical_json(digests)),'git_commit_sha':git_commit_sha,'digital_signature':False}

def verify_integrity(receipt,actual_digests):
    expected=make_integrity(actual_digests,receipt.get('git_commit_sha'))
    if receipt!=expected: raise ValueError('INTEGRITY_MISMATCH')
    return True

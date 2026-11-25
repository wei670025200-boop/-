"""Cleanroom numerical OOS components; inputs are local or synthetic only.
Pre-data implementation choices: winsorize training y (not OOS y), raw feature
scales, unpenalized intercept, EB shrinkage of back-Ridge training residuals
with zero prior mean, average two ball offsets; paired noncircular MBB.
The provisional OOS rule is both improvement CI lower bounds > 0.
Production policy admission is deliberately not implemented here.
"""
from dataclasses import dataclass
from datetime import date
from itertools import combinations
import math
import numpy as np
from scipy.stats import spearmanr
from .prereg_core import PARAMS, FRONT_NAMES, BACK_NAMES, front_features, back_features, targets, _balls

@dataclass(frozen=True)
class Ridge:
    intercept: float
    weights: tuple
    winsor_low: float
    winsor_high: float
    def predict(self,x):
        a=np.asarray(x,dtype=float)
        if a.ndim not in (1,2) or a.shape[-1]!=len(self.weights) or not np.isfinite(a).all():
            raise ValueError('INVALID_FEATURE_MATRIX')
        return self.intercept+a@np.asarray(self.weights)

def _xy(x,y,d=None):
    x=np.asarray(x,dtype=float); y=np.asarray(y,dtype=float)
    if x.ndim!=2 or y.ndim!=1 or len(x)!=len(y) or not len(y) or (d is not None and x.shape[1]!=d) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('INVALID_TRAINING_DATA')
    return x,y

def fit_ridge(x,y,alpha):
    x,y=_xy(x,y)
    if not math.isfinite(alpha) or alpha<=0: raise ValueError('INVALID_ALPHA')
    low,high=np.quantile(y,PARAMS['winsor']); yw=np.clip(y,low,high)
    xm=x.mean(axis=0); ym=yw.mean(); xc=x-xm
    w=np.linalg.solve(xc.T@xc+float(alpha)*np.eye(x.shape[1]),xc.T@(yw-ym))
    return Ridge(float(ym-xm@w),tuple(map(float,w)),float(low),float(high))

def _pairs(pairs):
    return tuple(_balls(pair,12,2) for pair in pairs)

@dataclass(frozen=True)
class BackModel:
    ridge: Ridge
    ball_bias: tuple
    def predict(self,x,pairs):
        p=_pairs(pairs); scores=np.atleast_1d(self.ridge.predict(x))
        if len(p)!=len(scores): raise ValueError('PAIR_COUNT_MISMATCH')
        bias=np.asarray(self.ball_bias)
        return scores+np.asarray([(bias[a-1]+bias[b-1])/2 for a,b in p])

def fit_back(x,y,pairs):
    x,y=_xy(x,y,len(BACK_NAMES)); p=_pairs(pairs)
    if len(p)!=len(y): raise ValueError('PAIR_COUNT_MISMATCH')
    model=fit_ridge(x,y,PARAMS['l2_alpha_back'])
    residual=np.clip(y,model.winsor_low,model.winsor_high)-model.predict(x)
    counts=np.zeros(12); sums=np.zeros(12)
    for pair,r in zip(p,residual):
        for ball in pair: counts[ball-1]+=1; sums[ball-1]+=r
    bias=sums/(counts+PARAMS['prior_strength'])
    return BackModel(model,tuple(map(float,bias)))

def mse_diagnostic(actual,baseline,model,seed):
    a=np.asarray(actual,dtype=float); b=np.asarray(baseline,dtype=float); m=np.asarray(model,dtype=float)
    if a.ndim!=1 or a.shape!=b.shape or a.shape!=m.shape or len(a)<PARAMS['block_length'] or not all(np.isfinite(x).all() for x in (a,b,m)):
        raise ValueError('INVALID_OOS_ARRAYS')
    eb=(a-b)**2; em=(a-m)**2; base=float(eb.mean()); mod=float(em.mean())
    if base<=0: raise ValueError('DEGENERATE_BASELINE_MSE')
    length=PARAMS['block_length']; B=PARAMS['bootstrap_B']; n=len(a)
    rng=np.random.default_rng(seed)
    starts=rng.integers(0,n-length+1,size=(B,math.ceil(n/length)))
    ix=(starts[:,:,None]+np.arange(length)).reshape(B,-1)[:,:n]
    bb=eb[ix].mean(axis=1); mm=em[ix].mean(axis=1)
    if np.any(bb<=0): raise ValueError('DEGENERATE_BOOTSTRAP_MSE')
    ci=np.quantile((bb-mm)/bb,[.025,.975])
    return {'n':n,'baseline_mse':base,'model_mse':mod,'relative_improvement':(base-mod)/base,'mbb_ci95':list(map(float,ci)),'block_length':length,'B':B,'seed':seed}

def oos_arrays(xf,yf,xb,yb,pairs,initial_train_size=200):
    """Each prediction uses rows strictly before its index; raw y for baseline and score."""
    xf,yf=_xy(xf,yf,len(FRONT_NAMES)); xb,yb=_xy(xb,yb,len(BACK_NAMES)); p=_pairs(pairs)
    if len(yf)!=len(yb) or len(p)!=len(yf) or type(initial_train_size) is not int or initial_train_size<1 or len(yf)-initial_train_size<PARAMS['block_length']:
        raise ValueError('INSUFFICIENT_OOS_ROWS')
    front=[]; back=[]; basef=[]; baseb=[]
    for t in range(initial_train_size,len(yf)):
        fm=fit_ridge(xf[:t],yf[:t],PARAMS['l2_alpha_front'])
        bm=fit_back(xb[:t],yb[:t],p[:t])
        front.append(float(fm.predict(xf[t]))); back.append(float(bm.predict(xb[t:t+1],p[t:t+1])[0]))
        basef.append(float(yf[:t].mean())); baseb.append(float(yb[:t].mean()))
    return {'actual_front':yf[initial_train_size:].tolist(),'actual_back':yb[initial_train_size:].tolist(),'prediction_front':front,'prediction_back':back,'baseline_front':basef,'baseline_back':baseb,'initial_train_size':initial_train_size}

def oos_diagnostics(arrays):
    f=mse_diagnostic(arrays['actual_front'],arrays['baseline_front'],arrays['prediction_front'],PARAMS['bootstrap_seed_front'])
    b=mse_diagnostic(arrays['actual_back'],arrays['baseline_back'],arrays['prediction_back'],PARAMS['bootstrap_seed_back'])
    p=arrays['prediction_back']; y=arrays['actual_back']
    rho=pv=None
    if len(set(p))>1 and len(set(y))>1:
        stat=spearmanr(p,y); rho=float(stat.statistic); pv=float(stat.pvalue)
    passed=f['mbb_ci95'][0]>0 and b['mbb_ci95'][0]>0 and rho is not None
    return {'front':f,'back':b,'back_spearman':{'rho':rho,'p_value':pv},'provisional_oos_gate':'PASS' if passed else 'FAIL','gate_rule':'BOTH_MBB_CI_LOWER_GT_ZERO_AND_SPEARMAN_DEFINED'}

def omission_set(history,n,lo,hi=None):
    """Prior rows only; not seen in provided history => age len(history); tie ascending."""
    ages=[len(history)]*n
    for age,draw in enumerate(reversed(history)):
        for x in draw:
            if not 1<=x<=n: raise ValueError('INVALID_HISTORY_BALL')
            ages[x-1]=min(ages[x-1],age)
    top=sorted(range(1,n+1),key=lambda x:(-ages[x-1],x))[:2]
    return set(top)|{x for x in range(1,n+1) if ages[x-1]>=lo and (hi is None or ages[x-1]<=hi)}

def feature_history(rows,holiday_pulses):
    """Rows must already pass full ledger validation. Explicit holiday mapping required.
    Mapping keys MM-DD, values pulse numbers. First row provides context only.
    """
    if not isinstance(holiday_pulses,dict): raise ValueError('HOLIDAY_PULSE_SPEC_REQUIRED')
    fs=[]; bs=[]; xf=[]; xb=[]; yf=[]; yb=[]; issues=[]; prev_date=None; prev_issue=-1
    for row in rows:
        d=date.fromisoformat(row['date']); issue=int(row['issue'])
        if issue<=prev_issue or (prev_date is not None and d<=prev_date): raise ValueError('HISTORY_ORDER')
        f=_balls(tuple(int(row['f'+str(i)]) for i in range(1,6)),35,5)
        b=_balls(tuple(int(row['b'+str(i)]) for i in range(1,3)),12,2)
        if fs:
            pulse={d.month,d.day}|set(holiday_pulses.get(d.strftime('%m-%d'),()))
            xf.append(front_features(f,previous=fs[-1],calendar_pulse_numbers=pulse,omit_chase_numbers=omission_set(fs,35,12,20)))
            xb.append(back_features(b,previous=bs[-1],omit_chase_numbers=omission_set(bs,12,12)))
            a,z=targets(row['sales_rmb'],row); yf.append(a); yb.append(z); issues.append(row['issue'])
        fs.append(f); bs.append(b); prev_issue=issue; prev_date=d
    return {'xf':xf,'yf':yf,'xb':xb,'yb':yb,'pairs':bs[1:],'issues':issues,'front_history':fs,'back_history':bs}

def latest_back_rankings(model,history):
    if not history: raise ValueError('EMPTY_HISTORY')
    pairs=list(combinations(range(1,13),2)); omit=omission_set(history,12,12)
    x=[back_features(p,previous=history[-1],omit_chase_numbers=omit) for p in pairs]
    scores=model.predict(x,pairs)
    ordered=sorted(zip(pairs,map(float,scores)),key=lambda x:(x[1],x[0]))
    ball=sorted(zip(range(1,13),model.ball_bias),key=lambda x:(-x[1],x[0]))
    return {'ball_bias_high_to_low':[{'ball':i,'bias':v} for i,v in ball],'low_top10':[{'pair':p,'score':v} for p,v in ordered[:10]],'high_top10':[{'pair':p,'score':v} for p,v in sorted(ordered,key=lambda x:(-x[1],x[0]))[:10]]}

def feature_history_frozen(rows):
    """Final holiday calendar, including +/-3 days and year boundaries."""
    from .frozen_protocol import calendar_pulse
    fs=[];bs=[];xf=[];xb=[];yf=[];yb=[];issues=[];prev_date=None;prev_issue=-1
    for row in rows:
        d=date.fromisoformat(row['date']);issue=int(row['issue'])
        if issue<=prev_issue or (prev_date is not None and d<=prev_date): raise ValueError('HISTORY_ORDER')
        f=_balls(tuple(int(row['f'+str(i)]) for i in range(1,6)),35,5)
        b=_balls(tuple(int(row['b'+str(i)]) for i in range(1,3)),12,2)
        if fs:
            xf.append(front_features(f,previous=fs[-1],calendar_pulse_numbers=calendar_pulse(d),omit_chase_numbers=omission_set(fs,35,12,20)))
            xb.append(back_features(b,previous=bs[-1],omit_chase_numbers=omission_set(bs,12,12)))
            a,z=targets(row['sales_rmb'],row);yf.append(a);yb.append(z);issues.append(row['issue'])
        fs.append(f);bs.append(b);prev_issue=issue;prev_date=d
    return {'xf':xf,'yf':yf,'xb':xb,'yb':yb,'pairs':bs[1:],'issues':issues,'front_history':fs,'back_history':bs}

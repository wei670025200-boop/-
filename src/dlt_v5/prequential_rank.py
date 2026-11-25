"""Exact eligible-front percentile and back rank from strictly prior fits.
No probabilities of future outcomes are inferred by this module.
"""
from datetime import date
from itertools import combinations
from functools import lru_cache
import numpy as np
from .prereg_core import front_features,back_features,front_exclusion_reasons,PARAMS
from .digital_twin import omission_set,fit_ridge,fit_back
from .frozen_protocol import calendar_pulse
from .combinadic import rank_combination

@lru_cache(maxsize=1)
def front_universe():
    a=np.asarray(list(combinations(range(1,36),5)),dtype=np.int16)
    gaps=np.diff(a,axis=1);adj=(gaps==1).sum(axis=1);odd=(a%2).sum(axis=1);zones=((a<=12).any(axis=1)&((a>=13)&(a<=24)).any(axis=1)&(a>=25).any(axis=1));sums=a.sum(axis=1);span=a[:,-1]-a[:,0]
    static=np.zeros((len(a),12),dtype=float)
    static[:,0]=a[:,-1]<=31;static[:,1]=np.maximum(0,(a<=12).sum(axis=1)-2)/3
    static[:,2]=np.isin(a%10,[6,8,9]).sum(axis=1)/5;static[:,3]=(a%10==4).sum(axis=1)/5;static[:,4]=(a%5==0).sum(axis=1)/5
    static[:,5]=(sums>=70)&(sums<=115)&(span>=18)&(span<=30)&np.isin(odd,[2,3])&zones&(adj<=1)
    static[:,10]=adj/4;static[:,11]=sum((a[:,i]%10==a[:,j]%10).astype(float) for i,j in combinations(range(5),2))/10
    excluded=(adj>=3)|(gaps==gaps[:,:1]).all(axis=1)|(odd==0)|(odd==5)|(a[:,-1]<=17)|(a[:,0]>=18)|((a>=32).sum(axis=1)>=3)
    for v in (a,static,excluded):v.flags.writeable=False
    return a,static,excluded

def dynamic_front_features(a,previous,pulse,omit):
    neighbors={v for x in previous for v in (x-1,x+1) if 1<=v<=35}
    out=[]
    for values in (pulse,previous,neighbors,omit):
        lookup=np.zeros(36,dtype=np.uint8);lookup[list(values)]=1;out.append(lookup[a].sum(axis=1)/5)
    return np.column_stack(out)

def front_percentile(model,actual,previous,day,omit):
    a,static,excluded=front_universe();i=rank_combination(tuple(x-1 for x in actual));eligible=not bool(excluded[i]);count=int((~excluded).sum())
    if not eligible:return 1.,False,count
    w=np.asarray(model.weights);dynamic=dynamic_front_features(a,previous,calendar_pulse(day),omit)
    scores=static@w+dynamic@w[6:10]+model.intercept;target=scores[i]
    # Lexicographic row order is the deterministic tie-break; 1-based rank / eligible count.
    rank=1+int(((~excluded)&((scores<target)|((scores==target)&(np.arange(len(a))<i)))).sum())
    return rank/count,True,count

BACK_PAIRS=tuple(combinations(range(1,13),2))
def back_rank(model,actual,previous,omit):
    xs=[back_features(p,previous=previous,omit_chase_numbers=omit) for p in BACK_PAIRS]
    scores=model.predict(xs,BACK_PAIRS);order=sorted(range(66),key=lambda i:(scores[i],BACK_PAIRS[i]));return order.index(BACK_PAIRS.index(tuple(actual)))+1

def rank_trace(features,dates,initial=200):
    fs=features['front_history'];bs=features['back_history'];xf=features['xf'];xb=features['xb'];yf=features['yf'];yb=features['yb'];pairs=features['pairs']
    if len(dates)!=len(fs) or len(fs)!=len(bs) or len(xf)!=len(fs)-1 or len(xf)!=len(xb) or len(xf)!=len(yf) or len(xf)!=len(yb) or len(xf)!=len(pairs) or len(xf)<=initial:raise ValueError('RANK_TRACE_ALIGNMENT')
    out=[]
    for t in range(initial,len(xf)):
        fm=fit_ridge(xf[:t],yf[:t],PARAMS['l2_alpha_front']);bm=fit_back(xb[:t],yb[:t],pairs[:t]);day=date.fromisoformat(dates[t+1]);fr,ok,count=front_percentile(fm,fs[t+1],fs[t],day,omission_set(fs[:t+1],35,12,20));br=back_rank(bm,bs[t+1],bs[t],omission_set(bs[:t+1],12,12))
        out.append({'issue':features['issues'][t],'front_rank':fr,'front_eligible':ok,'eligible_front_count':count,'back_rank':br})
    return out

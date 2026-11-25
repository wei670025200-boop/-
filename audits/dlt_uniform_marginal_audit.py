"""Fixed 100,000-replay audit of the real V4 sampler, n=8, slots 0 and 7.
Front ball-count Pearson statistic adjusted for within-ticket sampling without replacement.
Back pair categories tested against uniform 1/66. Four tests, Bonferroni 0.01/4.
"""
import hashlib
from itertools import combinations
import json
import numpy as np
from scipy.stats import chi2
from dlt_v5.balanced_coverage import balanced_tickets, UNIFORM_CLAIM
N=100000
pairs={p:i for i,p in enumerate(combinations(range(1,13),2))}
f=np.zeros((2,35),dtype=np.int64);b=np.zeros((2,66),dtype=np.int64)
for i in range(N):
    seed=hashlib.sha256(b'DLT_CLEANROOM_UNIFORM_AUDIT_V1\0'+i.to_bytes(8,'big')).digest()
    batch=balanced_tickets(8,seed)
    for j,slot in enumerate((0,7)):
        front,back=batch[slot]
        for x in front:f[j,x-1]+=1
        b[j,pairs[back]]+=1
out=[]
for j,slot in enumerate((0,7)):
    for name,obs,exp,df,correction in [('front_ball',f[j],N*5/35,34,34/30),('back_pair',b[j],N/66,65,1)]:
        stat=float(((obs-exp)**2/exp).sum()*correction);p=float(chi2.sf(stat,df));out.append({'slot':slot,'test':name,'chi_square':stat,'df':df,'p_value':p,'pass':p>=.0025})
print(json.dumps({'audit':'DLT_CLEANROOM_UNIFORM_AUDIT_V1','replays':N,'batch_n':8,'deterministic_audit_seeds':True,'claim':UNIFORM_CLAIM,'family_alpha':.01,'results':out,'pass':all(x['pass'] for x in out)},indent=2));assert all(x['pass'] for x in out)

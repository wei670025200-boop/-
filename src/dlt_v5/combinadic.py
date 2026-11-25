from __future__ import annotations
import math,itertools
import numpy as np

def rank_combination(c,n=35,k=5):
    c=tuple(sorted(c));rank=0;prev=-1
    for i,x in enumerate(c):
        for v in range(prev+1,x): rank+=math.comb(n-1-v,k-1-i)
        prev=x
    return rank

def unrank_combination(rank,n=35,k=5):
    if not 0<=rank<math.comb(n,k): raise ValueError('rank')
    out=[];x=0
    for i in range(k):
        while True:
            cnt=math.comb(n-1-x,k-1-i)
            if rank<cnt:out.append(x);x+=1;break
            rank-=cnt;x+=1
    return tuple(out)

def overlap_shell_counts(n=35,k=5):
    return {j:math.comb(k,j)*math.comb(n-k,k-j) for j in range(k+1)}

def expected_front_shell_identity(): return {5:1,4:150,3:4350,2:40600,1:137025,0:142506}

def shell_indices(base,n=35,k=5):
    b=set(base); rest=[x for x in range(n) if x not in b]; bs=sorted(b);out={j:[] for j in range(k+1)}
    for j in range(k+1):
        for keep in itertools.combinations(bs,j):
            for add in itertools.combinations(rest,k-j): out[j].append(rank_combination((*keep,*add),n,k))
    return out

def weighted_shell_sums(base,weights):
    sh=shell_indices(base);return {j:float(np.asarray(weights)[idx].sum()) for j,idx in sh.items()}

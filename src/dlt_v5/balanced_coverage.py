"""Offline V4 balanced coverage primitives. Not a production transaction engine."""
import hashlib
import hmac
import itertools
import os
from functools import lru_cache
from collections import Counter
import numpy as np
from .prereg_core import SPACE, _balls

UNIFORM_CLAIM='CRYPTOGRAPHICALLY_UNIFORM_MARGINAL_UNDER_CSPRNG_ASSUMPTION'
COVERAGE_CLAIM='EXACT_ENUMERATED_BATCH_COVERAGE'

class DomainDRBG:
    """HMAC-SHA256 DRBG with explicit personalization and buffered rejection sampling."""
    def __init__(self,seed,domain):
        if type(seed) is not bytes or len(seed)!=32 or type(domain) is not bytes or not domain:
            raise ValueError('INVALID_DRBG_INPUT')
        self.key=b'\0'*32; self.value=b'\1'*32; self.buffer=b''
        self._update(seed+b'\0DLT_V4\0'+domain)
    def _mac(self,data): return hmac.digest(self.key,data,'sha256')
    def _update(self,data=b''):
        self.key=self._mac(self.value+b'\0'+data); self.value=self._mac(self.value)
        if data:
            self.key=self._mac(self.value+b'\1'+data); self.value=self._mac(self.value)
    def _generate(self):
        self.value=self._mac(self.value); a=self.value
        self.value=self._mac(self.value); b=self.value
        self._update(); return a+b
    def randbelow(self,n):
        if type(n) is not int or n<1: raise ValueError('INVALID_BOUND')
        if n==1: return 0
        width=((n-1).bit_length()+7)//8; limit=(1<<(8*width)); limit-=limit%n
        while True:
            while len(self.buffer)<width: self.buffer+=self._generate()
            data,self.buffer=self.buffer[:width],self.buffer[width:]
            v=int.from_bytes(data,'big')
            if v<limit: return v%n
    def permutation(self,n):
        a=list(range(1,n+1))
        for i in range(n-1,0,-1):
            j=self.randbelow(i+1); a[i],a[j]=a[j],a[i]
        return a

def _count(n):
    if type(n) is not int or n<1: raise ValueError('INVALID_TICKET_COUNT')
    return n

@lru_cache(maxsize=128)
def front_templates(n):
    """Complete seven-ticket partitions + bounded deterministic overlap minimization.
    Independent final S35 is applied *after* template optimization.
    """
    _count(n); rows=[]; masks=[]
    for offset in range(0,n,7):
        m=min(7,n-offset); best=None
        for candidate in range(1 if offset==0 else 32):
            labels=range(35) if offset==0 else sorted(range(35),key=lambda x:hashlib.sha256(f'DLT_TEMPLATE_V1:{offset}:{candidate}:{x}'.encode()).digest())
            labels=list(labels); groups=tuple(tuple(sorted(labels[5*i:5*i+5])) for i in range(m))
            gm=[sum(1<<v for v in g) for g in groups]
            score=sum((a&b).bit_count()**2 for a in gm for b in masks)
            key=(score,groups)
            if best is None or key<best[0]: best=(key,gm)
        rows.extend(best[0][1]); masks.extend(best[1])
    return tuple(rows)

@lru_cache(maxsize=1)
def back_cycle():
    a=list(range(12)); result=[]
    for _ in range(11):
        result.extend(tuple(sorted((a[i],a[-1-i]))) for i in range(6))
        a=[a[0],a[-1],*a[1:-1]]
    assert len(set(result))==66
    return tuple(result)

def balanced_tickets(n,seed=None):
    """When seed is omitted, consume 256 bits of OS entropy. Explicit seed is for replay/audit."""
    _count(n)
    if seed is None: seed=os.urandom(32)
    fp=DomainDRBG(seed,b'FRONT_GLOBAL_S35').permutation(35)
    bp=DomainDRBG(seed,b'BACK_GLOBAL_S12').permutation(12)
    front=front_templates(n); back=back_cycle()
    return tuple((tuple(sorted(fp[x] for x in front[i])),tuple(sorted(bp[x] for x in back[i%66]))) for i in range(n))

def frequencies(tickets):
    f=Counter(x for front,_ in tickets for x in front); b=Counter(x for _,back in tickets for x in back)
    return tuple(f[x] for x in range(1,36)),tuple(b[x] for x in range(1,13))

@lru_cache(maxsize=1)
def _all_front_masks():
    return np.fromiter((sum(1<<(v-1) for v in front) for front in itertools.combinations(range(1,36),5)),dtype=np.uint64,count=324632)

@lru_cache(maxsize=66)
def _back_masks(pair):
    target=set(pair); pairs=tuple(itertools.combinations(range(1,13),2))
    exact=sum(1<<i for i,p in enumerate(pairs) if set(p)==target)
    sharing=sum(1<<i for i,p in enumerate(pairs) if target.intersection(p))
    return exact,sharing

def _popcount(a):
    if hasattr(np,'bitwise_count'): return np.bitwise_count(a)
    return np.unpackbits(a.view(np.uint8).reshape(-1,8),axis=1).sum(axis=1)

def exact_batch_coverage(tickets):
    """Exact enumeration of all front outcomes with a 66-bit back-outcome union per front.
    Counts the union of any-prize outcomes, not expected profit or tier payout.
    """
    tickets=tuple((_balls(f,35,5),_balls(b,12,2)) for f,b in tickets)
    if not tickets: raise ValueError('EMPTY_BATCH')
    if len(set(b for _,b in tickets))==66:
        won=SPACE
    else:
        universe=_all_front_masks(); lo=np.zeros(len(universe),dtype=np.uint64); hi=np.zeros(len(universe),dtype=np.uint64)
        all_lo=(1<<64)-1
        for front,back in set(tickets):
            fm=sum(1<<(v-1) for v in front); match=_popcount(universe & np.uint64(fm))
            exact,sharing=_back_masks(back)
            lo |= np.where(match>=3,np.uint64(all_lo),np.where(match>=2,np.uint64(sharing&all_lo),np.uint64(exact&all_lo)))
            hi |= np.where(match>=3,np.uint64(3),np.where(match>=2,np.uint64(sharing>>64),np.uint64(exact>>64)))
        won=int((_popcount(lo)+_popcount(hi)).sum(dtype=np.int64))
    return {'schema':COVERAGE_CLAIM,'winning_outcomes':won,'total_outcomes':SPACE,'prize_coverage':won/SPACE,'profit_guarantee':'NONE','unique_back_pairs':len(set(b for _,b in tickets))}

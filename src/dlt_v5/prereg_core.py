"""Frozen deterministic components of DLT_V5_PREREG_CLEANROOM_1.0.
No real-data access, model fitting, policy admission or ticket generation here.
"""
from math import comb, isfinite, log
from collections import Counter
from .statutory_liability import tier_for_matches

PROTOCOL = 'DLT_V5_PREREG_CLEANROOM_1.0'
SPACE = 21425712
COUNTS = (1, 20, 195, 3000, 11100, 127600, 1287281)
FRONT_NAMES = ('all_birthday_le31','month_cluster_excess','lucky_tail_ratio','taboo_tail_4_ratio','mult5_ratio','shrink_default_box','calendar_pulse_hits','prev_repeat_hits','prev_neighbor_hits','omit_chase_hits','adj_pair_ratio','same_tail_pair_ratio')
BACK_NAMES = ('back_is_consecutive','back_small_gap_le2','back_same_parity','back_lucky_count','back_taboo_04','back_edge_01_12','back_prev_repeat','back_omit_chase','back_sum_mid')
POLICY_GRID = tuple((band,k) for band in ((.01,.05),(.01,.10),(.01,.20)) for k in (18,24,33))
PARAMS = {'winsor':(.02,.98),'l2_alpha_front':15.0,'l2_alpha_back':12.0,'initial_train_size':200,'block_length':12,'bootstrap_B':5000,'prior_strength':20.0,'inner_warmup':100,'cusum_window':20,'bootstrap_seed_front':2026112501,'bootstrap_seed_back':2026112502,'primary':'ANTI_SHARING_CROWDING_SUPPRESSION','auxiliary':'SPARSE_CAPTURE_LIFT'}

def theory_counts():
    out = Counter()
    for f in range(6):
        for b in range(3):
            tier = tier_for_matches(f,b)
            if tier:
                out[tier] += comb(5,f)*comb(30,5-f)*comb(2,b)*comb(10,2-b)
    return tuple(out['g'+str(i)] for i in range(1,8))

def targets(sales_rmb, counts):
    if isinstance(sales_rmb,bool): raise ValueError('INVALID_SALES')
    sales = float(sales_rmb)
    if not isfinite(sales) or sales <= 0: raise ValueError('INVALID_SALES')
    ratios = {}
    for i in (4,5,6,7):
        v = counts['g'+str(i)]
        if isinstance(v,bool): raise ValueError('INVALID_COUNT')
        v = float(v)
        if not isfinite(v) or v < 0 or not v.is_integer(): raise ValueError('INVALID_COUNT')
        ratios[i] = max(v,1)/(sales/2*(COUNTS[i-1]/SPACE))
    return log(sum(ratios[i] for i in (4,5,6))/3), log(.5*ratios[6]+.5*ratios[7])

def _balls(values,n,k):
    a=tuple(values)
    if len(a)!=k or any(type(x) is not int or not 1<=x<=n for x in a) or len(set(a))!=k:
        raise ValueError('INVALID_BALLS')
    return tuple(sorted(a))

def _context(values,n):
    a=set(values)
    if any(type(x) is not int or not 1<=x<=n for x in a): raise ValueError('INVALID_CONTEXT')
    return a

def front_features(front, *, previous, calendar_pulse_numbers, omit_chase_numbers):
    """Calendar and omission sets must be supplied by a frozen prior-only context builder."""
    f=_balls(front,35,5); prev=set(_balls(previous,35,5))
    pulse=_context(calendar_pulse_numbers,35); omit=_context(omit_chase_numbers,35)
    adj=sum(b-a==1 for a,b in zip(f,f[1:]))
    parity=sum(x%2 for x in f)
    zones={0 if x<=12 else 1 if x<=24 else 2 for x in f}
    box=70<=sum(f)<=115 and 18<=f[-1]-f[0]<=30 and parity in (2,3) and len(zones)==3 and adj<=1
    neighbors={v for x in prev for v in (x-1,x+1) if 1<=v<=35}
    same_tail=sum(x*(x-1)//2 for x in Counter(v%10 for v in f).values())
    fs=set(f)
    return (int(max(f)<=31),max(0,sum(x<=12 for x in f)-2)/3,sum(x%10 in (6,8,9) for x in f)/5,sum(x%10==4 for x in f)/5,sum(x%5==0 for x in f)/5,int(box),len(fs&pulse)/5,len(fs&prev)/5,len(fs&neighbors)/5,len(fs&omit)/5,adj/4,same_tail/10)

def front_exclusion_reasons(front):
    f=_balls(front,35,5); gaps=[b-a for a,b in zip(f,f[1:])]; reasons=[]
    if gaps.count(1)>=3: reasons.append('ADJ_PAIRS_GE3')
    if len(set(gaps))==1: reasons.append('ARITHMETIC_PROGRESSION')
    if len({x%2 for x in f})==1: reasons.append('SAME_PARITY')
    if max(f)<=17 or min(f)>=18: reasons.append('EXTREME_HALF')
    if sum(x>=32 for x in f)>=3: reasons.append('HIGH_32_35_OVERLOAD')
    return tuple(reasons)

def back_features(back, *, previous, omit_chase_numbers):
    a,b=_balls(back,12,2); prev=set(_balls(previous,12,2)); omit=_context(omit_chase_numbers,12); pair={a,b}
    return (int(b-a==1),int(b-a<=2),int(a%2==b%2),len(pair&{6,7,8,9})/2,int(4 in pair),len(pair&{1,12})/2,len(pair&prev)/2,len(pair&omit)/2,int(10<=a+b<=16))

def effective_back_k(k,ticket_count):
    if k not in (18,24,33) or type(ticket_count) is not int or ticket_count<1: raise ValueError('INVALID_POLICY')
    return max(k,min(66,ticket_count))

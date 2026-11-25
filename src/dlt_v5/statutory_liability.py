from __future__ import annotations
import math

def _nonnegative_rmb(value):
    if isinstance(value,bool): raise ValueError('INVALID_RMB')
    try: v=float(value)
    except (ValueError,TypeError,OverflowError) as exc: raise ValueError('INVALID_RMB') from exc
    if not math.isfinite(v) or v<0: raise ValueError('INVALID_RMB')
    return v

def tier_for_matches(f,b):
    m=(f,b)
    if m==(5,2):return 'g1'
    if m==(5,1):return 'g2'
    if m in {(5,0),(4,2)}:return 'g3'
    if m==(4,1):return 'g4'
    if m in {(4,0),(3,2)}:return 'g5'
    if m in {(3,1),(2,2)}:return 'g6'
    if m in {(3,0),(2,1),(1,2),(0,2)}:return 'g7'
    return None

def fixed_awards(pool_after_rmb):
    pool_after_rmb=_nonnegative_rmb(pool_after_rmb)
    return {'g3':6666,'g4':380,'g5':200,'g6':18,'g7':7} if pool_after_rmb>=800_000_000 else {'g3':5000,'g4':300,'g5':150,'g6':15,'g7':5}

def plug_in_statutory_rmb_exposure(tickets,outcome,pool_after_rmb,g1_basic_rmb,g2_basic_rmb,promo_balance_remaining_rmb=None):
    pool_after_rmb=_nonnegative_rmb(pool_after_rmb)
    g1_basic_rmb=_nonnegative_rmb(g1_basic_rmb);g2_basic_rmb=_nonnegative_rmb(g2_basic_rmb)
    if promo_balance_remaining_rmb is not None: promo_balance_remaining_rmb=_nonnegative_rmb(promo_balance_remaining_rmb)
    of=set(outcome['front']);ob=set(outcome['back']);fixed=fixed_awards(pool_after_rmb)
    rows=[];gross={'g1':0.0,'g2':0.0,'fixed':0.0}
    for t in tickets:
        fm=len(of&set(t['front']));bm=len(ob&set(t['back']));tier=tier_for_matches(fm,bm);pay=0.0
        if tier=='g1': pay=float(g1_basic_rmb)*(1.8 if t.get('is_additional_bet') else 1.0);gross['g1']+=pay
        elif tier=='g2': pay=float(g2_basic_rmb)*(1.8 if t.get('is_additional_bet') else 1.0);gross['g2']+=pay
        elif tier in fixed: pay=float(fixed[tier]);gross['fixed']+=pay
        rows.append({'tier':tier,'front_matches':fm,'back_matches':bm,'raw_statutory_rmb':pay})
    g1=min(gross['g1'],100_000_000.0);g2=min(gross['g2'],100_000_000.0);total=g1+g2+gross['fixed']
    if promo_balance_remaining_rmb is not None: total=min(total,float(promo_balance_remaining_rmb))
    return {'schema':'PLUG_IN_STATUTORY_RMB_EXPOSURE_V1','pool_snapshot_rmb':pool_after_rmb,'fixed_award_schedule':fixed,'g1_after_aggregate_cap_rmb':g1,'g2_after_aggregate_cap_rmb':g2,'fixed_tier_rmb':gross['fixed'],'total_after_caps_rmb':total,'promo_balance_remaining_rmb':promo_balance_remaining_rmb,'artificial_exposure_weights':'NONE','ticket_rows':rows}

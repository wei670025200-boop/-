from __future__ import annotations
from datetime import date as Date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

HISTORY_FIELDS=['issue','date','f1','f2','f3','f4','f5','b1','b2','sales_rmb','pool_after_rmb','g1','g1_add','g2','g2_add','g3','g4','g5','g6','g7','rule_epoch','promo_active']
CUTOFF_DATE=Date(2026,2,2)
CUTOFF_ISSUE=26014
TIERS='一二三四五六七八九'

def _text(v):
    return '' if v is None else str(v).strip()

def _count(v):
    s=_text(v)
    if not s: return ''
    try: d=Decimal(s)
    except InvalidOperation as exc: raise ValueError('INVALID_STAKE_COUNT') from exc
    if not d.is_finite() or d<0 or d!=d.to_integral_value():
        raise ValueError('INVALID_STAKE_COUNT')
    return str(int(d))

def _sum_or_blank(r,*keys):
    vals=[_count(r.get(k)) for k in keys]
    return '' if '' in vals else str(sum(map(int,vals)))

def _check_epoch(issue,draw_date,epoch):
    issue=_text(issue)
    try: d=Date.fromisoformat(_text(draw_date))
    except ValueError as exc: raise ValueError('RULE_EPOCH_CONFLICT') from exc
    if not re.fullmatch(r'[0-9]{5}',issue) or issue[:2]!=str(d.year)[-2:] or int(issue[2:])==0:
        raise ValueError('RULE_EPOCH_CONFLICT')
    is_new=epoch=='7TIER_2026'
    if (d>=CUTOFF_DATE)!=is_new or (int(issue)>=CUTOFF_ISSUE)!=is_new:
        raise ValueError('RULE_EPOCH_CONFLICT')
    return epoch

def _label(name):
    return ''.join(unicodedata.normalize('NFKC',_text(name)).split())

def normalize_prize_rows(issue,draw_date,prize_rows):
    """Local, pure parser of (prize name, stakeCount) pairs; no I/O."""
    raw={}; promo=0
    for name,value in prize_rows:
        name=_label(name); count=_count(value)
        if count=='': raise ValueError('MISSING_STAKE_COUNT')
        if '派奖' in name:
            promo=max(promo,int(int(count)>0)); continue
        match=re.fullmatch(r'([一二三四五六七八九])等奖(\(追加\)|追加)?',name)
        if not match: raise ValueError('UNKNOWN_PRIZE_LABEL')
        tier=TIERS.index(match.group(1))+1
        if match.group(2) and tier not in (1,2): raise ValueError('UNKNOWN_PRIZE_LABEL')
        key='t'+str(tier)+('_add' if match.group(2) else '')
        if key in raw: raise ValueError('DUPLICATE_PRIZE_LABEL')
        raw[key]=count
    epoch=_check_epoch(issue,draw_date,'9TIER_2019' if ('t8' in raw or 't9' in raw) else '7TIER_2026')
    out={k:_count(raw.get('t'+k[1:])) for k in ('g1','g1_add','g2','g2_add')}
    if epoch=='9TIER_2019':
        out.update(g3=_sum_or_blank(raw,'t3','t4'),g4=_count(raw.get('t5')),g5=_sum_or_blank(raw,'t6','t7'),g6=_count(raw.get('t8')),g7=_count(raw.get('t9')))
    else:
        out.update({'g'+str(i):_count(raw.get('t'+str(i))) for i in range(3,8)})
    out.update(rule_epoch=epoch,promo_active=str(promo))
    return out

def normalize_legacy_9tier_row(r):
    if 't8' not in r and 't9' not in r: raise ValueError('RULE_EPOCH_CONFLICT')
    out={k:'' for k in HISTORY_FIELDS}
    for k in ['issue','date','f1','f2','f3','f4','f5','b1','b2','sales_rmb','pool_after_rmb','promo_active']: out[k]=_text(r.get(k))
    out.update({'g1':_count(r.get('t1')),'g1_add':_count(r.get('t1_add')),'g2':_count(r.get('t2')),'g2_add':_count(r.get('t2_add')),
                'g3':_sum_or_blank(r,'t3','t4'),'g4':_count(r.get('t5')),'g5':_sum_or_blank(r,'t6','t7'),'g6':_count(r.get('t8')),'g7':_count(r.get('t9'))})
    out['rule_epoch']=_check_epoch(out['issue'],out['date'],'9TIER_2019')
    return out

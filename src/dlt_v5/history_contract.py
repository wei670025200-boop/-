from __future__ import annotations
HISTORY_FIELDS=['issue','date','f1','f2','f3','f4','f5','b1','b2','sales_rmb','pool_after_rmb','g1','g1_add','g2','g2_add','g3','g4','g5','g6','g7','rule_epoch','promo_active']

def _sum_or_blank(r,*keys):
    vals=[]
    for k in keys:
        v=str(r.get(k,'') or '').strip()
        if not v:return ''
        vals.append(int(float(v)))
    return str(sum(vals))

def normalize_legacy_9tier_row(r):
    out={k:'' for k in HISTORY_FIELDS}
    for k in ['issue','date','f1','f2','f3','f4','f5','b1','b2','sales_rmb','pool_after_rmb','promo_active']:out[k]=str(r.get(k,'') or '')
    out.update({'g1':str(r.get('t1','') or ''),'g1_add':str(r.get('t1_add','') or ''),'g2':str(r.get('t2','') or ''),'g2_add':str(r.get('t2_add','') or ''),
                'g3':_sum_or_blank(r,'t3','t4'),'g4':str(r.get('t5','') or ''),'g5':_sum_or_blank(r,'t6','t7'),'g6':str(r.get('t8','') or ''),'g7':str(r.get('t9','') or '')})
    out['rule_epoch']='7TIER_2026' if out['date']>='2026-02-02' else '9TIER_2019'
    return out

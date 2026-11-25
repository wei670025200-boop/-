"""Strict local data gate. Synthetic test data is never treated as source evidence."""
import csv
import hashlib
import io
from decimal import Decimal,InvalidOperation
from .history_contract import HISTORY_FIELDS,_count,_check_epoch
from .prereg_core import _balls

HARD_ROWS=650
BUFFER_ROWS=780

def _money(v,positive=False):
    try: d=Decimal(v)
    except (InvalidOperation,TypeError,ValueError) as exc: raise ValueError('INVALID_RMB') from exc
    if not d.is_finite() or d<0 or (positive and d==0): raise ValueError('INVALID_RMB')

def validate_history(raw,schedule):
    if type(raw) is not bytes: raise TypeError('CSV_BYTES_REQUIRED')
    if not raw.endswith(b'\n'): raise ValueError('CSV_UNTERMINATED')
    reader=csv.reader(io.StringIO(raw.decode('utf-8'),newline=''),strict=True)
    if next(reader,None)!=HISTORY_FIELDS: raise ValueError('HISTORY_HEADER_MISMATCH')
    rows=[]; nonzero={'g1_add':0,'g2_add':0}; previous=None
    for fields in reader:
        if len(fields)!=len(HISTORY_FIELDS) or any(v=='' or v.strip()!=v for v in fields): raise ValueError('INCOMPLETE_HISTORY_ROW')
        r=dict(zip(HISTORY_FIELDS,fields)); issue=r['issue']; day=r['date']
        schedule.check(issue,day)
        if previous is not None and (issue,day)!=schedule.next(*previous): raise ValueError('HISTORY_NOT_CONTIGUOUS')
        previous=(issue,day)
        if r['rule_epoch'] not in ('9TIER_2019','7TIER_2026'): raise ValueError('RULE_EPOCH_CONFLICT')
        _check_epoch(issue,day,r['rule_epoch'])
        f=tuple(int(r['f'+str(i)]) for i in range(1,6)); b=tuple(int(r['b'+str(i)]) for i in range(1,3))
        if f!=_balls(f,35,5) or b!=_balls(b,12,2): raise ValueError('NONCANONICAL_BALLS')
        _money(r['sales_rmb'],True);_money(r['pool_after_rmb'])
        for key in ('g1','g1_add','g2','g2_add','g3','g4','g5','g6','g7'):
            value=_count(r[key])
            if not value: raise ValueError('MISSING_STAKE_COUNT')
            if key in nonzero: nonzero[key]+=int(int(value)>0)
        if r['promo_active'] not in ('0','1'): raise ValueError('INVALID_PROMO_FLAG')
        rows.append(r)
    if len(rows)<HARD_ROWS: raise ValueError('DATA_GATE_INSUFFICIENT_650')
    if any(v==0 for v in nonzero.values()): raise ValueError('ADDITIONAL_NONZERO_AUDIT_FAILED')
    report={'total_rows':len(rows),'start_issue':rows[0]['issue'],'start_date':rows[0]['date'],'end_issue':rows[-1]['issue'],'end_date':rows[-1]['date'],'hard_gate_650':'PASS','buffer_780':'PASS' if len(rows)>=BUFFER_ROWS else 'BELOW_TARGET','g1_add_nonzero_rows':nonzero['g1_add'],'g2_add_nonzero_rows':nonzero['g2_add'],'csv_sha256':hashlib.sha256(raw).hexdigest(),'data_gate':'PASS','source_authenticity':'NOT_ESTABLISHED_BY_SCHEMA_VALIDATION'}
    for key in nonzero:
        report[key+'_nonempty_rows']=len(rows);report[key+'_valid_numeric_rows']=len(rows);report[key+'_zero_rows']=len(rows)-nonzero[key]
    return rows,report

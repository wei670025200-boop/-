"""One-time official ledger export. Standard library only. No retries, no imputation."""
import csv
from decimal import Decimal, InvalidOperation
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[2]
# Load the existing pure-standard-library parser without importing the numerical package.
_spec=importlib.util.spec_from_file_location('dlt_history_contract_once',ROOT/'src/dlt_v5/history_contract.py')
history=_spec.loader.create_module(_spec) if hasattr(_spec.loader,'create_module') else None
if history is None: history=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(history)
URL='https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry?gameNo=85&provinceId=0&pageSize=30&isVerify=1&pageNo={page_no}'
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept':'application/json','Referer':'https://static.sporttery.cn/'}

def fail_response(page,status,raw,reason):
    print(f'BOOTSTRAP_HTTP_FAILURE page={page} HTTP_STATUS={status} reason={reason} BODY_PREFIX={raw.decode("utf-8",errors="replace")[:200]!r}',file=sys.stderr,flush=True)
    raise RuntimeError('OFFICIAL_REQUEST_FAILED')

def fetch_page(page):
    try:
        with urlopen(Request(URL.format(page_no=page),headers=HEADERS),timeout=25) as response:
            status=response.status;raw=response.read()
    except HTTPError as exc:
        fail_response(page,exc.code,exc.read(800),'HTTP_ERROR')
    except (URLError,TimeoutError,OSError) as exc:
        fail_response(page,'UNAVAILABLE',b'',str(exc))
    if status!=200:fail_response(page,status,raw,'NON_200')
    try:
        data=json.loads(raw);rows=data['value']['list']
        if data.get('success') is False or not isinstance(rows,list) or not rows:raise ValueError('UNEXPECTED_API_ENVELOPE')
    except (ValueError,TypeError,KeyError) as exc:
        fail_response(page,status,raw,'WAF_OR_INVALID_JSON_ENVELOPE: '+str(exc))
    print(f'BOOTSTRAP_PAGE={page} HTTP_STATUS=200 rows={len(rows)}',flush=True)
    return rows

def numeric(value,integer=False):
    if isinstance(value,bool) or value is None:raise ValueError('INVALID_NUMERIC_FIELD')
    s=str(value).strip()
    if ',' in s:
        if not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?',s):raise ValueError('INVALID_GROUPING')
        s=s.replace(',','')
    try:d=Decimal(s)
    except InvalidOperation as exc:raise ValueError('INVALID_NUMERIC_FIELD') from exc
    if not d.is_finite() or d<0 or (integer and d!=d.to_integral_value()):raise ValueError('INVALID_NUMERIC_FIELD')
    return str(int(d)) if integer else format(d,'f')

def parse_row(row):
    issue=str(row['lotteryDrawNum']).strip();day=str(row['lotteryDrawTime']).strip()
    values=re.split(r'[\s+]+',str(row['lotteryDrawResult']).strip())
    if len(values)!=7 or any(not re.fullmatch(r'\d{1,2}',x) for x in values):raise ValueError('INVALID_DRAW_RESULT')
    f,b=tuple(map(int,values[:5])),tuple(map(int,values[5:]))
    if len(set(f))!=5 or len(set(b))!=2 or not all(1<=x<=35 for x in f) or not all(1<=x<=12 for x in b) or tuple(sorted(f))!=f or tuple(sorted(b))!=b:raise ValueError('INVALID_DRAW_RESULT')
    prize_rows=row['prizeLevelList']
    if not isinstance(prize_rows,list) or not prize_rows:raise ValueError('MISSING_PRIZE_ROWS')
    p=history.normalize_prize_rows(issue,day,[(p['prizeLevel'],numeric(p['stakeCount'],integer=True)) for p in prize_rows])
    sales=numeric(row['totalSaleAmount']);pool=numeric(row['poolBalanceAfterdraw'])
    if Decimal(sales)<=0:raise ValueError('INVALID_SALES')
    out={'issue':issue,'date':day,**{'f'+str(i+1):str(v) for i,v in enumerate(f)},**{'b'+str(i+1):str(v) for i,v in enumerate(b)},'sales_rmb':sales,'pool_after_rmb':pool,**p}
    if any(out.get(k,'')=='' for k in history.HISTORY_FIELDS):raise ValueError('INCOMPLETE_HISTORY_ROW')
    return out

def export():
    out=ROOT/'data/dlt_history.csv'
    if out.exists() or (ROOT/'data/dlt_real_oos_once_claim.json').exists():raise RuntimeError('BOOTSTRAP_ALREADY_PRESENT')
    accepted={};invalid=0;seen=set()
    for page in range(1,29):
        if page>1:time.sleep(.4)
        for raw in fetch_page(page):
            if not isinstance(raw,dict):raise RuntimeError('INVALID_API_ROW_TYPE')
            issue=str(raw.get('lotteryDrawNum',''))
            if issue and issue in seen:raise RuntimeError('DUPLICATE_API_ISSUE: '+issue)
            if issue:seen.add(issue)
            try:r=parse_row(raw)
            except (ValueError,KeyError,TypeError,InvalidOperation) as exc:
                if 'RULE_EPOCH_CONFLICT' in str(exc):raise
                invalid+=1;print(f'BOOTSTRAP_REJECTED_ROW issue={issue!r} reason={str(exc)[:200]}',flush=True);continue
            accepted[r['issue']]=r
        if page>=26 and len(accepted)>=780:break
    if len(accepted)<650:raise RuntimeError(f'INSUFFICIENT_650 complete_rows={len(accepted)} rejected_rows={invalid}')
    out.parent.mkdir(exist_ok=True)
    # Refuse an existing ledger and publish only a complete sorted CSV, then independent validation.
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',newline='',dir=out.parent,delete=False) as fh:
        tmp=Path(fh.name);w=csv.DictWriter(fh,fieldnames=history.HISTORY_FIELDS,lineterminator='\n');w.writeheader();w.writerows(accepted[i] for i in sorted(accepted,key=int))
    try:
        if out.exists():raise RuntimeError('BOOTSTRAP_ALREADY_PRESENT')
        tmp.replace(out)
    finally:
        if tmp.exists():tmp.unlink()
    print(f'BOOTSTRAP_EXPORT_COMPLETE pages={page} complete_rows={len(accepted)} rejected_rows={invalid} buffer_780={len(accepted)>=780}',flush=True)

if __name__=='__main__':export()

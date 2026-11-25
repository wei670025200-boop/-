"""One-time DLT ledger bootstrap from kaijiang.500.com.
Standard library only. Fail closed: no retries, no imputation, no silent row skips.
This file is intentionally isolated under tools/bootstrap_once/.
"""
from __future__ import annotations
import argparse,csv
from datetime import date
from decimal import Decimal,InvalidOperation
from html import unescape
from html.parser import HTMLParser
import importlib.util,json
from pathlib import Path
import re,sys,tempfile,time,unicodedata
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[2]
_spec=importlib.util.spec_from_file_location("dlt_history_contract_once",ROOT/"src/dlt_v5/history_contract.py")
history=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(history)

LIST_XML="https://kaijiang.500.com/static/info/kaijiang/xml/dlt/list.xml"
LIST_HTML="https://kaijiang.500.com/dlt.shtml"
ISSUE_URL="https://kaijiang.500.com/shtml/dlt/{issue}.shtml"
HEADERS={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"zh-CN,zh;q=0.9,en;q=0.5","Referer":"https://kaijiang.500.com/dlt.shtml"}
INTERVAL_SECONDS=0.3
TARGET_ROWS=780
HARD_ROWS=650
TIERS="一二三四五六七八九"

def _snippet(raw):
    for enc in ("utf-8","gb18030"):
        try:return raw[:1200].decode(enc)[:300].replace("\r"," ").replace("\n"," ")
        except UnicodeError:pass
    return raw[:600].decode("latin1",errors="replace")[:300].replace("\r"," ").replace("\n"," ")

def fetch(url,label,fatal=True):
    try:
        with urlopen(Request(url,headers=HEADERS),timeout=25) as response:
            status=response.status;raw=response.read();content_type=response.headers.get("Content-Type","")
    except HTTPError as exc:
        raw=exc.read(1200);print(f"HTTP_FETCH label={label} status={exc.code} url={url} body_prefix={_snippet(raw)!r}",flush=True)
        if fatal:raise RuntimeError(f"HTTP_FAILURE:{label}:{exc.code}")
        return None
    except (URLError,TimeoutError,OSError) as exc:
        print(f"HTTP_FETCH label={label} status=UNAVAILABLE url={url} error={str(exc)[:240]!r}",flush=True)
        if fatal:raise RuntimeError(f"HTTP_FAILURE:{label}:UNAVAILABLE")
        return None
    print(f"HTTP_FETCH label={label} status={status} bytes={len(raw)} url={url}",flush=True)
    if status!=200:
        print(f"HTTP_BODY_PREFIX label={label} body_prefix={_snippet(raw)!r}",flush=True)
        if fatal:raise RuntimeError(f"HTTP_FAILURE:{label}:{status}")
        return None
    return raw,content_type,status

def decode_page(raw,content_type=""):
    candidates=[]
    m=re.search(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)",content_type,re.I)
    if m:candidates.append(m.group(1))
    prefix=raw[:4096].decode("ascii",errors="ignore")
    m=re.search(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)",prefix,re.I)
    if m:candidates.append(m.group(1))
    candidates.extend(("utf-8","gb18030"))
    seen=set()
    for enc in candidates:
        enc=enc.lower()
        if enc in seen:continue
        seen.add(enc)
        try:return raw.decode(enc),enc
        except (LookupError,UnicodeError):pass
    raise ValueError("UNSUPPORTED_PAGE_ENCODING")

def normalize_text(value):
    return "".join(unicodedata.normalize("NFKC",unescape(str(value))).replace("\xa0"," ").split())

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self._row=None;self._cell=None;self._cell_attrs=None;self._captures=[];self.elements=[];self.text_parts=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=="tr":self._row=[]
        if tag in ("td","th") and self._row is not None:self._cell=[];self._cell_attrs=attrs
        if tag in ("li","span","em","strong","b","i","div","p"):self._captures.append({"tag":tag,"attrs":attrs,"text":[]})
    def handle_data(self,data):
        if data:
            self.text_parts.append(data)
            if self._cell is not None:self._cell.append(data)
            for item in self._captures:item["text"].append(data)
    def handle_endtag(self,tag):
        if tag in ("td","th") and self._cell is not None and self._row is not None:
            rs=str(self._cell_attrs.get("rowspan","1") or "1");cs=str(self._cell_attrs.get("colspan","1") or "1")
            self._row.append({"text":" ".join(self._cell),"rowspan":int(rs) if rs.isdigit() else 1,"colspan":int(cs) if cs.isdigit() else 1})
            self._cell=None;self._cell_attrs=None
        if tag=="tr" and self._row is not None:
            if self._row:self.rows.append(self._row)
            self._row=None
        if tag in ("li","span","em","strong","b","i","div","p"):
            for i in range(len(self._captures)-1,-1,-1):
                if self._captures[i]["tag"]==tag:
                    item=self._captures.pop(i);self.elements.append({"tag":tag,"attrs":item["attrs"],"text":" ".join(item["text"])});break

def money_to_rmb(number,unit):
    s=normalize_text(number).replace(",","")
    try:value=Decimal(s)
    except InvalidOperation as exc:raise ValueError("INVALID_MONEY") from exc
    if not value.is_finite() or value<0:raise ValueError("INVALID_MONEY")
    value*= {"亿元":Decimal(100000000),"万元":Decimal(10000),"元":Decimal(1),"":Decimal(1)}[unit or ""]
    if value!=value.to_integral_value():raise ValueError("NON_INTEGER_RMB")
    return str(int(value))

def extract_money(text,labels):
    flat=" ".join(text.split())
    for label in labels:
        m=re.search(rf"{label}\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|元)?",flat,re.I)
        if m:return money_to_rmb(m.group(1),m.group(2) or "")
    raise ValueError("MISSING_MONEY_FIELD:"+labels[0])

def extract_date(text,issue):
    for pattern in (r"(?:开奖日期|开奖时间|开奖日)\s*[:：]?\s*(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?",r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*(?:开奖|开售)?"):
        for m in re.finditer(pattern,text):
            try:d=date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
            except ValueError:continue
            if issue[:2]==str(d.year)[-2:]:return d.isoformat()
    raise ValueError("MISSING_DRAW_DATE")

def valid_balls(values):
    if len(values)!=7:return None
    f=tuple(values[:5]);b=tuple(values[5:])
    if tuple(sorted(f))!=f or tuple(sorted(b))!=b or len(set(f))!=5 or len(set(b))!=2:return None
    if not all(1<=x<=35 for x in f) or not all(1<=x<=12 for x in b):return None
    return f,b

def extract_balls(parser):
    red=[];blue=[]
    for e in parser.elements:
        cls=normalize_text(e["attrs"].get("class","")).lower();token=normalize_text(e["text"])
        if not re.fullmatch(r"\d{1,2}",token):continue
        if "ball" in cls and "red" in cls:red.append(int(token))
        if "ball" in cls and "blue" in cls:blue.append(int(token))
    if len(red)>=5 and len(blue)>=2:
        found=valid_balls(red[:5]+blue[:2])
        if found:return found
    for row in parser.rows:
        joined=" ".join(cell["text"] for cell in row)
        if not any(k in joined for k in ("开奖","前区","后区")):continue
        nums=[int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)",joined)]
        for i in range(max(1,len(nums)-6)):
            found=valid_balls(nums[i:i+7])
            if found:return found
    full=" ".join(parser.text_parts);anchor=re.search(r"开奖号码.{0,260}",full,re.S)
    if anchor:
        nums=[int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)",anchor.group(0))]
        for i in range(max(1,len(nums)-6)):
            found=valid_balls(nums[i:i+7])
            if found:return found
    raise ValueError("MISSING_DRAW_BALLS")

def parse_count_cell(text):
    s=normalize_text(text)
    if any(u in s for u in ("元","万元","亿元","奖金")):return None
    m=re.fullmatch(r"([0-9][0-9,]*)注?",s)
    return str(int(m.group(1).replace(",",""))) if m else None

def extract_prize_rows(parser,issue,draw_date):
    parsed=[];carried_tier=None;carry_left=0
    for row in parser.rows:
        cells=[normalize_text(c["text"]) for c in row];joined="|".join(cells)
        m=re.search(r"([一二三四五六七八九])等奖",joined);tier=None
        if m:
            tier=m.group(1)
            for cell in row:
                if re.search(rf"{tier}等奖",normalize_text(cell["text"])):
                    rs=cell.get("rowspan",1)
                    if rs>1:carried_tier=tier;carry_left=rs-1
                    else:carried_tier=tier
                    break
        elif carry_left>0:
            tier=carried_tier;carry_left-=1
        elif "追加" in joined and carried_tier in ("一","二"):tier=carried_tier
        if tier is None:continue
        is_promo="派奖" in joined;is_add="追加" in joined and not is_promo
        counts=[x for x in (parse_count_cell(c) for c in cells) if x is not None]
        if not counts:raise ValueError(f"MISSING_STAKE_COUNT_ROW:{issue}:{joined[:160]}")
        name=f"{tier}等奖派奖" if is_promo else f"{tier}等奖追加" if is_add else f"{tier}等奖"
        parsed.append((name,counts[0]))
    result=history.normalize_prize_rows(issue,draw_date,parsed)
    required=("g1","g1_add","g2","g2_add","g3","g4","g5","g6","g7","rule_epoch","promo_active")
    if any(result.get(k,"")=="" for k in required):raise ValueError("MISSING_REQUIRED_PRIZE_FIELD")
    if int(result["g1_add"])>int(result["g1"]):raise ValueError("G1_ADD_GT_G1")
    if int(result["g2_add"])>int(result["g2"]):raise ValueError("G2_ADD_GT_G2")
    if int(result["g7"])<=0:raise ValueError("G7_NOT_POSITIVE")
    return result,parsed

def parse_issue_html(issue,raw,content_type):
    html,encoding=decode_page(raw,content_type);parser=TableParser();parser.feed(html);text=" ".join(parser.text_parts)
    if not re.search(rf"(?<!\d){re.escape(issue)}(?!\d)",text):raise ValueError("ISSUE_NOT_PRESENT_IN_PAGE")
    draw_date=extract_date(text,issue);front,back=extract_balls(parser)
    sales=extract_money(text,(r"本期(?:全国)?销售(?:总额|额|金额)?",r"本期销量",r"销售额"))
    pool=extract_money(text,(r"奖池(?:滚存|累计|余额|资金|金额)?",r"滚存奖池"))
    if int(sales)<=0:raise ValueError("SALES_NOT_POSITIVE")
    prize,raw_prize=extract_prize_rows(parser,issue,draw_date)
    row={"issue":issue,"date":draw_date,**{f"f{i+1}":str(v) for i,v in enumerate(front)},**{f"b{i+1}":str(v) for i,v in enumerate(back)},"sales_rmb":sales,"pool_after_rmb":pool,**prize}
    row={k:row[k] for k in history.HISTORY_FIELDS}
    if any(row[k]=="" for k in history.HISTORY_FIELDS):raise ValueError("INCOMPLETE_22_COLUMN_ROW")
    return row,encoding,raw_prize

def issue_candidates(raw,content_type,label):
    text,encoding=decode_page(raw,content_type);found=[]
    for token in re.findall(r"(?<!\d)(\d{5})(?!\d)",text):
        yy=int(token[:2]);serial=int(token[2:])
        if 7<=yy<=99 and 1<=serial<=999:found.append(token)
    issues=sorted(set(found),key=int,reverse=True)
    if not issues:raise ValueError("NO_ISSUES_IN_"+label)
    print(f"ISSUE_LIST_SOURCE={label} encoding={encoding} count={len(issues)} latest={issues[0]} oldest={issues[-1]}",flush=True)
    return issues

def get_issue_list():
    primary=fetch(LIST_XML,"list.xml",fatal=False)
    if primary:
        try:return issue_candidates(primary[0],primary[1],"list.xml")
        except ValueError as exc:print(f"ISSUE_LIST_PRIMARY_PARSE_FAILURE error={exc} body_prefix={_snippet(primary[0])!r}",flush=True)
    fallback=fetch(LIST_HTML,"dlt.shtml",fatal=False)
    if fallback:
        try:return issue_candidates(fallback[0],fallback[1],"dlt.shtml")
        except ValueError as exc:print(f"ISSUE_LIST_FALLBACK_PARSE_FAILURE error={exc} body_prefix={_snippet(fallback[0])!r}",flush=True)
    raise RuntimeError("ISSUE_LIST_UNAVAILABLE")

def fetch_and_parse_issue(issue,probe=False):
    raw,content_type,status=fetch(ISSUE_URL.format(issue=issue),f"issue:{issue}",fatal=True)
    try:row,encoding,raw_prize=parse_issue_html(issue,raw,content_type)
    except Exception as exc:
        print(f"{'PROBE' if probe else 'EXPORT'}_PARSE_FAILURE issue={issue} status={status} error={type(exc).__name__}:{exc} body_prefix={_snippet(raw)!r}",file=sys.stderr,flush=True);raise
    if probe:
        print(f"PROBE_PASS issue={issue} HTTP_STATUS={status} encoding={encoding}",flush=True)
        print("PROBE_ROW_JSON="+json.dumps(row,ensure_ascii=False),flush=True)
        print("PROBE_PRIZE_ROWS="+json.dumps(raw_prize,ensure_ascii=False),flush=True)
    return row

def choose_probe_issues(issues):
    old="25100" if "25100" in issues else next((x for x in issues if x.startswith("25") and int(x)<26014),None)
    new=next((x for x in issues if int(x)>=26014),None)
    if not old or not new:raise RuntimeError("PROBE_ISSUES_UNAVAILABLE")
    return old,new

def probe_only():
    issues=get_issue_list();old,new=choose_probe_issues(issues)
    print(f"PROBE_SELECTION legacy_issue={old} new_issue={new}",flush=True)
    fetch_and_parse_issue(old,probe=True);time.sleep(INTERVAL_SECONDS);fetch_and_parse_issue(new,probe=True)
    print("PROBE_ONLY_RESULT=PASS",flush=True)

def export_full():
    out=ROOT/"data/dlt_history.csv";claim=ROOT/"data/dlt_real_oos_once_claim.json"
    if out.exists() or claim.exists():raise RuntimeError("BOOTSTRAP_ALREADY_PRESENT")
    issues=get_issue_list();selected=issues[:min(TARGET_ROWS,len(issues))]
    if len(selected)<HARD_ROWS:raise RuntimeError(f"ISSUE_LIST_BELOW_650:{len(selected)}")
    accepted={}
    for i,issue in enumerate(selected):
        if i:time.sleep(INTERVAL_SECONDS)
        row=fetch_and_parse_issue(issue)
        if issue in accepted:raise RuntimeError("DUPLICATE_ISSUE:"+issue)
        accepted[issue]=row
        if (i+1)%25==0 or i+1==len(selected):print(f"EXPORT_PROGRESS fetched={i+1}/{len(selected)} issue={issue}",flush=True)
    rows=[accepted[x] for x in sorted(accepted,key=int)];out.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",newline="",dir=out.parent,delete=False) as fh:
        tmp=Path(fh.name);w=csv.DictWriter(fh,fieldnames=history.HISTORY_FIELDS,lineterminator="\n");w.writeheader();w.writerows(rows);fh.flush()
    try:
        if out.exists():raise RuntimeError("BOOTSTRAP_ALREADY_PRESENT")
        tmp.replace(out)
    finally:
        if tmp.exists():tmp.unlink()
    print(f"BOOTSTRAP_EXPORT_COMPLETE complete_rows={len(rows)} start_issue={rows[0]['issue']} end_issue={rows[-1]['issue']} buffer_780={len(rows)>=TARGET_ROWS}",flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument("--probe-only",action="store_true");a=p.parse_args()
    probe_only() if a.probe_only else export_full()

if __name__=="__main__":main()

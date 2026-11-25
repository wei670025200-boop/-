"""One-time DLT ledger export from the public 17500 text archive.
Standard library only. Two HTTP reads total: 17500 full text + 500.com list.xml baseline.
All 780 rows are held in memory, cross-validated and hard-audited before data/dlt_history.csv
is atomically created. No retries, no WAF bypass, no imputation, no silent skips.
"""
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,re,statistics,sys,tempfile
from datetime import date as Date
from decimal import Decimal,InvalidOperation
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
_spec=importlib.util.spec_from_file_location("dlt_history_contract_once",ROOT/"src/dlt_v5/history_contract.py")
history=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(history)

TXT_URL="https://www.17500.cn/getData/dlt.TXT"
BASELINE_URL="https://kaijiang.500.com/static/info/kaijiang/xml/dlt/list.xml"
HEADERS={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Accept":"text/plain,application/xml,text/xml;q=0.9,*/*;q=0.5","Accept-Language":"zh-CN,zh;q=0.9,en;q=0.5"}
TARGET=780
HARD=650
CUT_ISSUE=26014
CUT_DATE=Date(2026,2,2)
TIERS="一二三四五六七八九"
KEY_ISSUES=("25100","26013","26014","26134")


def fetch_once(url,label):
    try:
        with urlopen(Request(url,headers=HEADERS),timeout=25) as r:
            raw=r.read();status=int(r.status);ctype=r.headers.get("Content-Type","")
    except HTTPError as exc:
        raw=exc.read(1200);print(f"HTTP_FAIL label={label} status={exc.code} bytes={len(raw)} body_prefix={raw[:240]!r}",flush=True);raise RuntimeError(f"HTTP_FAIL:{label}:{exc.code}")
    except (URLError,TimeoutError,OSError) as exc:
        print(f"HTTP_FAIL label={label} status=UNAVAILABLE error={type(exc).__name__}:{exc}",flush=True);raise RuntimeError(f"HTTP_FAIL:{label}:UNAVAILABLE")
    challenge=any(x.lower() in raw.lower() for x in (b"EO_Bot_Ssid",b"__jsl_clearance",b"captcha",b"cf-chl-",b"document.cookie"))
    print(f"HTTP_OK label={label} status={status} bytes={len(raw)} content_type={ctype!r} challenge={str(challenge).lower()}",flush=True)
    if status!=200 or challenge:raise RuntimeError(f"HTTP_INVALID:{label}:{status}:challenge={challenge}")
    return raw,ctype


def decode(raw):
    for enc in ("utf-8-sig","utf-8","gb18030","gbk"):
        try:return raw.decode(enc)
        except UnicodeError:pass
    raise ValueError("DECODE_FAILED")


def as_int(s,label):
    if not re.fullmatch(r"\d+",s):raise ValueError(f"INVALID_INT:{label}:{s}")
    return int(s)


def valid_balls(vals):
    if len(vals)!=7:return None
    f=tuple(vals[:5]);b=tuple(vals[5:])
    if len(set(f))!=5 or len(set(b))!=2:return None
    if not all(1<=x<=35 for x in f) or not all(1<=x<=12 for x in b):return None
    return tuple(sorted(f)),tuple(sorted(b))


def parse_txt(raw):
    text=decode(raw);rows={};line_count=len(text.splitlines());nonempty=0
    for line_no,line in enumerate(text.splitlines(),1):
        if not line.strip():continue
        nonempty+=1;cols=line.strip().split()
        if len(cols)!=45:raise ValueError(f"TXT_COLUMN_COUNT line={line_no} columns={len(cols)}")
        issue=cols[0]
        if not re.fullmatch(r"\d{5}",issue):raise ValueError(f"INVALID_ISSUE:{issue}")
        try:day=Date.fromisoformat(cols[1])
        except ValueError as exc:raise ValueError(f"INVALID_DATE:{issue}:{cols[1]}") from exc
        if issue[:2]!=str(day.year)[-2:]:raise ValueError(f"ISSUE_YEAR_CONFLICT:{issue}:{day}")
        sorted_balls=valid_balls([as_int(x,"ball") for x in cols[2:9]])
        draw_order=valid_balls([as_int(x,"draw_order") for x in cols[9:16]])
        if not sorted_balls or not draw_order or sorted_balls!=draw_order:raise ValueError(f"BALL_ORDER_CONFLICT:{issue}")
        if tuple(as_int(x,"front") for x in cols[2:7])!=sorted_balls[0] or tuple(as_int(x,"back") for x in cols[7:9])!=sorted_balls[1]:raise ValueError(f"NONCANONICAL_BALLS:{issue}")
        if issue in rows:raise ValueError(f"DUPLICATE_TXT_ISSUE:{issue}")
        rows[issue]={"issue":issue,"date":day.isoformat(),"front":sorted_balls[0],"back":sorted_balls[1],"cols":cols,"raw_line":line}
    print(f"TXT_PARSE total_lines={line_count} nonempty_lines={nonempty} parsed_rows={len(rows)} latest={max(rows,key=int)} oldest={min(rows,key=int)}",flush=True)
    return rows,{"total_lines":line_count,"nonempty_lines":nonempty,"parsed_rows":len(rows)}


def parse_date_blob(blob):
    m=re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})",blob)
    if not m:return None
    try:return Date(int(m.group(1)),int(m.group(2)),int(m.group(3))).isoformat()
    except ValueError:return None


def parse_ball_blob(blob):
    cleaned=re.sub(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"," ",blob)
    tokens=[int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)",cleaned)]
    for i in range(max(0,len(tokens)-6)):
        got=valid_balls(tokens[i:i+7])
        if got:return got
    return None


def elem_blob(elem,children=False):
    parts=[elem.tag,elem.text or "",*elem.attrib.keys(),*elem.attrib.values()]
    if children:
        for c in list(elem):
            parts.extend([c.tag,c.text or "",*c.attrib.keys(),*c.attrib.values()])
    return " ".join(parts)


def parse_list_xml(raw):
    try:root=ET.fromstring(raw)
    except ET.ParseError as exc:raise ValueError("BASELINE_XML_PARSE") from exc
    found={}
    for elem in root.iter():
        own=elem_blob(elem,False);issues=re.findall(r"(?<!\d)(\d{5})(?!\d)",own)
        if len(set(issues))!=1:
            own=elem_blob(elem,True);issues=re.findall(r"(?<!\d)(\d{5})(?!\d)",own)
        if len(set(issues))!=1:continue
        issue=issues[0]
        day=parse_date_blob(own)
        balls=None
        preferred=[]
        for k,v in elem.attrib.items():
            if any(t in k.lower() for t in ("code","result","number","num","ball","open")):preferred.append(v)
        preferred.extend(elem.attrib.values())
        preferred.extend((c.text or "") for c in list(elem))
        for value in preferred:
            balls=parse_ball_blob(value)
            if balls:break
        if not day or not balls:
            extended=ET.tostring(elem,encoding="unicode")
            day=day or parse_date_blob(extended);balls=balls or parse_ball_blob(extended)
        if day and balls:
            record=(day,balls[0],balls[1])
            if issue in found and found[issue]!=record:raise ValueError(f"BASELINE_DUPLICATE_CONFLICT:{issue}")
            found[issue]=record
    print(f"BASELINE_PARSE records={len(found)} latest={max(found,key=int) if found else None} oldest={min(found,key=int) if found else None}",flush=True)
    if not found:raise ValueError("BASELINE_NO_RECORDS")
    return found


def prize_rows_from_cols(issue,day,cols):
    is_new=(int(issue)>=CUT_ISSUE)
    if (Date.fromisoformat(day)>=CUT_DATE)!=is_new:raise ValueError(f"RULE_EPOCH_CONFLICT:{issue}")
    pairs=[(as_int(cols[18+2*i],f"t{i+1}_count"),as_int(cols[19+2*i],f"t{i+1}_award")) for i in range(9)]
    add1=(as_int(cols[36],"g1_add_count"),as_int(cols[37],"g1_add_award"))
    add2=(as_int(cols[38],"g2_add_count"),as_int(cols[39],"g2_add_award"))
    tail=[as_int(x,"tail") for x in cols[40:45]]
    if any(tail):raise ValueError(f"UNMAPPED_NONZERO_TAIL_FIELDS:{issue}:{tail}")
    if is_new and any(v for pair in pairs[7:] for v in pair):raise ValueError(f"POST_CUTOVER_T8_T9_NONZERO:{issue}")
    ordinary=7 if is_new else 9
    source=[]
    for i in range(ordinary):source.append((f"{TIERS[i]}等奖",str(pairs[i][0])))
    source.append(("一等奖追加",str(add1[0])));source.append(("二等奖追加",str(add2[0])))
    norm=history.normalize_prize_rows(issue,day,source)
    if int(norm["g1_add"])>int(norm["g1"]):raise ValueError(f"G1_ADD_GT_G1:{issue}")
    if int(norm["g2_add"])>int(norm["g2"]):raise ValueError(f"G2_ADD_GT_G2:{issue}")
    if int(norm["g7"])<=0:raise ValueError(f"G7_NOT_POSITIVE:{issue}")
    return norm,{"pairs":pairs,"g1_add":add1,"g2_add":add2,"tail":tail,"source_rows":source}


def canonical_row(item):
    issue=item["issue"];day=item["date"];c=item["cols"]
    sales=as_int(c[16],"sales");pool=as_int(c[17],"pool")
    if not 100_000_000<=sales<=800_000_000:raise ValueError(f"SALES_OUT_OF_RANGE:{issue}:{sales}")
    norm,debug=prize_rows_from_cols(issue,day,c)
    row={"issue":issue,"date":day,**{f"f{i+1}":str(v) for i,v in enumerate(item["front"])},**{f"b{i+1}":str(v) for i,v in enumerate(item["back"])},"sales_rmb":str(sales),"pool_after_rmb":str(pool),**norm}
    row={k:row[k] for k in history.HISTORY_FIELDS}
    if any(v=="" for v in row.values()):raise ValueError(f"INCOMPLETE_CANONICAL_ROW:{issue}")
    return row,debug


def validate_all(txt,baseline):
    issues=sorted(txt,key=int,reverse=True)[:TARGET]
    if len(issues)<HARD:raise ValueError(f"BELOW_HARD_FLOOR:{len(issues)}")
    for key in KEY_ISSUES:
        if key not in issues:raise ValueError(f"KEY_ISSUE_NOT_IN_WINDOW:{key}")
    rows=[];debug={};mismatch=[]
    for issue in issues:
        item=txt[issue]
        if issue not in baseline:raise ValueError(f"BASELINE_MISSING:{issue}")
        bday,bf,bb=baseline[issue]
        if (item["date"],item["front"],item["back"])!=(bday,bf,bb):
            mismatch.append({"issue":issue,"txt":[item["date"],item["front"],item["back"]],"baseline":[bday,bf,bb]})
            continue
        row,d=canonical_row(item);rows.append(row);debug[issue]=d
    if mismatch:raise ValueError("BASELINE_MISMATCH:"+json.dumps(mismatch[:3],ensure_ascii=False))
    if len(rows)!=len(issues):raise ValueError("ROW_COUNT_MISMATCH")
    by_issue={r["issue"]:r for r in rows}
    for key in KEY_ISSUES:
        print(f"RAW_KEY_LINE issue={key} line={txt[key]['raw_line']}",flush=True)
        print("MAPPED_KEY "+json.dumps({"issue":key,"canonical":by_issue[key],"source_debug":debug[key]},ensure_ascii=False),flush=True)
    ordered=sorted(rows,key=lambda r:int(r["issue"]))
    med={g:statistics.median(int(r[g]) for r in ordered) for g in ("g3","g4","g5","g6","g7")}
    if not (med["g7"]>med["g6"]>med["g5"]>med["g4"]>med["g3"]):raise ValueError("PRIZE_COUNT_MEDIAN_ORDER:"+json.dumps(med))
    if not (med["g7"]>1_000_000 and med["g6"]>100_000 and med["g5"]>10_000 and med["g4"]>1_000 and med["g3"]>100):raise ValueError("PRIZE_COUNT_SCALE:"+json.dumps(med))
    g1_add_positive=sum(int(r["g1_add"])>0 for r in ordered);g2_add_positive=sum(int(r["g2_add"])>0 for r in ordered)
    if g1_add_positive<=0 or g2_add_positive<=0:raise ValueError(f"ADDITIONAL_AUDIT_FAIL:g1={g1_add_positive}:g2={g2_add_positive}")
    epochs={r["rule_epoch"] for r in ordered}
    if epochs!={"9TIER_2019","7TIER_2026"}:raise ValueError("EPOCH_COVERAGE_FAIL:"+repr(epochs))
    report={"selected_rows":len(ordered),"start_issue":ordered[0]["issue"],"start_date":ordered[0]["date"],"end_issue":ordered[-1]["issue"],"end_date":ordered[-1]["date"],"baseline_exact_matches":len(ordered),"baseline_mismatches":0,"median_counts":med,"g1_add_positive_rows":g1_add_positive,"g2_add_positive_rows":g2_add_positive,"tail_nonzero_rows":0,"rule_epoch_conflicts":0,"hard_assertions":"PASS"}
    print("HARD_SELF_VALIDATION="+json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
    return ordered,report


def atomic_write(rows,path):
    path.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",newline="",dir=path.parent,delete=False) as fh:
        tmp=Path(fh.name);w=csv.DictWriter(fh,fieldnames=history.HISTORY_FIELDS,lineterminator="\n");w.writeheader();w.writerows(rows);fh.flush()
    try:
        if path.exists():raise RuntimeError("HISTORY_ALREADY_EXISTS")
        tmp.replace(path)
    finally:
        if tmp.exists():tmp.unlink()


def main():
    p=argparse.ArgumentParser();p.add_argument("--audit-out",default="dlt_oos_artifacts/source_selfcheck.json");a=p.parse_args()
    out=ROOT/"data/dlt_history.csv";claim=ROOT/"data/dlt_real_oos_once_claim.json"
    if out.exists() or claim.exists():raise RuntimeError("BOOTSTRAP_ALREADY_PRESENT")
    txt_raw,_=fetch_once(TXT_URL,"17500_DLT_TXT")
    base_raw,_=fetch_once(BASELINE_URL,"500_LIST_XML")
    txt,txt_meta=parse_txt(txt_raw);baseline=parse_list_xml(base_raw)
    rows,report=validate_all(txt,baseline);report["txt_meta"]=txt_meta;report["txt_sha256"]=hashlib.sha256(txt_raw).hexdigest();report["baseline_sha256"]=hashlib.sha256(base_raw).hexdigest()
    audit=ROOT/a.audit_out;audit.parent.mkdir(parents=True,exist_ok=True);audit.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    atomic_write(rows,out)
    print(f"EXPORT_COMPLETE rows={len(rows)} csv_sha256={hashlib.sha256(out.read_bytes()).hexdigest()}",flush=True)

if __name__=="__main__":raise SystemExit(main())

"""One-shot DLT ledger export from the approved 17500 plain-text archive.
Standard library only. Exactly one network source request, no retries, no WAF bypass,
no imputation. The latest 780 rows must pass the frozen 45-column contract in memory
before data/dlt_history.csv is created.
"""
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,re,statistics,tempfile
from datetime import date as Date
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[2]
_spec=importlib.util.spec_from_file_location("dlt_history_contract_once",ROOT/"src/dlt_v5/history_contract.py")
history=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(history)

SOURCE_URL="https://www.17500.cn/getData/dlt.TXT"
HEADERS={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Accept":"text/plain,*/*;q=0.5","Accept-Language":"zh-CN,zh;q=0.9,en;q=0.5"}
TARGET=780
HARD=650
CUT_ISSUE=26014
CUT_DATE=Date(2026,2,2)
KEY_ISSUES=("25100","26013","26014","26015","26134")
OLD_FIXED=(10000,3000,300,200,100)
NEW_FIXED={(5000,300,150,15,5),(6666,380,200,18,7)}
TIERS="一二三四五六七八九"


def fetch_once():
    try:
        with urlopen(Request(SOURCE_URL,headers=HEADERS),timeout=30) as r:
            raw=r.read();status=int(r.status);ctype=r.headers.get("Content-Type","")
    except HTTPError as exc:
        raw=exc.read(1200);print(f"SOURCE_HTTP status={exc.code} bytes={len(raw)} body_prefix={raw[:240]!r}",flush=True);raise RuntimeError(f"SOURCE_HTTP_FAIL:{exc.code}")
    except (URLError,TimeoutError,OSError) as exc:
        print(f"SOURCE_HTTP status=UNAVAILABLE error={type(exc).__name__}:{exc}",flush=True);raise RuntimeError("SOURCE_HTTP_FAIL:UNAVAILABLE")
    challenge=any(x.lower() in raw.lower() for x in (b"EO_Bot_Ssid",b"__jsl_clearance",b"captcha",b"cf-chl-",b"document.cookie"))
    print(f"SOURCE_HTTP status={status} bytes={len(raw)} content_type={ctype!r} challenge={str(challenge).lower()}",flush=True)
    if status!=200 or challenge:raise RuntimeError(f"SOURCE_INVALID:status={status}:challenge={challenge}")
    return raw


def decode(raw):
    for enc in ("utf-8-sig","utf-8","gb18030","gbk"):
        try:return raw.decode(enc)
        except UnicodeError:pass
    raise ValueError("SOURCE_DECODE_FAILED")


def integer(value,label,issue):
    if not re.fullmatch(r"\d+",value):raise ValueError(f"INVALID_INTEGER:{issue}:{label}:{value}")
    return int(value)


def parse_source(raw):
    text=decode(raw);parsed={};total_lines=len(text.splitlines());nonempty=0
    for n,line in enumerate(text.splitlines(),1):
        if not line.strip():continue
        nonempty+=1;c=line.strip().split()
        if len(c)!=45:raise ValueError(f"COLUMN_COUNT:{n}:{len(c)}")
        issue=c[0]
        if not re.fullmatch(r"\d{5}",issue):raise ValueError(f"ISSUE_FORMAT:{n}:{issue}")
        try:d=Date.fromisoformat(c[1])
        except ValueError as exc:raise ValueError(f"DATE_FORMAT:{issue}:{c[1]}") from exc
        if issue[:2]!=str(d.year)[-2:]:raise ValueError(f"ISSUE_DATE_YEAR:{issue}:{d}")
        if issue in parsed:raise ValueError(f"DUPLICATE_ISSUE:{issue}")
        parsed[issue]={"issue":issue,"date":d.isoformat(),"c":c,"raw_line":line}
    print(f"SOURCE_PARSE total_lines={total_lines} nonempty_lines={nonempty} parsed_rows={len(parsed)} latest={max(parsed,key=int)} oldest={min(parsed,key=int)}",flush=True)
    return parsed,{"total_lines":total_lines,"nonempty_lines":nonempty,"parsed_rows":len(parsed)}


def expected_award(count,award,fixed,issue,label):
    if count==0:
        if award!=0:raise ValueError(f"ZERO_COUNT_NONZERO_AWARD:{issue}:{label}:{award}")
    elif award!=fixed:
        raise ValueError(f"FIXED_AWARD_MISMATCH:{issue}:{label}:{award}!={fixed}")


def normalize_row(item):
    issue=item["issue"];day=item["date"];c=item["c"]
    vals=[integer(x,f"c[{i}]",issue) for i,x in enumerate(c) if i!=1]
    def I(i):return integer(c[i],f"c[{i}]",issue)

    front=tuple(I(i) for i in range(2,7));back=tuple(I(i) for i in range(7,9))
    front_order=tuple(I(i) for i in range(9,14));back_order=tuple(I(i) for i in range(14,16))
    if tuple(sorted(front))!=tuple(sorted(front_order)):raise ValueError(f"FRONT_REDUNDANCY:{issue}")
    if tuple(sorted(back))!=tuple(sorted(back_order)):raise ValueError(f"BACK_REDUNDANCY:{issue}")
    if tuple(sorted(front))!=front or len(set(front))!=5 or not all(1<=x<=35 for x in front):raise ValueError(f"FRONT_CANONICAL:{issue}")
    if tuple(sorted(back))!=back or len(set(back))!=2 or not all(1<=x<=12 for x in back):raise ValueError(f"BACK_CANONICAL:{issue}")

    sales=I(16);pool=I(17)
    if not 100_000_000<=sales<=1_000_000_000:raise ValueError(f"SALES_RANGE:{issue}:{sales}")
    if pool<0:raise ValueError(f"POOL_NEGATIVE:{issue}")

    g1=I(18);g1_award=I(19);g2=I(20);g2_award=I(21)
    g1_add=I(36);g1_add_award=I(37);g2_add=I(38);g2_add_award=I(39)
    if g1_add>g1:raise ValueError(f"G1_ADD_GT_G1:{issue}:{g1_add}>{g1}")
    if g2_add>g2:raise ValueError(f"G2_ADD_GT_G2:{issue}:{g2_add}>{g2}")
    add_alignment=[]
    if g1_add>0:
        expected=round(g1_award*.8)
        add_alignment.append({"issue":issue,"tier":"g1_add","base_award":g1_award,"add_award":g1_add_award,"expected_80pct":expected,"abs_diff":abs(g1_add_award-expected),"match":abs(g1_add_award-expected)<=2})
    if g2_add>0:
        expected=round(g2_award*.8)
        add_alignment.append({"issue":issue,"tier":"g2_add","base_award":g2_award,"add_award":g2_add_award,"expected_80pct":expected,"abs_diff":abs(g2_add_award-expected),"match":abs(g2_add_award-expected)<=2})

    is_new=int(issue)>=CUT_ISSUE
    if (Date.fromisoformat(day)>=CUT_DATE)!=is_new:raise ValueError(f"RULE_EPOCH_CONFLICT:{issue}:{day}")
    prize_rows=[("一等奖",str(g1)),("一等奖追加",str(g1_add)),("二等奖",str(g2)),("二等奖追加",str(g2_add))]

    counts=[I(i) for i in (22,24,26,28,30)]
    awards=[I(i) for i in (23,25,27,29,31)]
    if not is_new:
        if I(33)!=15 or I(35)!=5 or I(34)<=0:raise ValueError(f"9TIER_FINGERPRINT:{issue}:{I(32)}:{I(33)}:{I(34)}:{I(35)}")
        for j,(count,award,fixed) in enumerate(zip(counts,awards,OLD_FIXED),3):expected_award(count,award,fixed,issue,f"t{j}")
        prize_rows.extend((f"{TIERS[j-1]}等奖",str(I(18+2*(j-1)))) for j in range(3,10))
        frozen={"g3":I(22)+I(24),"g4":I(26),"g5":I(28)+I(30),"g6":I(32),"g7":I(34)}
        epoch="9TIER_2019"
    else:
        if tuple(I(i) for i in range(32,36))!=(0,0,0,0):raise ValueError(f"7TIER_FINGERPRINT_TAIL:{issue}:{tuple(I(i) for i in range(32,36))}")
        award_tuple=tuple(awards)
        if award_tuple not in NEW_FIXED:raise ValueError(f"7TIER_FIXED_AWARD:{issue}:{award_tuple}")
        prize_rows.extend((f"{TIERS[j-1]}等奖",str(I(18+2*(j-1)))) for j in range(3,8))
        frozen={"g3":I(22),"g4":I(24),"g5":I(26),"g6":I(28),"g7":I(30)}
        epoch="7TIER_2026"

    norm=history.normalize_prize_rows(issue,day,prize_rows)
    if norm["rule_epoch"]!=epoch:raise ValueError(f"RULE_EPOCH_CONFLICT:{issue}:{norm['rule_epoch']}!={epoch}")
    norm["promo_active"]="0"
    for k,v in frozen.items():
        if int(norm[k])!=v:raise ValueError(f"FROZEN_MAPPING_MISMATCH:{issue}:{k}:{norm[k]}!={v}")
    if int(norm["g7"])<=0:raise ValueError(f"G7_NOT_POSITIVE:{issue}")

    row={"issue":issue,"date":day,**{f"f{i+1}":str(v) for i,v in enumerate(front)},**{f"b{i+1}":str(v) for i,v in enumerate(back)},"sales_rmb":str(sales),"pool_after_rmb":str(pool),**norm}
    row={k:row[k] for k in history.HISTORY_FIELDS}
    return row,{"epoch":epoch,"base_awards":{"g1":g1_award,"g2":g2_award},"add":{"g1_count":g1_add,"g1_award":g1_add_award,"g2_count":g2_add,"g2_award":g2_add_award},"add_alignment":add_alignment,"fixed_awards":tuple(awards),"frozen_counts":frozen}


def audit_latest(source):
    issues=sorted(source,key=int,reverse=True)[:TARGET]
    if len(issues)<HARD:raise ValueError(f"DATA_GATE_INSUFFICIENT_650:{len(issues)}")
    for key in KEY_ISSUES:
        if key not in source:raise ValueError(f"KEY_ISSUE_MISSING:{key}")
        if key not in issues:raise ValueError(f"KEY_ISSUE_OUTSIDE_780:{key}")

    ext_nonzero=[]
    for issue in issues:
        c=source[issue]["c"];tail=[integer(c[i],f"c[{i}]",issue) for i in range(40,45)]
        if any(tail):ext_nonzero.append({"issue":issue,"tail":tail,"raw_line":source[issue]["raw_line"]})
    print(f"EXTENSION_AUDIT rows={len(issues)} nonzero_rows={len(ext_nonzero)} promo_active=0 promo_flag_source=UNAVAILABLE_IN_17500_TXT_SET_0",flush=True)
    for sample in ext_nonzero:print("EXTENSION_NONZERO_RECORD "+json.dumps(sample,ensure_ascii=False),flush=True)

    rows=[];debug={}
    for issue in issues:
        row,d=normalize_row(source[issue]);rows.append(row);debug[issue]=d
    if tuple(debug["26015"]["fixed_awards"])!=(6666,380,200,18,7):raise ValueError(f"ISSUE_26015_FIXED_AWARD:{debug['26015']['fixed_awards']}")
    print("ISSUE_26015_FIXED_AWARD=PASS tuple="+repr(debug["26015"]["fixed_awards"]),flush=True)

    for key in KEY_ISSUES:
        print(f"RAW_KEY_LINE issue={key} columns=45 line={source[key]['raw_line']}",flush=True)
        print("MAPPED_KEY "+json.dumps({"issue":key,"canonical":next(r for r in rows if r["issue"]==key),"debug":debug[key]},ensure_ascii=False,sort_keys=True),flush=True)

    ordered=sorted(rows,key=lambda r:int(r["issue"]))
    med={g:statistics.median(int(r[g]) for r in ordered) for g in ("g3","g4","g5","g6","g7")}
    if not (med["g7"]>med["g6"]>med["g5"]>med["g4"]>med["g3"]):raise ValueError("PRIZE_MEDIAN_ORDER:"+json.dumps(med))
    scale_floor={"g3":100,"g4":1000,"g5":10000,"g6":100000,"g7":1000000}
    if any(med[g]<=floor for g,floor in scale_floor.items()):raise ValueError("PRIZE_MEDIAN_SCALE:"+json.dumps({"median":med,"floor":scale_floor}))
    g1_add_positive=sum(int(r["g1_add"])>0 for r in ordered);g2_add_positive=sum(int(r["g2_add"])>0 for r in ordered)
    if g1_add_positive<=0 or g2_add_positive<=0:raise ValueError(f"ADDITIONAL_NONZERO_AUDIT:g1={g1_add_positive}:g2={g2_add_positive}")
    if {r["rule_epoch"] for r in ordered}!={"9TIER_2019","7TIER_2026"}:raise ValueError("RULE_EPOCH_COVERAGE")

    alignment=[a for issue in issues for a in debug[issue]["add_alignment"]]
    if not alignment:raise ValueError("ADD_80PCT_ALIGNMENT_NO_NONZERO_ROWS")
    matched=sum(bool(a["match"]) for a in alignment);total=len(alignment)
    anomalies=[a for a in alignment if not a["match"]]
    rate=matched/total
    print(f"ADD_80PCT_ALIGNMENT matched={matched} total={total} rate={rate:.9f} threshold=0.95 anomalies={len(anomalies)}",flush=True)
    for anomaly in anomalies:print("ADD_80PCT_ANOMALY "+json.dumps(anomaly,ensure_ascii=False,sort_keys=True),flush=True)
    if matched*100 < total*95:raise ValueError(f"ADD_80PCT_ALIGNMENT_BELOW_95PCT:{matched}/{total}")

    report={"source":SOURCE_URL,"selected_rows":len(ordered),"start_issue":ordered[0]["issue"],"start_date":ordered[0]["date"],"end_issue":ordered[-1]["issue"],"end_date":ordered[-1]["date"],"median_counts":med,"median_scale_floor":scale_floor,"g1_add_positive_rows":g1_add_positive,"g2_add_positive_rows":g2_add_positive,"extension_nonzero_rows":len(ext_nonzero),"extension_nonzero_records":ext_nonzero,"promo_flag_source":"UNAVAILABLE_IN_17500_TXT_SET_0","add_80pct_alignment":{"matched":matched,"total":total,"rate":rate,"threshold":0.95,"anomaly_count":len(anomalies),"anomalies":anomalies},"sales_rmb_range":[100000000,1000000000],"issue_26015_fixed_awards":[6666,380,200,18,7],"rule_epoch_conflicts":0,"hard_contract":"PASS"}
    print("HARD_CONTRACT_PASS "+json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
    return ordered,report


def atomic_csv(rows,path):
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
    raw=fetch_once();source,meta=parse_source(raw);rows,report=audit_latest(source)
    report["source_file_sha256"]=hashlib.sha256(raw).hexdigest();report["source_meta"]=meta
    audit=ROOT/a.audit_out;audit.parent.mkdir(parents=True,exist_ok=True);audit.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    atomic_csv(rows,out);csv_sha=hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"EXPORT_COMPLETE rows={len(rows)} csv_sha256={csv_sha}",flush=True)

if __name__=="__main__":raise SystemExit(main())

from __future__ import annotations
import concurrent.futures as cf
import hashlib, json, re, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path("canonical_v2_fujian_audit"); RAW=ROOT/"raw"
PRE=Path("ssq_v5/CANONICAL_V2_AUDIT_60_PRECOMMIT.json")
UA="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
BASE="https://fjfc.uplottery.com.cn/kjgg.php?term={issue}&type=ssq"

def sha(b): return hashlib.sha256(b).hexdigest()
def get(url,tries=4):
    last=None
    for i in range(tries):
        try:
            r=requests.get(url,headers={"User-Agent":UA},timeout=30)
            r.raise_for_status(); return r.content
        except Exception as e:
            last=e; time.sleep(.4*(i+1))
    raise RuntimeError(f"GET failed: {last}")

def parse(issue,raw,url):
    soup=BeautifulSoup(raw,"html.parser")
    text=" ".join(soup.stripped_strings)
    if issue not in text:
        raise ValueError("ISSUE_NOT_FOUND")
    if "福建" not in text or "福利彩票" not in text:
        raise ValueError("AUTHORITY_MARKER_NOT_FOUND")
    vals={}
    for label,key in [("三等奖","tier3_count"),("四等奖","tier4_count"),("五等奖","tier5_count"),("六等奖","tier6_count")]:
        got=None
        for tr in soup.find_all("tr"):
            cells=tr.find_all(["td","th"])
            if not cells: continue
            if cells[0].get_text(" ",strip=True)==label:
                # page columns: prize, national count, per-ticket amount (possibly provincial count on desktop)
                for cell in cells[1:]:
                    s=re.sub(r"[^0-9]","",cell.get_text(" ",strip=True))
                    if s:
                        got=int(s); break
            if got is not None: break
        if got is None:
            # normalized text fallback
            m=re.search(label+r"\s+([0-9,]+)",text)
            if m: got=int(m.group(1).replace(",",""))
        if got is None: raise ValueError("MISSING_"+label)
        vals[key]=got
    return {"issue":issue,"authority_id":"FUJIAN_WELFARE_LOTTERY",
            "source_url":url,"parser_version":"fujian_mobile_ssq_html_v1",
            "raw_sha256":sha(raw),**vals}

def one(issue):
    url=BASE.format(issue=issue); raw=get(url); return parse(issue,raw,url),raw

def main():
    ROOT.mkdir(exist_ok=True); RAW.mkdir(exist_ok=True)
    pre=json.loads(PRE.read_text("utf-8")); issues=pre["sample_issues"]
    rows=[]; errors=[]
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(one,i):i for i in issues}
        for fut in cf.as_completed(futs):
            issue=futs[fut]
            try:
                rec,raw=fut.result(); (RAW/f"{issue}.html").write_bytes(raw); rows.append(rec)
            except Exception as e:
                errors.append({"issue":issue,"error":repr(e)})
    rows.sort(key=lambda x:x["issue"])
    out=ROOT/"fujian_audit_rows.json"
    out.write_text(json.dumps(rows,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    manifest={
      "schema":"SSQ_CANONICAL_V2_FUJIAN_AUDIT_FETCH_1.0",
      "precommit_sha256":pre["precommit_sha256"],"sample_count":len(issues),
      "parsed_count":len(rows),"errors":errors,
      "missing_sample_issues":sorted(set(issues)-{r["issue"] for r in rows}),
      "raw_sha_missing_count":sum(not r["raw_sha256"] for r in rows),
      "rows_sha256":sha(out.read_bytes())
    }
    (ROOT/"FUJIAN_AUDIT_FETCH_MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    if errors or len(rows)!=60: raise SystemExit(2)

if __name__=="__main__": main()

from __future__ import annotations
import concurrent.futures as cf, hashlib, json, re, time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE="https://www.scflcp.com.cn/"
ROOT=Path("canonical_v2_sichuan_fast")
RAW=ROOT/"raw"
PRE=Path("ssq_v5/CANONICAL_V2_AUDIT_60_PRECOMMIT.json")
UA="Mozilla/5.0"
TARGET_PRIMARY_HEBEI=set(json.loads(PRE.read_text("utf-8"))["sample_issues"][10:])

def get(url,tries=3):
    last=None
    for i in range(tries):
        try:
            r=requests.get(url,headers={"User-Agent":UA},timeout=(7,20))
            r.raise_for_status(); return r.content
        except Exception as e:
            last=e; time.sleep(.25*(i+1))
    raise RuntimeError(f"GET {url} failed: {last}")

def scan(n):
    url=urljoin(BASE,"czfl.jhtml" if n==1 else f"czfl_{n}.jhtml")
    raw=get(url)
    s=raw.decode("utf-8","ignore")
    soup=BeautifulSoup(s,"html.parser")
    pairs=[]
    # Find each target issue and all hrefs in its local HTML neighborhood.
    for issue in TARGET_PRIMARY_HEBEI:
        for m in re.finditer(re.escape(issue),s):
            lo=max(0,m.start()-1200); hi=min(len(s),m.end()+1200); frag=s[lo:hi]
            hrefs=re.findall(r'href=["\']([^"\']+)["\']',frag,re.I)
            for h in hrefs:
                u=urljoin(url,h)
                if ("scflcp.com.cn" in u and ("/ssq/" in u or "/assets/" in u or u.lower().endswith(".pdf"))):
                    pairs.append((issue,u,n))
    # Also structured row matching.
    for tr in soup.find_all(["tr","li","div"]):
        txt=" ".join(tr.stripped_strings)
        hits=[i for i in TARGET_PRIMARY_HEBEI if i in txt]
        if not hits: continue
        for a in tr.find_all("a",href=True):
            u=urljoin(url,a["href"])
            if "scflcp.com.cn" in u:
                for issue in hits: pairs.append((issue,u,n))
    return n,raw,pairs

def parse(issue,url,raw):
    text=BeautifulSoup(raw,"html.parser").get_text(" ",strip=True)
    if issue not in text: raise ValueError("ISSUE_NOT_FOUND")
    vals={}
    for label,key in [("三等奖","tier3_count"),("四等奖","tier4_count"),("五等奖","tier5_count"),("六等奖","tier6_count")]:
        m=re.search(label+r"\s+([0-9,]+)",text)
        if not m: raise ValueError("MISSING_"+label)
        vals[key]=int(m.group(1).replace(",",""))
    return {"issue":issue,"authority_id":"SICHUAN_WELFARE_LOTTERY","source_url":url,
            "parser_version":"sichuan_ssq_detail_text_v1","raw_sha256":hashlib.sha256(raw).hexdigest(),**vals}

def main():
    ROOT.mkdir(exist_ok=True); RAW.mkdir(exist_ok=True)
    mapping={}; scan_errors=[]
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs={ex.submit(scan,n):n for n in range(1,61)}
        for fut in cf.as_completed(futs):
            n=futs[fut]
            try:
                _,raw,pairs=fut.result()
                for issue,u,pn in pairs:
                    # prefer /ssq/ HTML over assets/pdf only if first
                    cur=mapping.get(issue)
                    score=(2 if "/ssq/" in u else 1, -pn)
                    if cur is None or score>cur[1]: mapping[issue]=(u,score)
            except Exception as e: scan_errors.append({"page":n,"error":repr(e)})
    # unique URLs
    todo=[(i,v[0]) for i,v in mapping.items()]
    rows=[]; errors=[]
    def work(p):
        i,u=p; raw=get(u); return parse(i,u,raw),raw,u
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(work,p):p for p in todo}
        for fut in cf.as_completed(futs):
            i,u=futs[fut]
            try:
                rec,raw,url=fut.result()
                ext=".pdf" if url.lower().split("?")[0].endswith(".pdf") else ".html"
                (RAW/f"{i}{ext}").write_bytes(raw); rows.append(rec)
            except Exception as e: errors.append({"issue":i,"url":u,"error":repr(e)})
    rows.sort(key=lambda x:x["issue"])
    out=ROOT/"sichuan_rows.json"; out.write_text(json.dumps(rows,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    man={"schema":"SSQ_SICHUAN_FAST_AUDIT_1.0","target_count":len(TARGET_PRIMARY_HEBEI),
         "mapped_count":len(mapping),"parsed_count":len(rows),"scan_errors":scan_errors,
         "detail_errors":errors,"missing":sorted(TARGET_PRIMARY_HEBEI-{r["issue"] for r in rows}),
         "mapping":{i:v[0] for i,v in sorted(mapping.items())},
         "rows_sha256":hashlib.sha256(out.read_bytes()).hexdigest()}
    (ROOT/"MANIFEST.json").write_text(json.dumps(man,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(man,ensure_ascii=False,indent=2))
    if len(rows)!=len(TARGET_PRIMARY_HEBEI): raise SystemExit(2)

if __name__=="__main__": main()

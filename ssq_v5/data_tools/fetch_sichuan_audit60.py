from __future__ import annotations
import concurrent.futures as cf
import hashlib, json, re, time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE="https://www.scflcp.com.cn/"
ROOT=Path("canonical_v2_sichuan_audit")
PAGES=ROOT/"pages"; DETAILS=ROOT/"details"
PRECOMMIT=Path("ssq_v5/CANONICAL_V2_AUDIT_60_PRECOMMIT.json")
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/158.0 Safari/537.36"

def sha(b): return hashlib.sha256(b).hexdigest()
def get(url, tries=4):
    last=None
    for i in range(tries):
        try:
            r=requests.get(url,headers={"User-Agent":UA},timeout=35)
            r.raise_for_status(); return r.content
        except Exception as e:
            last=e; time.sleep(.4*(i+1))
    raise RuntimeError(f"GET failed {url}: {last}")

def page_url(n):
    return urljoin(BASE, "czfl.jhtml" if n==1 else f"czfl_{n}.jhtml")

def scan_page(n):
    url=page_url(n); raw=get(url)
    soup=BeautifulSoup(raw,"html.parser")
    found=[]
    for tr in soup.find_all("tr"):
        txt=" ".join(tr.stripped_strings)
        issues=re.findall(r"(?<!\d)(20\d{5})(?!\d)",txt)
        if not issues: continue
        a=tr.find("a",href=True)
        href=urljoin(url,a["href"]) if a else None
        for issue in issues:
            found.append((issue,href))
    # fallback: anchor-neighborhood parse
    s=raw.decode("utf-8","ignore")
    for m in re.finditer(r'href=["\']([^"\']+)["\']',s,re.I):
        lo=max(0,m.start()-500); hi=min(len(s),m.end()+500)
        for issue in re.findall(r"(?<!\d)(20\d{5})(?!\d)",s[lo:hi]):
            found.append((issue,urljoin(url,m.group(1))))
    return n,url,raw,found

def parse_detail(issue,url,raw):
    soup=BeautifulSoup(raw,"html.parser")
    text=" ".join(soup.stripped_strings)
    # Require issue to be present in detail raw bytes.
    if issue not in text:
        raise ValueError("ISSUE_NOT_IN_DETAIL")
    out={}
    for label,key in [("三等奖","tier3_count"),("四等奖","tier4_count"),("五等奖","tier5_count"),("六等奖","tier6_count")]:
        got=None
        for tr in soup.find_all("tr"):
            cells=tr.find_all(["td","th"])
            if not cells: continue
            first=cells[0].get_text(" ",strip=True)
            if first==label and len(cells)>=2:
                # Prefer nationwide count: first numeric cell after label.
                for cell in cells[1:]:
                    ds=re.sub(r"[^0-9]","",cell.get_text(" ",strip=True))
                    if ds:
                        got=int(ds); break
            if got is not None: break
        if got is None:
            # fallback plain text pattern
            m=re.search(label+r"\s+([0-9,]+)",text)
            if m: got=int(m.group(1).replace(",",""))
        if got is None: raise ValueError(f"MISSING_{label}")
        out[key]=got
    return {
      "issue":issue,"source_url":url,"authority_id":"SICHUAN_WELFARE_LOTTERY",
      "parser_version":"sichuan_full_prize_html_or_pdf_v1",
      "raw_sha256":sha(raw),**out
    }

def main():
    ROOT.mkdir(exist_ok=True); PAGES.mkdir(exist_ok=True); DETAILS.mkdir(exist_ok=True)
    pre=json.loads(PRECOMMIT.read_text("utf-8"))
    target=set(pre["sample_issues"])
    page_errors=[]; mapping={}

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs={ex.submit(scan_page,n):n for n in range(1,665)}
        for fut in cf.as_completed(futs):
            n=futs[fut]
            try:
                pn,url,raw,found=fut.result()
                (PAGES/f"{pn:03d}.html").write_bytes(raw)
                for issue,href in found:
                    if issue in target and href:
                        # Favor SSQ/PDF-looking detail URLs; never overwrite a plausible earlier hit with blank.
                        if issue not in mapping or ("/ssq/" in href or href.lower().endswith(".pdf")):
                            mapping[issue]=href
            except Exception as e:
                page_errors.append({"page":n,"error":repr(e)})

    detail_rows=[]; detail_errors=[]
    def work(pair):
        issue,url=pair
        raw=get(url)
        rec=parse_detail(issue,url,raw)
        ext=".pdf" if url.lower().split("?")[0].endswith(".pdf") else ".html"
        return rec,raw,ext

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(work,p):p for p in mapping.items()}
        for fut in cf.as_completed(futs):
            issue,url=futs[fut]
            try:
                rec,raw,ext=fut.result()
                (DETAILS/f"{issue}{ext}").write_bytes(raw)
                detail_rows.append(rec)
            except Exception as e:
                detail_errors.append({"issue":issue,"url":url,"error":repr(e)})

    detail_rows.sort(key=lambda x:x["issue"])
    (ROOT/"sichuan_audit_rows.json").write_text(json.dumps(detail_rows,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    manifest={
      "schema":"SSQ_CANONICAL_V2_SICHUAN_AUDIT_FETCH_1.0",
      "precommit_sha256":pre["precommit_sha256"],
      "sample_count":len(target),"pages_requested":664,
      "pages_ok":664-len(page_errors),"page_errors":page_errors,
      "mapped_issue_count":len(mapping),"parsed_detail_count":len(detail_rows),
      "unmapped_issues":sorted(target-set(mapping)),
      "detail_errors":detail_errors,
      "parsed_issues":[x["issue"] for x in detail_rows],
      "rows_sha256":sha((ROOT/"sichuan_audit_rows.json").read_bytes())
    }
    (ROOT/"SICHUAN_AUDIT_FETCH_MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    if detail_errors:
        raise SystemExit(2)

if __name__=="__main__": main()

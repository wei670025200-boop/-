from __future__ import annotations
import concurrent.futures as cf, hashlib, html as htmlmod, json, re, time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT=Path("canonical_v2_secondary50"); RAW=ROOT/"raw"; MAPDIR=ROOT/"mapping_pages"
PRE=Path("ssq_v5/CANONICAL_V2_AUDIT_60_PRECOMMIT.json")
TARGET=set(json.loads(PRE.read_text("utf-8"))["sample_issues"][10:])
JS_BASE="https://www.jslottery.com"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/158.0 Safari/537.36"
CWL_REF="https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/"

def sha(b): return hashlib.sha256(b).hexdigest()
def get(url,headers=None,tries=5):
    last=None
    h={"User-Agent":UA}
    if headers: h.update(headers)
    for i in range(tries):
        try:
            r=requests.get(url,headers=h,timeout=(10,45),allow_redirects=True)
            r.raise_for_status(); return r.content, r.url
        except Exception as e:
            last=e; time.sleep(.6*(i+1))
    raise RuntimeError(f"GET failed {url}: {last}")

def tier_parse(issue,raw,authority,url,parser_version):
    soup=BeautifulSoup(raw,"html.parser")
    text=" ".join(soup.stripped_strings)
    if issue not in text:
        raise ValueError("ISSUE_NOT_FOUND")
    vals={}
    for label,key in [("三等奖","tier3_count"),("四等奖","tier4_count"),("五等奖","tier5_count"),("六等奖","tier6_count")]:
        got=None
        for tr in soup.find_all("tr"):
            cells=tr.find_all(["td","th"])
            if not cells: continue
            first=re.sub(r"\s+","",cells[0].get_text(" ",strip=True))
            if first==label:
                for cell in cells[1:]:
                    s=re.sub(r"[^0-9]","",cell.get_text(" ",strip=True))
                    if s:
                        got=int(s); break
            if got is not None: break
        if got is None:
            # Text fallback tolerates spaces and punctuation.
            m=re.search(label+r"[^0-9]{0,30}([0-9][0-9,]*)",text)
            if m: got=int(m.group(1).replace(",",""))
        if got is None:
            raise ValueError("MISSING_"+label)
        vals[key]=got
    return {"issue":issue,"authority_id":authority,"source_url":url,
            "parser_version":parser_version,"raw_sha256":sha(raw),**vals}

def fetch_js_page(n):
    url=f"{JS_BASE}/winning_history_a?locale=zh-CN&lottery_type_id=10&page={n}&periods="
    raw,_=get(url)
    return n,raw

def build_mapping():
    mapping={}
    MAPDIR.mkdir(parents=True,exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(fetch_js_page,n):n for n in range(1,27)}
        for fut in cf.as_completed(futs):
            n=futs[fut]; pn,raw=fut.result(); (MAPDIR/f"page_{pn}.html").write_bytes(raw)
            soup=BeautifulSoup(raw.decode("utf-8","ignore"),"html.parser")
            for a in soup.find_all("a"):
                txt=" ".join(a.stripped_strings)
                hit=[i for i in TARGET if i in txt]
                if not hit: continue
                href=a.get("href") or ""
                for issue in hit:
                    mapping[issue]={"page":pn,"href":href,"mapping_page_sha256":sha(raw)}
    return mapping

def one(issue,entry):
    href=entry["href"]
    # Legacy Jiangsu pages sometimes place the complete notice HTML literally in href.
    if "<" in href and ("三等奖" in href or "双色球" in href):
        raw=htmlmod.unescape(href).encode("utf-8")
        rec=tier_parse(issue,raw,"JIANGSU_WELFARE_LOTTERY",
                       f"{JS_BASE}/winning_history_a?page={entry['page']}#embedded-{issue}",
                       "jiangsu_embedded_notice_html_v1")
        rec["mapping_page_sha256"]=entry["mapping_page_sha256"]
        rec["transport"]="EMBEDDED_IN_JIANGSU_OFFICIAL_ARCHIVE"
        return rec,raw,".html"
    if "cwl.gov.cn" not in href:
        raise ValueError(f"UNSUPPORTED_MAPPING_HREF:{href[:120]}")
    url=href.replace("http://","https://",1)
    raw,final_url=get(url,headers={"Referer":CWL_REF,"Accept":"text/html,application/xhtml+xml"})
    rec=tier_parse(issue,raw,"CHINA_WELFARE_LOTTERY_CENTER",final_url,"cwl_draw_notice_html_v1")
    rec["mapping_page_sha256"]=entry["mapping_page_sha256"]
    rec["mapping_url"]=f"{JS_BASE}/winning_history_a?locale=zh-CN&lottery_type_id=10&page={entry['page']}&periods="
    rec["transport"]="DIRECT_CWL_OFFICIAL_ARTICLE"
    return rec,raw,".html"

def main():
    ROOT.mkdir(exist_ok=True); RAW.mkdir(exist_ok=True)
    mapping=build_mapping()
    rows=[]; errors=[]
    missing_map=sorted(TARGET-set(mapping))
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(one,i,mapping[i]):i for i in sorted(TARGET & set(mapping))}
        for fut in cf.as_completed(futs):
            issue=futs[fut]
            try:
                rec,raw,ext=fut.result(); (RAW/f"{issue}{ext}").write_bytes(raw); rows.append(rec)
            except Exception as e:
                errors.append({"issue":issue,"href":mapping[issue]["href"][:300],"error":repr(e)})
    rows.sort(key=lambda x:x["issue"])
    out=ROOT/"secondary50_rows.json"; out.write_text(json.dumps(rows,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    man={"schema":"SSQ_CANONICAL_V2_SECONDARY50_FETCH_1.0",
         "target_count":len(TARGET),"mapping_count":len(mapping),"missing_mapping":missing_map,
         "parsed_count":len(rows),"errors":errors,
         "authority_counts":{a:sum(r["authority_id"]==a for r in rows) for a in sorted({r["authority_id"] for r in rows})},
         "rows_sha256":sha(out.read_bytes())}
    (ROOT/"MANIFEST.json").write_text(json.dumps(man,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(man,ensure_ascii=False,indent=2))
    if missing_map or errors or len(rows)!=50: raise SystemExit(2)

if __name__=="__main__": main()

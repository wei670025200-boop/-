#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, html, json, re, time, urllib.request
from pathlib import Path

API="https://www.yzfcw.com/api/cms/content/lottery?sid=846994348564549&cid=849209070977093&ps=700"
BASE="https://www.yzfcw.com"
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/158 Safari/537.36"}

def fetch(url:str, timeout=45)->bytes:
    last=None
    for attempt in range(4):
        try:
            req=urllib.request.Request(url,headers=UA)
            with urllib.request.urlopen(req,timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last=e; time.sleep(1+attempt)
    raise last

def plain(raw:bytes)->str:
    s=raw.decode("utf-8","ignore")
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",html.unescape(s))).strip()

def num(s:str)->int:
    return int(s.replace(",","").strip())

def parse_detail(issue:str,url:str,raw:bytes)->dict:
    t=plain(raw)
    if issue not in t:
        raise ValueError(f"{issue}: issue not in detail")
    date_m=re.search(r"开奖日期[:：]\s*(20\d{2}-\d{2}-\d{2})",t)
    nums_m=re.search(r"开奖号码[:：]\s*((?:\d{1,2}\s+){6}\d{1,2})",t)
    sales_m=re.search(r"本期全国销售额\s*([\d,]+)\s*元",t)
    pool_m=re.search(r"奖池金额\s*([\d,]+)\s*元",t)
    if not all((date_m,nums_m,sales_m,pool_m)):
        raise ValueError(f"{issue}: core parse missing")
    nums=[int(x) for x in nums_m.group(1).split()]
    reds=sorted(nums[:6]); blue=nums[6]
    patterns=[
      ("tier1_count",r"一等奖\s+6\s*\+\s*1\s+([\d,]+)\s*注"),
      ("tier2_count",r"二等奖\s+6\s*\+\s*0\s+([\d,]+)\s*注"),
      ("tier3_count",r"三等奖\s+5\s*\+\s*1\s+([\d,]+)\s*注"),
      ("tier4_count",r"四等奖\s+5\s*\+\s*0\s+4\s*\+\s*1\s+([\d,]+)\s*注"),
      ("tier5_count",r"五等奖\s+4\s*\+\s*0\s+3\s*\+\s*1\s+([\d,]+)\s*注"),
      ("tier6_count",r"六等奖\s+2\s*\+\s*1\s+1\s*\+\s*1\s+0\s*\+\s*1\s+([\d,]+)\s*注"),
    ]
    tiers={}
    for k,p in patterns:
        m=re.search(p,t)
        if not m: raise ValueError(f"{issue}: missing {k}")
        tiers[k]=num(m.group(1))
    promo6=re.search(r"六等奖翻番奖[^\d]{0,80}([\d,]+)\s*注",t)
    promo2=re.search(r"二等奖特别奖[^\d]{0,80}([\d,]+)\s*注",t)
    return {
      "issue":issue,"draw_date_bjt":date_m.group(1),"reds":reds,"blue":blue,
      "sales_rmb":num(sales_m.group(1)),"pool_after_rmb":num(pool_m.group(1)),
      **tiers,
      "promo_sixth_double":bool("六等奖翻番" in t),
      "promo_sixth_double_count":num(promo6.group(1)) if promo6 else None,
      "promo_second_special":bool("二等奖特别奖" in t),
      "promo_second_special_count":num(promo2.group(1)) if promo2 else None,
      "source_url":url,"authority_id":"HEBEI_WELFARE_LOTTERY",
      "parser_version":"hb_ssq_detail_html_v1",
      "raw_html_sha256":hashlib.sha256(raw).hexdigest(),
      "raw_path":f"raw/{issue}.html",
    }

def main():
    out=Path("out"); rawdir=out/"raw"; rawdir.mkdir(parents=True,exist_ok=True)
    api_raw=fetch(API); (out/"hebei_api.json").write_bytes(api_raw)
    doc=json.loads(api_raw.decode("utf-8"))
    rows=(doc.get("data") or {}).get("rows") or []
    wanted=[]
    for r in rows:
        issue=str(r.get("issueNo") or "")
        if "2023048" <= issue <= "2026135":
            link=str(r.get("link") or "")
            wanted.append((issue,BASE+link))
    wanted=sorted(dict(wanted).items())
    print("WANTED",len(wanted),wanted[0],wanted[-1])

    def one(pair):
        issue,url=pair
        raw=fetch(url)
        (rawdir/f"{issue}.html").write_bytes(raw)
        return parse_detail(issue,url,raw)

    parsed=[]; errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(one,p):p for p in wanted}
        for i,f in enumerate(concurrent.futures.as_completed(futs),1):
            try: parsed.append(f.result())
            except Exception as e: errors.append({"issue":futs[f][0],"error":repr(e)})
            if i%50==0: print("DONE",i,"ERR",len(errors),flush=True)
    parsed.sort(key=lambda x:x["issue"])
    (out/"HEBEI_PARSED_ROWS.json").write_text(json.dumps(parsed,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    manifest={
      "schema":"SSQ_V5_HEBEI_FULL_TIER_RAW_1.0",
      "api_url":API,"api_sha256":hashlib.sha256(api_raw).hexdigest(),
      "requested":len(wanted),"parsed":len(parsed),"errors":errors,
      "first_issue":parsed[0]["issue"] if parsed else None,
      "last_issue":parsed[-1]["issue"] if parsed else None,
    }
    (out/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False))
    if errors or len(parsed)!=len(wanted):
        raise SystemExit(2)

if __name__=="__main__":
    main()

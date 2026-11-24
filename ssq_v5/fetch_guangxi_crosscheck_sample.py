#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures,hashlib,html,json,re,time,urllib.request
from pathlib import Path

UA={"User-Agent":"Mozilla/5.0"}
PRE="ssq_v5/CANONICAL_V2_CROSSCHECK_PRECOMMIT.json"

def fetch(issue):
 u=f"https://www.gxcaipiao.com.cn/notice/detail/ssq/{issue}.html"
 last=None
 for a in range(4):
  try:
   req=urllib.request.Request(u,headers=UA)
   with urllib.request.urlopen(req,timeout=40) as r:return issue,u,r.read()
  except Exception as e:last=e;time.sleep(.5+a)
 raise last

def plain(b):
 s=b.decode("utf-8","ignore")
 return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",html.unescape(s))).strip()

def iv(x): return int(float(x.replace(",","")))

def parse(issue,u,b):
 t=plain(b)
 if f"第{issue}期" not in t: raise ValueError("ISSUE_MISMATCH")
 dm=re.search(r"开奖日期[:：]\s*(20\d{2}-\d{2}-\d{2})",t)
 sm=re.search(r"全国销售金额（元）[:：]\s*([\d,.]+)",t)
 pm=re.search(r"奖池累计金额（元）[:：]\s*([\d,.]+)",t)
 nm=re.search(r"奖池累计金额（元）[:：]\s*[\d,.]+\s*((?:\d{1,2}\s+){6}\d{1,2})\s+奖级",t)
 if not all((dm,sm,pm,nm)): raise ValueError("CORE_PARSE_MISSING")
 nums=[int(x) for x in nm.group(1).split()]
 tiers={}
 for i,lab in enumerate(["一等奖","二等奖","三等奖","四等奖","五等奖","六等奖"],1):
  m=re.search(lab+r"\s+([\d,]+)\s+[\d,.]+",t)
  if not m: raise ValueError(f"TIER{i}_MISSING")
  tiers[f"tier{i}_count"]=iv(m.group(1))
 p2=re.search(r"二等奖特别奖\s+([\d,]+)\s+[\d,.]+",t)
 p6=re.search(r"六等奖翻番奖\s+([\d,]+)\s+[\d,.]+",t)
 return {
  "issue":issue,"draw_date_bjt":dm.group(1),"reds":sorted(nums[:6]),"blue":nums[6],
  "sales_rmb":iv(sm.group(1)),"pool_after_rmb":iv(pm.group(1)),**tiers,
  "promo_second_special":p2 is not None,"promo_second_special_count":iv(p2.group(1)) if p2 else None,
  "promo_sixth_double":p6 is not None,"promo_sixth_double_count":iv(p6.group(1)) if p6 else None,
  "source_url":u,"authority_id":"GUANGXI_WELFARE_LOTTERY",
  "parser_version":"gx_ssq_detail_html_v1","raw_html_sha256":hashlib.sha256(b).hexdigest(),
  "raw_path":f"raw/{issue}.html"
 }

def main():
 pre=json.load(open(PRE,encoding="utf-8")); issues=pre["sample_issues"]
 root=Path("out_gx_audit"); rawdir=root/"raw"; rawdir.mkdir(parents=True,exist_ok=True)
 rows=[];errs=[]
 def one(issue):
  issue,u,b=fetch(issue);(rawdir/f"{issue}.html").write_bytes(b);return parse(issue,u,b)
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
  futs={ex.submit(one,i):i for i in issues}
  for n,f in enumerate(concurrent.futures.as_completed(futs),1):
   try:rows.append(f.result())
   except Exception as e:errs.append({"issue":futs[f],"error":repr(e)})
   if n%10==0:print("DONE",n,"ERR",len(errs),flush=True)
 rows.sort(key=lambda r:r["issue"])
 (root/"GX_PARSED_ROWS.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2)+"\n")
 manifest={"schema":"SSQ_V5_GX_CROSSCHECK_RAW_1.0","precommit_sha256":pre["precommit_sha256"],
           "requested":len(issues),"parsed":len(rows),"errors":errs,
           "first_issue":rows[0]["issue"] if rows else None,"last_issue":rows[-1]["issue"] if rows else None}
 (root/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
 print(json.dumps(manifest,ensure_ascii=False))
 if errs or len(rows)!=len(issues):raise SystemExit(2)
if __name__=="__main__":main()

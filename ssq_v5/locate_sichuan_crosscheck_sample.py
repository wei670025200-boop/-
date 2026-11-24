#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures,json,re,time,urllib.parse,urllib.request
from pathlib import Path
UA={"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest","Referer":"https://www.scflcp.com.cn/czfl.jhtml"}
BASE="https://www.scflcp.com.cn/content/page"
pre=json.load(open("ssq_v5/CANONICAL_V2_CROSSCHECK_PRECOMMIT.json",encoding="utf-8"))
wanted=set(pre["sample_issues"])

def get_page(page):
 q=urllib.parse.urlencode({"page":page,"size":10,"channelIds":2565,"orderBy":1})
 u=BASE+"?"+q
 for a in range(3):
  try:
   req=urllib.request.Request(u,headers=UA)
   with urllib.request.urlopen(req,timeout=30) as r:return page,r.read()
  except Exception:
   if a==2:return page,None
   time.sleep(.5+a)

def find_matches(raw):
 if not raw:return []
 d=json.loads(raw.decode("utf-8")); content=(d.get("data") or {}).get("content") or []
 out=[]
 for obj in content:
  blob=json.dumps(obj,ensure_ascii=False)
  for issue in re.findall(r"20\d{5}",blob):
   if issue in wanted and ("双色球" in blob or "SSQ" in blob.upper()):
    out.append((issue,obj));break
 return out

def main():
 found={}
 # 1..420 comfortably reaches 2021; if not, extend to all 664 in second pass.
 for start,end in [(1,420),(421,664)]:
  with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
   for page,raw in ex.map(get_page,range(start,end+1)):
    if not raw:continue
    for issue,obj in find_matches(raw):
     found.setdefault(issue,{"page":page,"obj":obj})
  print("SCAN",start,end,"FOUND",len(found),"MISSING",len(wanted-set(found)),flush=True)
  if wanted<=set(found):break
 missing=sorted(wanted-set(found))
 print("FINAL_FOUND",len(found),"MISSING",missing)
 for issue in sorted(found)[:5]:
  obj=found[issue]["obj"]
  print("SAMPLE_OBJECT",issue,"PAGE",found[issue]["page"],"KEYS",sorted(obj.keys()))
  print(json.dumps(obj,ensure_ascii=False)[:6000])
 Path("out_sc_probe").mkdir(exist_ok=True)
 Path("out_sc_probe/matches.json").write_text(json.dumps({k:v for k,v in sorted(found.items())},ensure_ascii=False,indent=2)+"\n")
 Path("out_sc_probe/missing.json").write_text(json.dumps(missing,ensure_ascii=False,indent=2)+"\n")
 if missing:raise SystemExit(2)
if __name__=="__main__":main()

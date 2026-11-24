#!/usr/bin/env python3
from __future__ import annotations
import hashlib,re,urllib.request
UA={"User-Agent":"Mozilla/5.0"}
for issue in ("2021144","2025105","2026130"):
 u=f"https://www.gxcaipiao.com.cn/notice/detail/ssq/{issue}.html"
 try:
  req=urllib.request.Request(u,headers=UA)
  with urllib.request.urlopen(req,timeout=45) as r:
   b=r.read(); status=r.status
  s=b.decode("utf-8","ignore")
  print("GX",issue,"HTTP",status,"BYTES",len(b),"SHA",hashlib.sha256(b).hexdigest())
  for x in ("一等奖","二等奖","三等奖","四等奖","五等奖","六等奖","销售","奖池"):
   print(" ",x,s.count(x))
  plain=re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",s))
  i=plain.find("三等奖"); print(" SNIP",plain[max(0,i-400):i+1800] if i>=0 else plain[:2200])
 except Exception as e: print("GX_FAIL",issue,repr(e))

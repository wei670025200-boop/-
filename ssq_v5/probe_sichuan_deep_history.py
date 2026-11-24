#!/usr/bin/env python3
from __future__ import annotations
import hashlib,re,urllib.request
UA={"User-Agent":"Mozilla/5.0"}
def get(url):
 req=urllib.request.Request(url,headers=UA)
 with urllib.request.urlopen(req,timeout=45) as r:return r.read()
for page in (30,40,50,55,60,65,70):
 url=f"https://www.scflcp.com.cn/ssqinfo.jhtml?page={page}"
 try:
  b=get(url); s=b.decode("utf-8","ignore")
  issues=[]
  for x in re.findall(r"20\d{5}",s):
   if x[:4] in {"2021","2022","2023","2024","2025","2026"} and x not in issues:issues.append(x)
  links=re.findall(r'href=["\']([^"\']*(?:ssq|assets)[^"\']*)["\']',s,re.I)
  print("SC",page,"bytes",len(b),"sha",hashlib.sha256(b).hexdigest(),
        "issues",len(issues),issues[:3],issues[-3:],"links",len(links),links[:3])
 except Exception as e:
  print("SC_FAIL",page,repr(e))

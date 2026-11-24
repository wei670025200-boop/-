#!/usr/bin/env python3
from __future__ import annotations
import hashlib,re,urllib.parse,urllib.request
UA={"User-Agent":"Mozilla/5.0"}
def get(url):
 req=urllib.request.Request(url,headers=UA)
 with urllib.request.urlopen(req,timeout=30) as r:return r.read()
base="https://www.scflcp.com.cn/czfl.jhtml"
raw=get(base); s=raw.decode("utf-8","ignore")
print("BASE",len(raw),hashlib.sha256(raw).hexdigest())
for m in re.finditer(r'href=["\']([^"\']+)["\']',s,re.I):
 href=m.group(1)
 if "czfl" in href or "page" in href.lower():
  print("HREF",href)
for m in re.finditer(r'(?:page|Page|pageno|pageNo|pageNum|currPage|currentPage)[^\n<]{0,120}',s):
 print("TOKEN",m.group(0)[:200])
variants=[
 {"page":"2"},{"pageNo":"2"},{"pageNum":"2"},{"p":"2"},{"pi":"2"},
 {"pageIndex":"2"},{"currPage":"2"},{"currentPage":"2"},
 {"page":"60"},{"pageNo":"60"},{"pageNum":"60"},{"p":"60"},{"pi":"60"},
]
for q in variants:
 u=base+"?"+urllib.parse.urlencode(q)
 try:
  b=get(u); t=b.decode("utf-8","ignore")
  issues=[]
  for x in re.findall(r"20\d{5}",t):
   if x not in issues:issues.append(x)
  print("VAR",q,"sha",hashlib.sha256(b).hexdigest(),"issues",issues[:3],issues[-3:])
 except Exception as e:print("FAIL",q,repr(e))

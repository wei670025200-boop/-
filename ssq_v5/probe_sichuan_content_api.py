#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,urllib.parse,urllib.request
UA={"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest","Referer":"https://www.scflcp.com.cn/czfl.jhtml"}
BASE="https://www.scflcp.com.cn/content/page"
def get(q):
 u=BASE+"?"+urllib.parse.urlencode(q)
 req=urllib.request.Request(u,headers=UA)
 with urllib.request.urlopen(req,timeout=40) as r:return u,r.read()
variants=[
 {"size":10,"channelIds":2565,"orderBy":1},
 {"size":100,"channelIds":2565,"orderBy":1},
 {"size":1000,"channelIds":2565,"orderBy":1},
 {"page":2,"size":10,"channelIds":2565,"orderBy":1},
 {"pageNo":2,"size":10,"channelIds":2565,"orderBy":1},
 {"page":60,"size":10,"channelIds":2565,"orderBy":1},
 {"pageNo":60,"size":10,"channelIds":2565,"orderBy":1},
]
for q in variants:
 try:
  u,b=get(q); d=json.loads(b.decode("utf-8"))
  print("OK",q,"bytes",len(b),"sha",hashlib.sha256(b).hexdigest(),"keys",list(d)[:20])
  # inspect likely page containers
  for k,v in d.items():
   if isinstance(v,list):
    print(" LIST",k,len(v),"first",json.dumps(v[0],ensure_ascii=False)[:700] if v else None)
   elif isinstance(v,(int,str,float,bool)) or v is None:
    print(" SCALAR",k,v)
   elif isinstance(v,dict):
    print(" DICT",k,"keys",list(v)[:20])
    for kk,vv in v.items():
     if isinstance(vv,list):
      print("  SUBLIST",kk,len(vv),"first",json.dumps(vv[0],ensure_ascii=False)[:700] if vv else None)
 except Exception as e: print("FAIL",q,repr(e))

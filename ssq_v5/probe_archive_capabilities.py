#!/usr/bin/env python3
from __future__ import annotations
import hashlib, html, json, re, urllib.parse, urllib.request

UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/158 Safari/537.36"}

def get(url:str)->bytes:
    req=urllib.request.Request(url,headers=UA)
    with urllib.request.urlopen(req,timeout=60) as r:
        return r.read()

def issues_from_html(raw:bytes):
    s=raw.decode("utf-8","ignore")
    vals=[]
    for x in re.findall(r"20\d{5}",s):
        if x[:4] in {"2021","2022","2023","2024","2025","2026"} and x not in vals:
            vals.append(x)
    return vals,s

def probe_js():
    for page in (1,2,20,21,22,39,45):
        url=f"https://www.jslottery.com/winning_history_a?locale=zh-CN&lottery_type_id=10&page={page}&periods="
        raw=get(url); issues,s=issues_from_html(raw)
        plain=re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",html.unescape(s)))
        print("JS",page,"bytes",len(raw),"sha",hashlib.sha256(raw).hexdigest(),
              "issues",len(issues),issues[:3],issues[-3:],
              "tier3",plain.count("三等奖"),"tier6",plain.count("六等奖"),"win",plain.count("中奖注数"))

def probe_hb():
    base="https://www.yzfcw.com/api/cms/content/lottery"
    common={"sid":"846994348564549","cid":"849209070977093"}
    variants=[
      {"ps":"500"},
      {"ps":"700"},
      {"ps":"1000"},
      {"ps":"500","pn":"2"},
      {"ps":"500","page":"2"},
      {"ps":"500","pageNo":"2"},
      {"ps":"500","pi":"2"},
    ]
    for q in variants:
        u=base+"?"+urllib.parse.urlencode({**common,**q})
        try:
            raw=get(u); doc=json.loads(raw.decode("utf-8"))
            data=doc.get("data") or {}; rows=data.get("rows") or []
            issues=[str(x.get("issueNo")) for x in rows if x.get("issueNo")]
            print("HB",q,"bytes",len(raw),"sha",hashlib.sha256(raw).hexdigest(),
                  "rows",len(rows),"total",data.get("total"),
                  "first",issues[0] if issues else None,"last",issues[-1] if issues else None)
        except Exception as e:
            print("HB_FAIL",q,repr(e))

if __name__=="__main__":
    probe_js()
    probe_hb()

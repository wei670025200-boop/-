#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, re, urllib.request

UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/158 Safari/537.36"}

def get(url:str)->bytes:
    req=urllib.request.Request(url,headers=UA)
    with urllib.request.urlopen(req,timeout=60) as r:
        return r.read()

def main():
    cwl="https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&systemType=PC&issueStart=2026001&issueEnd=2026999&pageNo=1&pageSize=300"
    body=get(cwl)
    doc=json.loads(body.decode("utf-8"))
    rows=doc.get("result") or []
    print("CWL_STATUS",doc.get("state"),"ROWS",len(rows),"SHA256",hashlib.sha256(body).hexdigest())
    if rows:
        r=rows[0]
        print("CWL_FIRST_KEYS",sorted(r.keys()))
        print("CWL_FIRST_ISSUE",r.get("code"))
        pg=r.get("prizegrades") or r.get("prizeGrades") or []
        print("CWL_PRIZEGRADES_LEN",len(pg))
        if pg:
            print("CWL_PRIZEGRADE_KEYS",sorted(pg[0].keys()))
            print("CWL_PRIZEGRADES_SAMPLE",json.dumps(pg[:10],ensure_ascii=False)[:4000])
        print("CWL_FIRST_ROW_SAMPLE",json.dumps({k:r.get(k) for k in ["code","date","red","blue","saleamount","poolmoney"]},ensure_ascii=False))

    for page in (1,2,21,22,39):
        url=f"https://www.jslottery.com/winning_history_a?locale=zh-CN&lottery_type_id=10&page={page}&periods="
        b=get(url); s=b.decode("utf-8","ignore")
        issues=[]
        for x in re.findall(r"20\d{5}",s):
            if x not in issues: issues.append(x)
        print("JS_PAGE",page,"BYTES",len(b),"SHA256",hashlib.sha256(b).hexdigest(),
              "ISSUES",len(issues),issues[:2],issues[-2:],
              "TIER3",s.count("三等奖"),"TIER6",s.count("六等奖"),"WIN_COUNT",s.count("中奖注数"))

if __name__=="__main__":
    main()

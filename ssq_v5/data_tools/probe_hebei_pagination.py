import json, requests
BASE="https://www.yzfcw.com/api/cms/content/lottery"
base={"sid":"846994348564549","cid":"849209070977093"}
tests=[
 {"ps":500},
 {"ps":500,"pn":2},
 {"ps":500,"pageNo":2},
 {"ps":500,"page":2},
 {"ps":500,"pi":2},
 {"ps":500,"p":2},
 {"ps":500,"offset":500},
 {"ps":1000},
]
h={"User-Agent":"Mozilla/5.0"}
for q in tests:
    p={**base,**q}
    try:
        r=requests.get(BASE,params=p,headers=h,timeout=30); print("QUERY",q,"STATUS",r.status_code,"URL",r.url)
        d=r.json(); rows=(d.get("data") or {}).get("rows") or []
        print("ROWS",len(rows),"TOTAL",(d.get("data") or {}).get("total"),"FIRST",rows[0].get("issueNo") if rows else None,"LAST",rows[-1].get("issueNo") if rows else None)
    except Exception as e: print("ERROR",q,repr(e))

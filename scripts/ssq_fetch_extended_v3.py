#!/usr/bin/env python3
import re,json,hashlib,datetime,pathlib,subprocess,concurrent.futures
from urllib.parse import urlencode
ROOT=pathlib.Path('evidence');ROOT.mkdir(exist_ok=True)
BASE_SH='https://www.swlc.net.cn/lottery/ssq.html'
BASE_SC='https://www.scflcp.com.cn'
requests=[]
ranges=[(2021,120,151),(2022,1,100),(2022,101,151),(2023,1,100),(2023,101,151),(2024,1,100),(2024,101,151),(2025,1,100),(2025,101,151),(2026,1,100),(2026,101,132)]
for yr,a,b in ranges:
 begin=f'{yr}{a:03d}';end=f'{yr}{b:03d}'
 params={'view':'previous','limit':'100','start_issue':begin,'end_issue':end}
 requests.append((f'shanghai_{begin}_{end}',BASE_SH+'?'+urlencode(params)))
for p in range(1,31):
 requests.append((f'sichuan_history_page_{p}',BASE_SC+'/ssqinfo'+(f'_{p}' if p>1 else '')+'.jhtml'))
def fetch(item):
 name,url=item
 body=ROOT/(name+'.body');headers=ROOT/(name+'.headers')
 args=['curl','--silent','--show-error','--fail','--location','--compressed','--retry','1','--connect-timeout','12','--max-time','45','--dump-header',str(headers),'--output',str(body),url]
 proc=subprocess.run(args,capture_output=True,text=True)
 r={'name':name,'url':url,'fetched_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':proc.returncode==0,'exit_code':proc.returncode,'stderr':proc.stderr[:1000],'body':str(body),'headers':str(headers)}
 for k in ('body','headers'):
  p=pathlib.Path(r[k]);data=p.read_bytes() if p.exists() else b''
  r[k+'_sha256']=hashlib.sha256(data).hexdigest() if p.exists() else None
  r[k+'_size_bytes']=len(data)
 if r['success'] and name.startswith('shanghai_'):
  content=body.read_text(errors='replace');matches=re.findall(r'<tr><td>(20\d{5})</td>',content)
  r['visible_issues']=sorted(set(matches));r['visible_issue_count']=len(r['visible_issues'])
 return r
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
 results=list(ex.map(fetch,requests))
pdfs={}
for r in results:
 if not r['name'].startswith('sichuan_history_') or not r['success']:continue
 html=pathlib.Path(r['body']).read_text(errors='replace')
 for url in re.findall(r'href="(https://www\.scflcp\.com\.cn/assets/[^"]+?\.pdf)"',html):
  pdfs[url]=True
pdfrequests=[(f'sichuan_pdf_{i+1:03d}',url) for i,url in enumerate(sorted(pdfs))]
print('PDF links extracted from official history HTML',len(pdfrequests),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
 results+=list(ex.map(fetch,pdfrequests))
manifest={'schema':'SSQ-OFFICIAL-R3-EXTENDED-RAW-v1','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'records':results}
(ROOT/'RAW_EVIDENCE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('TOTAL',len(results),'OK',sum(x['success'] for x in results),flush=True)
for r in results:
 if r['name'].startswith('shanghai_'):
  print(r['name'],r['success'],r.get('visible_issue_count'),flush=True)

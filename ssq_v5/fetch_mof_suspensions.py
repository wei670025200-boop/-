#!/usr/bin/env python3
from __future__ import annotations
import hashlib,html,json,re,time,urllib.request
from pathlib import Path

SOURCES={
  2021:"https://zhs.mof.gov.cn/zhengcefabu/202012/t20201215_3634843.htm",
  2022:"https://zhs.mof.gov.cn/zhengcefabu/202112/t20211216_3775649.htm",
  2023:"https://m.mof.gov.cn/czxw/202212/t20221227_3860392.htm",
  2024:"https://zhs.mof.gov.cn/zhengcefabu/202312/t20231201_3919333.htm",
  2025:"https://zhs.mof.gov.cn/zhengcefabu/202412/t20241206_3949123.htm",
  2026:"https://www.mof.gov.cn/gp/xxgkml/zhs/202512/t20251225_3980248.htm",
}
EXPECTED={
  2021:[["2021-02-09","2021-02-18"],["2021-10-01","2021-10-04"]],
  2022:[["2022-01-29","2022-02-07"],["2022-10-01","2022-10-04"]],
  2023:[["2023-01-19","2023-01-28"],["2023-10-01","2023-10-04"]],
  2024:[["2024-02-08","2024-02-17"],["2024-10-01","2024-10-04"]],
  2025:[["2025-01-27","2025-02-05"],["2025-10-01","2025-10-04"]],
  2026:[["2026-02-14","2026-02-23"],["2026-10-01","2026-10-04"]],
}
UA={"User-Agent":"Mozilla/5.0"}

def fetch(url):
 last=None
 for i in range(4):
  try:
   req=urllib.request.Request(url,headers=UA)
   with urllib.request.urlopen(req,timeout=45) as r:return r.read()
  except Exception as e:last=e;time.sleep(1+i)
 raise last

def decode(b):
 for enc in ("utf-8","gb18030"):
  try:return b.decode(enc)
  except:pass
 return b.decode("utf-8","ignore")

def parse(year,raw):
 s=html.unescape(re.sub(r"<[^>]+>"," ",decode(raw)))
 s=re.sub(r"\s+"," ",s)
 # pair all explicit same-year or abbreviated date ranges
 pat=re.compile(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日(?:\s*\d{1,2}:\d{2})?\s*至\s*(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日")
 out=[]
 for m in pat.finditer(s):
  sy=int(m.group(1) or year); ey=int(m.group(4) or sy)
  if sy!=year or ey!=year:continue
  a=f"{sy:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
  z=f"{ey:04d}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
  if [a,z] not in out:out.append([a,z])
 return out

def main():
 root=Path("out_mof");rawdir=root/"raw";rawdir.mkdir(parents=True,exist_ok=True)
 rec=[]
 for y,u in SOURCES.items():
  b=fetch(u);(rawdir/f"{y}.html").write_bytes(b);p=parse(y,b)
  row={"year":y,"url":u,"authority_id":"MINISTRY_OF_FINANCE_CHINA","parser_version":"mof_annual_suspension_html_v1",
       "raw_path":f"raw/{y}.html","raw_sha256":hashlib.sha256(b).hexdigest(),"parsed_ranges":p,"expected_ranges":EXPECTED[y],"match":p==EXPECTED[y]}
  print(json.dumps(row,ensure_ascii=False),flush=True);rec.append(row)
 if not all(r["match"] for r in rec):raise SystemExit(2)
 (root/"MANIFEST.json").write_text(json.dumps({"schema":"SSQ_MOF_SUSPENSION_2021_2026_1.0","records":rec},ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__":main()

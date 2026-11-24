from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

API_URL = "https://www.yzfcw.com/api/cms/content/lottery?sid=846994348564549&cid=849209070977093&ps=500"
BASE = "https://www.yzfcw.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/158.0 Safari/537.36"
ROOT = Path("canonical_v2_hebei")
RAW = ROOT / "raw"
OUT = ROOT / "hebei_full_500.jsonl"
MANIFEST = ROOT / "HEBEI_500_MANIFEST.json"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def digits(s: str) -> int:
    x = re.sub(r"[^0-9]", "", s or "")
    if not x:
        raise ValueError(f"no integer in {s!r}")
    return int(x)


def money(s: str) -> float:
    x = re.sub(r"[^0-9.]", "", s or "")
    if not x:
        raise ValueError(f"no money in {s!r}")
    return float(x)


def get_bytes(url: str, tries: int = 5) -> bytes:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=40)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def find_stat(soup: BeautifulSoup, label: str) -> float:
    for item in soup.select(".qlc-stat-item"):
        ps = item.find_all("p")
        if len(ps) >= 2 and label in ps[0].get_text(" ", strip=True):
            return money(ps[1].get_text(" ", strip=True))
    raise ValueError(f"stat not found: {label}")


def parse_detail(raw: bytes, expected_issue: str, url: str) -> dict:
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(" ", strip=True)

    m = re.search(r"第\s*(\d{7})\s*期", text)
    if not m or m.group(1) != expected_issue:
        raise ValueError(f"issue mismatch expected={expected_issue} got={m.group(1) if m else None}")

    mdate = re.search(r"开奖日期：\s*(\d{4}-\d{2}-\d{2})", text)
    if not mdate:
        raise ValueError("draw date missing")

    red = [int(x.get_text(strip=True)) for x in soup.select(".qlc-balls span.red")]
    blue = [int(x.get_text(strip=True)) for x in soup.select(".qlc-balls span.blue")]
    if len(red) != 6 or len(blue) != 1:
        raise ValueError(f"bad balls red={red} blue={blue}")

    tiers = {}
    for label in ("一等奖","二等奖","三等奖","四等奖","五等奖","六等奖"):
        row = None
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td","th"])
            if tds and tds[0].get_text(" ", strip=True) == label:
                row = tds
                break
        if row is None or len(row) < 3:
            raise ValueError(f"tier row missing {label}")
        tiers[label] = digits(row[2].get_text(" ", strip=True))

    raw_sha = sha256(raw)
    return {
        "issue": expected_issue,
        "draw_date_bjt": mdate.group(1),
        "reds": sorted(red),
        "blue": blue[0],
        "sales_rmb": find_stat(soup, "本期全国销售额"),
        "pool_after_rmb": find_stat(soup, "奖池金额"),
        "tier1_count": tiers["一等奖"],
        "tier2_count": tiers["二等奖"],
        "tier3_count": tiers["三等奖"],
        "tier4_count": tiers["四等奖"],
        "tier5_count": tiers["五等奖"],
        "tier6_count": tiers["六等奖"],
        "promo_second_special": "二等奖特别奖" in text,
        "promo_sixth_double": "六等奖翻番奖" in text,
        "source_url": url,
        "raw_html_path": f"raw/{expected_issue}.html",
        "raw_html_sha256": raw_sha,
        "authority_id": "HEBEI_WELFARE_LOTTERY",
        "parser_version": "hebei_ssq_detail_html_v1",
    }


def fetch_one(row: dict) -> tuple[dict, bytes]:
    issue = str(row["issueNo"])
    url = BASE + str(row["link"])
    raw = get_bytes(url)
    rec = parse_detail(raw, issue, url)
    api_nums = [int(x) for x in str(row.get("kjNumbers","")).split(",") if x.strip()]
    if len(api_nums) == 7:
        if sorted(api_nums[:6]) != rec["reds"] or api_nums[6] != rec["blue"]:
            raise ValueError(f"API/detail balls conflict {issue}")
    return rec, raw


def main() -> None:
    ROOT.mkdir(exist_ok=True)
    RAW.mkdir(exist_ok=True)
    api_raw = get_bytes(API_URL)
    (ROOT / "lottery500.json").write_bytes(api_raw)
    doc = json.loads(api_raw.decode("utf-8"))
    rows = doc["data"]["rows"]
    if len(rows) != 500:
        raise RuntimeError(f"expected 500 API rows, got {len(rows)}")

    results = []
    errors = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_one, r): r for r in rows}
        for fut in cf.as_completed(futs):
            r = futs[fut]
            issue = str(r.get("issueNo"))
            try:
                rec, raw = fut.result()
                (RAW / f"{issue}.html").write_bytes(raw)
                results.append(rec)
            except Exception as e:
                errors.append({"issue": issue, "error": repr(e)})

    results.sort(key=lambda x: (x["draw_date_bjt"], x["issue"]))
    with OUT.open("w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",",":")) + "\n")

    issues = [r["issue"] for r in results]
    manifest = {
        "schema": "SSQ_CANONICAL_V2_HEBEI_FETCH_1.0",
        "api_url": API_URL,
        "api_sha256": sha256(api_raw),
        "api_rows": len(rows),
        "parsed_rows": len(results),
        "errors": errors,
        "first_issue": issues[0] if issues else None,
        "last_issue": issues[-1] if issues else None,
        "missing_source_sha_count": sum(1 for r in results if not r["raw_html_sha256"]),
        "tier6_zero_count": sum(1 for r in results if r["tier6_count"] <= 0),
        "canonical_jsonl_sha256": sha256(OUT.read_bytes()),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if errors or len(results) != 500 or manifest["missing_source_sha_count"] != 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

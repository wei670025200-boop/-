"""Read-only DLT public-source probe for GitHub Actions.
Standard library only. Never writes data/dlt_history.csv, never imports or invokes
the real OOS runner, never retries, and never attempts to bypass WAF/JS challenges.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dlt_source_probe_artifacts"
OUT.mkdir(exist_ok=True)

A_URL = "https://www.17500.cn/getData/dlt.TXT"
BASELINE_URL = "https://kaijiang.500.com/static/info/kaijiang/xml/dlt/list.xml"
B_URLS = [
    ("25100", "https://kaijiang.500.com/static/info/kaijiang/xml/dlt/25100.xml"),
    ("26134", "https://kaijiang.500.com/static/info/kaijiang/xml/dlt/26134.xml"),
]
TARGETS = ("25100", "26013", "26014", "26134")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/plain,application/xml,text/xml,text/html;q=0.8,*/*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
CHALLENGE_MARKERS = (
    b"EO_Bot_Ssid", b"captcha", b"__jsl_clearance", b"document.cookie",
    b"challenge-platform", b"cf-chl-", b"waf", b"security verification",
)

report = {
    "schema": "DLT_PUBLIC_SOURCE_PROBE_V1",
    "read_only": True,
    "real_oos_executed": False,
    "phase4_status": "LOCKED",
    "sources": {},
    "baseline": {},
}
raw_samples = []


def challenge(raw: bytes) -> bool:
    low = raw.lower()
    return any(marker.lower() in low for marker in CHALLENGE_MARKERS)


def fetch_once(url: str, label: str):
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=10) as response:
            raw = response.read()
            result = {
                "label": label,
                "url": url,
                "status": int(response.status),
                "bytes": len(raw),
                "content_type": response.headers.get("Content-Type", ""),
                "challenge": challenge(raw),
                "raw": raw,
            }
    except HTTPError as exc:
        raw = exc.read(2048)
        result = {
            "label": label, "url": url, "status": int(exc.code), "bytes": len(raw),
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "challenge": challenge(raw), "raw": raw,
        }
    except (URLError, TimeoutError, OSError) as exc:
        result = {
            "label": label, "url": url, "status": "UNAVAILABLE", "bytes": 0,
            "content_type": "", "challenge": False, "error": type(exc).__name__ + ":" + str(exc),
            "raw": b"",
        }
    printable = {k: v for k, v in result.items() if k != "raw"}
    print("PROBE_HTTP " + json.dumps(printable, ensure_ascii=False, sort_keys=True), flush=True)
    if result["challenge"]:
        snippet = decode(result["raw"][:1200])[:300].replace("\r", " ").replace("\n", " ")
        print(f"PROBE_CHALLENGE label={label} body_prefix={snippet!r}", flush=True)
    return result


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            pass
    return raw.decode("latin1", errors="replace")


def find_target_lines(text: str):
    lines = text.splitlines()
    found = {}
    for issue in TARGETS:
        matches = [line for line in lines if re.search(rf"(?<!\d){re.escape(issue)}(?!\d)", line)]
        if matches:
            found[issue] = matches[0]
    return lines, found


def probe_a():
    r = fetch_once(A_URL, "A_17500_DLT_TXT")
    info = {k: v for k, v in r.items() if k != "raw"}
    info["valid_payload"] = False
    info["target_lines"] = {}
    if r["status"] == 200 and not r["challenge"]:
        text = decode(r["raw"])
        lines, found = find_target_lines(text)
        nonempty = [x for x in lines if x.strip()]
        info["total_lines"] = len(lines)
        info["nonempty_lines"] = len(nonempty)
        for issue in TARGETS:
            line = found.get(issue)
            if line is None:
                info["target_lines"][issue] = None
                print(f"A_RAW_LINE issue={issue} MISSING", flush=True)
            else:
                cols = line.strip().split()
                info["target_lines"][issue] = {"columns": len(cols), "line": line}
                print(f"A_RAW_LINE issue={issue} columns={len(cols)} line={line}", flush=True)
                raw_samples.append(f"[A {issue}] columns={len(cols)}\n{line}\n")
        info["valid_payload"] = all(issue in found for issue in TARGETS)
        info["raw_tail"] = "\n".join(nonempty[-5:])
        raw_samples.append("[A FILE TAIL]\n" + info["raw_tail"] + "\n")
    report["sources"]["A_17500_DLT_TXT"] = info


def text_has_full_tiers(text: str, issue: str):
    compact = re.sub(r"\s+", "", text)
    # This is deliberately conservative. A probe passes only if prize-tier plus
    # sales/pool/additional concepts are visibly present in the returned static XML.
    tier_tokens = [f"{c}等奖" for c in "一二三四五六七"]
    if issue < "26014":
        tier_tokens += ["八等奖", "九等奖"]
    required = tier_tokens + ["追加"]
    sales_ok = any(x in compact for x in ("销售", "销量", "sale", "Sale"))
    pool_ok = any(x in compact for x in ("奖池", "pool", "Pool"))
    return all(x in compact for x in required) and sales_ok and pool_ok


def probe_b():
    items = []
    for issue, url in B_URLS:
        r = fetch_once(url, f"B_500_STATIC_XML_{issue}")
        info = {k: v for k, v in r.items() if k != "raw"}
        if r["status"] == 200 and not r["challenge"]:
            text = decode(r["raw"])
            info["contains_issue"] = issue in text
            info["contains_full_tier_markers"] = text_has_full_tiers(text, issue)
            info["body_prefix"] = text[:500].replace("\r", " ").replace("\n", " ")
            raw_samples.append(f"[B {issue} FULL RAW]\n{text}\n")
        else:
            info["contains_issue"] = False
            info["contains_full_tier_markers"] = False
        items.append(info)
    report["sources"]["B_500_STATIC_XML"] = {
        "requests": items,
        "valid_payload": all(
            x.get("status") == 200 and not x.get("challenge") and x.get("contains_issue") and x.get("contains_full_tier_markers")
            for x in items
        ),
    }


def smallest_matching_element(root, issue):
    for elem in root.iter():
        own = " ".join([elem.text or "", *elem.attrib.values()])
        if issue in own:
            return elem
    return None


def collect_fragment(elem):
    if elem is None:
        return {}
    out = {"tag": elem.tag, "attributes": dict(elem.attrib), "text": (elem.text or "").strip()}
    children = []
    for child in list(elem):
        children.append({"tag": child.tag, "attributes": dict(child.attrib), "text": (child.text or "").strip()})
    if children:
        out["children"] = children
    return out


def baseline_summary(fragment, issue):
    blob = json.dumps(fragment, ensure_ascii=False)
    date_match = re.search(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", blob)
    # Prefer a recognizable 5+2 expression; otherwise return all small-number tokens
    # for audit rather than inventing a mapping.
    draw_match = re.search(
        r"(?<!\d)(\d{1,2})[,\s+|-]+(\d{1,2})[,\s+|-]+(\d{1,2})[,\s+|-]+(\d{1,2})[,\s+|-]+(\d{1,2})"
        r"[|+,\s-]+(\d{1,2})[,\s+|-]+(\d{1,2})(?!\d)", blob
    )
    return {
        "issue": issue,
        "date": date_match.group(0) if date_match else None,
        "draw_5_plus_2": list(draw_match.groups()) if draw_match else None,
        "fragment": fragment,
    }


def probe_baseline():
    r = fetch_once(BASELINE_URL, "BASELINE_500_LIST_XML")
    info = {k: v for k, v in r.items() if k != "raw"}
    info["targets"] = {}
    if r["status"] == 200 and not r["challenge"]:
        try:
            root = ET.fromstring(r["raw"])
            for issue in TARGETS:
                frag = collect_fragment(smallest_matching_element(root, issue))
                summary = baseline_summary(frag, issue)
                info["targets"][issue] = summary
                print("BASELINE_TARGET " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
                raw_samples.append(f"[BASELINE {issue}]\n{json.dumps(frag,ensure_ascii=False)}\n")
        except ET.ParseError as exc:
            info["xml_parse_error"] = str(exc)
            text = decode(r["raw"])
            for issue in TARGETS:
                pos = text.find(issue)
                frag = text[max(0,pos-300):pos+700] if pos >= 0 else ""
                info["targets"][issue] = {"issue": issue, "raw_fragment": frag}
                print(f"BASELINE_RAW_FRAGMENT issue={issue} fragment={frag!r}", flush=True)
    report["baseline"] = info


def main():
    print("DLT_SOURCE_PROBE_BEGIN read_only=true real_oos=false phase4=LOCKED", flush=True)
    probe_a()
    probe_b()
    report["sources"]["C_PORTAL_JSON"] = {
        "status": "SKIPPED_NO_CONFIDENT_UNAUTHENTICATED_STATIC_ENDPOINT",
        "requests": 0,
        "reason": "No endpoint was guessed; probe budget intentionally preserved.",
    }
    probe_baseline()
    (OUT / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "raw_samples.txt").write_text("\n".join(raw_samples), encoding="utf-8")
    print("PROBE_REPORT " + json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    print("DLT_SOURCE_PROBE_END real_oos=false phase4=LOCKED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

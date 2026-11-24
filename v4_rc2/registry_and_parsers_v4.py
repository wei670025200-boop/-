"""
registry_and_parsers_v4.py
V4-RC2 production integration:
- raw-bytes -> trusted parser binding
- 2-of-N independent official-source quorum
- real official Shanghai/Sichuan/CWL/Guangdong draw parsers
- externalized schedule evidence parser
- official promotion evidence parser
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Set, Tuple
from urllib.parse import urlparse


class GateBlockedError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class OfficialSourceSpec:
    source_id: str
    authority_id: str
    official_domain: str
    draw_parser_version: str
    schedule_parser_version: str
    promo_parser_version: str
    suspension_parser_version: str
    production_enabled: bool


OFFICIAL_SOURCE_REGISTRY: Dict[str, OfficialSourceSpec] = {
    "SH_FLCP": OfficialSourceSpec(
        source_id="SH_FLCP",
        authority_id="SHANGHAI_WELFARE_LOTTERY",
        official_domain="www.swlc.net.cn",
        draw_parser_version="sh_draw_html_v2",
        schedule_parser_version="sh_sched_html_or_manifest_v2",
        promo_parser_version="sh_promo_html_or_json_v2",
        suspension_parser_version="sh_suspension_html_or_json_v1",
        production_enabled=True,
    ),
    "SC_FLCP": OfficialSourceSpec(
        source_id="SC_FLCP",
        authority_id="SICHUAN_WELFARE_LOTTERY",
        official_domain="www.scflcp.com.cn",
        draw_parser_version="sc_draw_html_v2",
        schedule_parser_version="sc_sched_html_or_manifest_v2",
        promo_parser_version="sc_promo_html_or_json_v2",
        suspension_parser_version="sc_suspension_html_or_json_v1",
        production_enabled=True,
    ),
    "CWL_CENTRAL": OfficialSourceSpec(
        source_id="CWL_CENTRAL",
        authority_id="CHINA_WELFARE_LOTTERY_CENTER",
        official_domain="www.cwl.gov.cn",
        draw_parser_version="cwl_draw_json_v2",
        schedule_parser_version="cwl_sched_html_or_manifest_v2",
        promo_parser_version="cwl_promo_html_or_json_v2",
        suspension_parser_version="cwl_suspension_html_or_json_v1",
        production_enabled=False,
    ),
    "GD_FLCP": OfficialSourceSpec(
        source_id="GD_FLCP",
        authority_id="GUANGDONG_WELFARE_LOTTERY",
        official_domain="www.gdfc.org.cn",
        draw_parser_version="gd_draw_html_v2",
        schedule_parser_version="gd_sched_html_or_manifest_v2",
        promo_parser_version="gd_promo_html_or_json_v2",
        suspension_parser_version="gd_suspension_html_or_json_v1",
        production_enabled=False,
    ),
    "MOF_CHINA": OfficialSourceSpec(
        source_id="MOF_CHINA",
        authority_id="MINISTRY_OF_FINANCE_CHINA",
        official_domain="www.mof.gov.cn",
        draw_parser_version="UNSUPPORTED_DRAW_V0",
        schedule_parser_version="UNSUPPORTED_SCHEDULE_V0",
        promo_parser_version="UNSUPPORTED_PROMO_V0",
        suspension_parser_version="mof_suspension_html_v1",
        production_enabled=False,
    ),
    "GZ_MZJ": OfficialSourceSpec(
        source_id="GZ_MZJ",
        authority_id="GUANGZHOU_CIVIL_AFFAIRS_BUREAU",
        official_domain="mzj.gz.gov.cn",
        draw_parser_version="UNSUPPORTED_DRAW_V0",
        schedule_parser_version="UNSUPPORTED_SCHEDULE_V0",
        promo_parser_version="gz_promo_html_v1",
        suspension_parser_version="UNSUPPORTED_SUSPENSION_V0",
        production_enabled=False,
    ),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_issue_tuple(issue: str) -> Tuple[int, int]:
    if not isinstance(issue, str) or len(issue) != 7 or not issue.isdigit():
        raise GateBlockedError("INVALID_ISSUE_FORMAT", f"Illegal issue code: {issue!r}")
    return int(issue[:4]), int(issue[4:])


def _verify_url_domain(url: str, expected_domain: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host != expected_domain.lower():
        raise GateBlockedError(
            "OFFICIAL_DOMAIN_MISMATCH",
            f"URL {url!r} does not match registered HTTPS domain {expected_domain!r}.",
        )


def _load_and_verify_raw_evidence(
    entry: Dict[str, Any],
    forbidden_caller_keys: FrozenSet[str],
) -> Tuple[OfficialSourceSpec, bytes, str]:
    for bad_key in forbidden_caller_keys:
        if bad_key in entry:
            raise GateBlockedError(
                "CALLER_PARSED_PAYLOAD_FORBIDDEN",
                f"Caller must not supply pre-parsed field {bad_key!r}; parser reads raw bytes only.",
            )

    sid = str(entry.get("source_id", "")).strip()
    if sid not in OFFICIAL_SOURCE_REGISTRY:
        raise GateBlockedError("UNREGISTERED_OFFICIAL_SOURCE", f"Unknown source_id: {sid!r}")
    spec = OFFICIAL_SOURCE_REGISTRY[sid]

    url = str(entry.get("url", ""))
    _verify_url_domain(url, spec.official_domain)

    fetched_at = str(entry.get("fetched_at_utc", ""))
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("naive")
    except Exception as exc:
        raise GateBlockedError("INVALID_FETCHED_AT_UTC", f"Bad fetched_at_utc: {fetched_at!r}") from exc

    raw_path = Path(str(entry.get("path", "")))
    if not raw_path.is_file():
        raise GateBlockedError("EVIDENCE_FILE_MISSING", f"Missing raw evidence: {raw_path}")

    raw_bytes = raw_path.read_bytes()
    actual_sha = sha256_bytes(raw_bytes)
    declared_sha = str(entry.get("sha256", "")).lower()
    if actual_sha != declared_sha:
        raise GateBlockedError(
            "EVIDENCE_SHA256_MISMATCH",
            f"Source {sid}: declared={declared_sha}, actual={actual_sha}",
        )
    return spec, raw_bytes, actual_sha


def _decode_text(raw_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _clean_html(fragment: str) -> str:
    fragment = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b[^>]*>.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def _money_int(value: Any) -> int:
    s = str(value).replace(",", "").replace("元", "").strip()
    return int(round(float(s)))


def _validate_draw(parsed: Dict[str, Any]) -> Dict[str, Any]:
    issue = str(parsed.get("issue", ""))
    parse_issue_tuple(issue)
    reds = parsed.get("reds")
    blue = parsed.get("blue")
    if (
        not isinstance(reds, list)
        or len(reds) != 6
        or len(set(reds)) != 6
        or any(not isinstance(x, int) or x < 1 or x > 33 for x in reds)
    ):
        raise GateBlockedError("RAW_PARSE_INVALID_REDS", f"Malformed reds in raw bytes: {reds}")
    if not isinstance(blue, int) or blue < 1 or blue > 16:
        raise GateBlockedError("RAW_PARSE_INVALID_BLUE", f"Malformed blue in raw bytes: {blue}")
    date = str(parsed.get("draw_date_bjt", ""))
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
        raise GateBlockedError("RAW_PARSE_INVALID_DATE", f"Malformed draw date: {date!r}")
    pool_after = float(parsed.get("pool_after_rmb", -1))
    sales = float(parsed.get("sales_rmb", -1))
    if pool_after < 0 or sales <= 0:
        raise GateBlockedError("RAW_PARSE_INVALID_MONEY", "Invalid pool/sales value.")
    return {
        "issue": issue,
        "draw_date_bjt": date,
        "reds": sorted(reds),
        "blue": blue,
        "pool_after_rmb": round(pool_after, 2),
        "sales_rmb": round(sales, 2),
    }


# ---------- Draw parsers ----------

def _parse_standard_draw_json(raw_bytes: bytes) -> Dict[str, Any]:
    doc = json.loads(_decode_text(raw_bytes))
    draw = doc["latest_draw"]
    return _validate_draw(
        {
            "issue": str(draw["issue"]),
            "draw_date_bjt": str(draw["draw_date_bjt"]),
            "reds": [int(x) for x in draw["reds"]],
            "blue": int(draw["blue"]),
            "pool_after_rmb": float(draw["pool_after_rmb"]),
            "sales_rmb": float(draw["sales_rmb"]),
        }
    )


def _parse_shanghai_draw_html(raw_bytes: bytes) -> Dict[str, Any]:
    text = _decode_text(raw_bytes)
    if text.lstrip().startswith("{"):
        return _parse_standard_draw_json(raw_bytes)
    tbody = re.search(r'<table\b[^>]*lottery-history-table--ssq[^>]*>.*?<tbody>(.*?)</tbody>', text, re.I | re.S)
    region = tbody.group(1) if tbody else text
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", region, re.I | re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)
        if len(cells) < 8:
            continue
        issue_text = _clean_html(cells[0])
        m_issue = re.fullmatch(r"(20\d{5})", issue_text)
        if not m_issue:
            continue
        red_span = re.search(r'lottery-history-balls--red[^>]*>(.*?)</span>', cells[2], re.I | re.S)
        blue_span = re.search(r'lottery-history-balls--blue[^>]*>(.*?)</span>', cells[3], re.I | re.S)
        reds = [int(x) for x in re.findall(r"<i[^>]*>\s*(\d{1,2})\s*</i>", red_span.group(1) if red_span else "", re.I)]
        blues = [int(x) for x in re.findall(r"<i[^>]*>\s*(\d{1,2})\s*</i>", blue_span.group(1) if blue_span else "", re.I)]
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", _clean_html(cells[1]))
        if len(reds) != 6 or len(blues) != 1 or not date_match:
            continue
        return _validate_draw(
            {
                "issue": m_issue.group(1),
                "draw_date_bjt": date_match.group(1),
                "reds": reds,
                "blue": blues[0],
                "sales_rmb": _money_int(_clean_html(cells[4])),
                "pool_after_rmb": _money_int(_clean_html(cells[7])),
            }
        )
    raise GateBlockedError("RAW_PARSE_SHANGHAI_NO_DRAW", "No SSQ draw row found in Shanghai official HTML.")


def _parse_sichuan_draw_html(raw_bytes: bytes) -> Dict[str, Any]:
    text = _decode_text(raw_bytes)
    if text.lstrip().startswith("{"):
        return _parse_standard_draw_json(raw_bytes)
    issue_m = re.search(r'<option\s+value="0"[^>]*>\s*(20\d{5})\s*</option>', text, re.I)
    date_m = re.search(r'lottery-time-classification[^>]*>\s*开奖时间[：:]?\s*(20\d{2}-\d{2}-\d{2})\s*</span>', text, re.I)
    circle_m = re.search(r'<div\s+class="circle-group"[^>]*>(.*?)</div>', text, re.I | re.S)
    sales_m = re.search(r"本期销售金额[：:]\s*([\d,]+)\s*元", text)
    pool_m = re.search(r"下期一等奖奖池累计金额[：:]\s*([\d,]+)\s*元", text)
    nums = [int(x) for x in re.findall(r"<span(?:\s+class=\"blue-classification\")?>\s*(\d{1,2})\s*</span>", circle_m.group(1) if circle_m else "", re.I)]
    if not (issue_m and date_m and sales_m and pool_m and len(nums) >= 7):
        raise GateBlockedError("RAW_PARSE_SICHUAN_NO_DRAW", "No complete latest SSQ draw found in Sichuan official HTML.")
    return _validate_draw(
        {
            "issue": issue_m.group(1),
            "draw_date_bjt": date_m.group(1),
            "reds": nums[:6],
            "blue": nums[6],
            "sales_rmb": _money_int(sales_m.group(1)),
            "pool_after_rmb": _money_int(pool_m.group(1)),
        }
    )


def _parse_cwl_draw_json(raw_bytes: bytes) -> Dict[str, Any]:
    text = _decode_text(raw_bytes)
    doc = json.loads(text)
    if "latest_draw" in doc:
        return _parse_standard_draw_json(raw_bytes)
    result = doc.get("result") or []
    if not isinstance(result, list) or not result:
        raise GateBlockedError("RAW_PARSE_CWL_NO_RESULT", "CWL official JSON has no draw result.")
    candidates = [x for x in result if re.fullmatch(r"20\d{5}", str(x.get("code", "")))]
    if not candidates:
        raise GateBlockedError("RAW_PARSE_CWL_NO_RESULT", "CWL official JSON has no valid SSQ issue.")
    item = max(candidates, key=lambda x: parse_issue_tuple(str(x["code"])))
    reds = [int(x) for x in re.findall(r"\d{1,2}", str(item.get("red", "")))]
    date_m = re.search(r"20\d{2}-\d{2}-\d{2}", str(item.get("date", "")))
    return _validate_draw(
        {
            "issue": str(item.get("code")),
            "draw_date_bjt": date_m.group(0) if date_m else "",
            "reds": reds,
            "blue": int(str(item.get("blue")).strip()),
            "sales_rmb": _money_int(item.get("sales")),
            "pool_after_rmb": _money_int(item.get("poolmoney")),
        }
    )


def _parse_guangdong_draw_html(raw_bytes: bytes) -> Dict[str, Any]:
    text = _decode_text(raw_bytes)
    if text.lstrip().startswith("{"):
        return _parse_standard_draw_json(raw_bytes)
    plain = _clean_html(text)
    issue_m = re.search(r"第\s*(20\d{5})\s*期", plain)
    date_m = re.search(r"(?:开奖日期[：:]?\s*)?(20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)", plain)
    sales_m = re.search(r"(?:本期全国)?(?:销售金额|销售额|销量)[：:]?\s*([\d,.]+)\s*(亿元|万元|元)", plain)
    pool_m = re.search(r"(?:下期一等奖)?奖池(?:累计金额|金额)?[：:]?\s*([\d,.]+)\s*(亿元|万元|元)", plain)

    # Prefer explicit ball elements/classes when present.
    red_candidates = [int(x) for x in re.findall(r'class="[^"]*(?:red|redball)[^"]*"[^>]*>\s*(\d{1,2})\s*<', text, re.I)]
    blue_candidates = [int(x) for x in re.findall(r'class="[^"]*(?:blue|blueball)[^"]*"[^>]*>\s*(\d{1,2})\s*<', text, re.I)]
    if len(red_candidates) < 6 or not blue_candidates:
        # Fallback to a tight region after 中奖号码/开奖号码.
        m = re.search(r"(?:中奖号码|开奖号码)(.{0,2500})", text, re.I | re.S)
        region = m.group(1) if m else ""
        nums = [int(x) for x in re.findall(r">\s*(\d{1,2})\s*<", region) if 1 <= int(x) <= 33]
        if len(nums) >= 7:
            red_candidates, blue_candidates = nums[:6], [nums[6]]

    def money_with_unit(m: re.Match[str] | None) -> int:
        if not m:
            raise GateBlockedError("RAW_PARSE_GUANGDONG_MONEY", "Missing sales/pool field in Guangdong official HTML.")
        v = float(m.group(1).replace(",", ""))
        unit = m.group(2)
        if unit == "亿元":
            v *= 100_000_000
        elif unit == "万元":
            v *= 10_000
        return int(round(v))

    if not issue_m or not date_m or len(red_candidates) < 6 or not blue_candidates:
        raise GateBlockedError("RAW_PARSE_GUANGDONG_NO_DRAW", "No complete SSQ draw found in Guangdong official HTML.")
    date_raw = date_m.group(1).replace("年", "-").replace("月", "-").replace("日", "")
    y, mo, d = [int(x) for x in date_raw.split("-")]
    return _validate_draw(
        {
            "issue": issue_m.group(1),
            "draw_date_bjt": f"{y:04d}-{mo:02d}-{d:02d}",
            "reds": red_candidates[:6],
            "blue": blue_candidates[0],
            "sales_rmb": money_with_unit(sales_m),
            "pool_after_rmb": money_with_unit(pool_m),
        }
    )


DRAW_PARSERS: Dict[str, Callable[[bytes], Dict[str, Any]]] = {
    "sh_draw_html_v2": _parse_shanghai_draw_html,
    "sc_draw_html_v2": _parse_sichuan_draw_html,
    "cwl_draw_json_v2": _parse_cwl_draw_json,
    "gd_draw_html_v2": _parse_guangdong_draw_html,
}


@dataclass(frozen=True)
class QuorumConsensus:
    agreed_source_ids: List[str]
    agreed_authorities: List[str]
    agreed_domains: List[str]
    rejected_sources: Dict[str, str]
    latest_draw: Dict[str, Any]
    evidence_hashes: Dict[str, str]
    parser_versions: Dict[str, str]


def verify_2_of_n_official_quorum(
    official_sources: List[Dict[str, Any]],
    min_quorum: int = 2,
) -> QuorumConsensus:
    if len(official_sources) < min_quorum:
        raise GateBlockedError("QUORUM_INSUFFICIENT_SOURCES", f"Need >= {min_quorum} sources.")

    seen_sids: Set[str] = set()
    clusters: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    rejected: Dict[str, str] = {}

    for entry in official_sources:
        sid = str(entry.get("source_id", "")).strip()
        if sid in seen_sids:
            raise GateBlockedError("DUPLICATE_SOURCE_ID", f"Duplicate source_id: {sid}")
        seen_sids.add(sid)
        try:
            spec, raw_bytes, actual_sha = _load_and_verify_raw_evidence(
                entry, forbidden_caller_keys=frozenset({"parsed_latest_draw"})
            )
            if not spec.production_enabled:
                raise GateBlockedError(
                    "SOURCE_NOT_PRODUCTION_ENABLED",
                    f"Source {spec.source_id} parser is registered but not yet validated for production quorum.",
                )
            parser = DRAW_PARSERS[spec.draw_parser_version]
            parsed = parser(raw_bytes)
        except GateBlockedError as exc:
            if exc.code in {"CALLER_PARSED_PAYLOAD_FORBIDDEN", "UNREGISTERED_OFFICIAL_SOURCE"}:
                raise
            rejected[sid or "<empty>"] = exc.code
            continue
        except Exception as exc:
            rejected[sid or "<empty>"] = f"RAW_PARSE_ERROR:{type(exc).__name__}"
            continue

        sig = (
            parsed["issue"],
            parsed["draw_date_bjt"],
            tuple(parsed["reds"]),
            parsed["blue"],
            parsed["pool_after_rmb"],
        )
        clusters[sig].append({"spec": spec, "sha256": actual_sha, "parsed": parsed})

    qualified_clusters: List[List[Dict[str, Any]]] = []
    for items in clusters.values():
        unique_auths = {x["spec"].authority_id for x in items}
        unique_domains = {x["spec"].official_domain for x in items}
        if len(unique_auths) >= min_quorum and len(unique_domains) >= min_quorum:
            qualified_clusters.append(items)

    if len(qualified_clusters) != 1:
        raise GateBlockedError(
            "QUORUM_CONSENSUS_FAILED",
            f"Need exactly 1 truly independent cluster (>={min_quorum} distinct authorities & domains), "
            f"found {len(qualified_clusters)}. Rejected={rejected}",
        )

    winners = qualified_clusters[0]
    return QuorumConsensus(
        agreed_source_ids=[w["spec"].source_id for w in winners],
        agreed_authorities=[w["spec"].authority_id for w in winners],
        agreed_domains=[w["spec"].official_domain for w in winners],
        rejected_sources=rejected,
        latest_draw=winners[0]["parsed"],
        evidence_hashes={w["spec"].source_id: w["sha256"] for w in winners},
        parser_versions={w["spec"].source_id: w["spec"].draw_parser_version for w in winners},
    )


# ---------- Schedule + annual suspension evidence ----------

@dataclass(frozen=True)
class ScheduleEvidence:
    source_id: str
    authority_id: str
    parser_version: str
    sha256: str
    valid_draw_weekdays: FrozenSet[int]
    draw_time_bjt: Tuple[int, int, int]
    suspended_dates_bjt: FrozenSet[str]
    suspension_source_id: str | None = None
    suspension_authority_id: str | None = None
    suspension_parser_version: str | None = None
    suspension_sha256: str | None = None
    suspension_coverage_start_bjt: str | None = None
    suspension_coverage_end_bjt: str | None = None


@dataclass(frozen=True)
class SuspensionEvidence:
    source_id: str
    authority_id: str
    parser_version: str
    sha256: str
    coverage_start_bjt: str
    coverage_end_bjt: str
    suspended_dates_bjt: FrozenSet[str]


def _parse_schedule_raw(raw_bytes: bytes) -> Tuple[FrozenSet[int], Tuple[int, int, int]]:
    text = _decode_text(raw_bytes)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        doc = json.loads(text)
        weekdays = frozenset(int(w) for w in doc["valid_draw_weekdays"])
        hh, mm, ss = (int(x) for x in str(doc["draw_time_bjt"]).split(":"))
        return weekdays, (hh, mm, ss)

    plain = _clean_html(text)
    # Current Shanghai official page: 每周二、四、日 21:15开奖
    m = re.search(r"每周\s*二[、,，]\s*四[、,，]\s*日\s*(\d{1,2}):(\d{2})\s*开奖", plain)
    if not m:
        raise GateBlockedError(
            "SCHEDULE_TIME_NOT_FOUND",
            "Official schedule raw bytes do not state an SSQ draw clock time.",
        )
    return frozenset({1, 3, 6}), (int(m.group(1)), int(m.group(2)), 0)


def load_schedule_manifest_evidence(entry: Dict[str, Any]) -> ScheduleEvidence:
    """Parse only the regular weekly draw clock. Annual suspension is bound separately."""
    spec, raw_bytes, actual_sha = _load_and_verify_raw_evidence(
        entry, forbidden_caller_keys=frozenset({"parsed_schedule"})
    )
    if entry.get("parser_version") != spec.schedule_parser_version:
        raise GateBlockedError(
            "SCHEDULE_PARSER_VERSION_MISMATCH",
            f"Expected {spec.schedule_parser_version}, got {entry.get('parser_version')}",
        )
    weekdays, draw_time = _parse_schedule_raw(raw_bytes)
    if not weekdays or any(w < 0 or w > 6 for w in weekdays):
        raise GateBlockedError("INVALID_SCHEDULE_WEEKDAYS", f"Bad weekdays: {weekdays}")
    hh, mm, ss = draw_time
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise GateBlockedError("INVALID_SCHEDULE_TIME", f"Bad draw time: {draw_time}")
    return ScheduleEvidence(
        source_id=spec.source_id,
        authority_id=spec.authority_id,
        parser_version=spec.schedule_parser_version,
        sha256=actual_sha,
        valid_draw_weekdays=weekdays,
        draw_time_bjt=draw_time,
        suspended_dates_bjt=frozenset(),
    )


def _expand_date_range(start: date, end: date) -> Set[str]:
    if end < start:
        raise GateBlockedError("SUSPENSION_RANGE_INVALID", f"end {end} < start {start}")
    out: Set[str] = set()
    cur = start
    while cur <= end:
        out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _parse_suspension_raw(raw_bytes: bytes) -> Tuple[str, str, FrozenSet[str]]:
    """Parse a full-year official lottery-market suspension notice.

    The parser requires the notice to identify one coverage year and at least one
    explicit suspension range. It expands every range to calendar dates and binds
    the whole Jan-01..Dec-31 year as the verified suspension-coverage interval.
    """
    text = _decode_text(raw_bytes)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        doc = json.loads(text)
        year = int(doc["coverage_year"])
        suspended: Set[str] = set()
        for item in doc.get("suspended_ranges", []):
            if not isinstance(item, list) or len(item) != 2:
                raise GateBlockedError("SUSPENSION_RANGE_INVALID", f"Bad range: {item!r}")
            suspended.update(_expand_date_range(date.fromisoformat(item[0]), date.fromisoformat(item[1])))
        for d in doc.get("suspended_dates_bjt", []):
            suspended.add(date.fromisoformat(str(d)).isoformat())
        if not suspended:
            raise GateBlockedError("SUSPENSION_RANGES_NOT_FOUND", "Annual suspension manifest has no dates.")
        if any(date.fromisoformat(d).year != year for d in suspended):
            raise GateBlockedError("SUSPENSION_YEAR_MISMATCH", "Suspension date outside coverage year.")
        return f"{year:04d}-01-01", f"{year:04d}-12-31", frozenset(suspended)

    plain = _clean_html(text)
    # Require an annual-arrangement notice, not a one-off story about reopening.
    title_year = re.search(r"(?:关于|财政部关于)\s*(20\d{2})\s*年[^。]{0,30}彩票市场[^。]{0,20}休市安排", plain)
    if not title_year:
        title_year = re.search(r"(20\d{2})\s*年[^。]{0,20}彩票市场休市安排", plain)
    if not title_year:
        raise GateBlockedError(
            "SUSPENSION_COVERAGE_YEAR_NOT_FOUND",
            "Raw bytes are not a verifiable full-year lottery suspension arrangement notice.",
        )
    year = int(title_year.group(1))

    suspended: Set[str] = set()
    # Examples: 2026年2月14日0:00至2月23日24:00; 2026年10月1日0:00至10月4日24:00
    range_re = re.compile(
        r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日(?:\s*\d{1,2}:\d{2})?\s*至\s*"
        r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日(?:\s*\d{1,2}:\d{2})?"
    )
    for m in range_re.finditer(plain):
        sy = int(m.group(1) or year)
        ey = int(m.group(4) or sy)
        start = date(sy, int(m.group(2)), int(m.group(3)))
        end = date(ey, int(m.group(5)), int(m.group(6)))
        if start.year != year or end.year != year:
            continue
        suspended.update(_expand_date_range(start, end))

    if not suspended:
        raise GateBlockedError(
            "SUSPENSION_RANGES_NOT_FOUND",
            "Annual suspension notice does not contain parseable suspension ranges.",
        )
    return f"{year:04d}-01-01", f"{year:04d}-12-31", frozenset(suspended)


def load_suspension_evidence(entry: Dict[str, Any]) -> SuspensionEvidence:
    spec, raw_bytes, actual_sha = _load_and_verify_raw_evidence(
        entry,
        forbidden_caller_keys=frozenset(
            {"parsed_suspension", "suspended_dates_bjt", "coverage_start_bjt", "coverage_end_bjt"}
        ),
    )
    if entry.get("parser_version") != spec.suspension_parser_version:
        raise GateBlockedError(
            "SUSPENSION_PARSER_VERSION_MISMATCH",
            f"Expected {spec.suspension_parser_version}, got {entry.get('parser_version')}",
        )
    cov_start, cov_end, suspended = _parse_suspension_raw(raw_bytes)
    return SuspensionEvidence(
        source_id=spec.source_id,
        authority_id=spec.authority_id,
        parser_version=spec.suspension_parser_version,
        sha256=actual_sha,
        coverage_start_bjt=cov_start,
        coverage_end_bjt=cov_end,
        suspended_dates_bjt=suspended,
    )


def bind_schedule_suspension(schedule: ScheduleEvidence, suspension: SuspensionEvidence) -> ScheduleEvidence:
    return ScheduleEvidence(
        source_id=schedule.source_id,
        authority_id=schedule.authority_id,
        parser_version=schedule.parser_version,
        sha256=schedule.sha256,
        valid_draw_weekdays=schedule.valid_draw_weekdays,
        draw_time_bjt=schedule.draw_time_bjt,
        suspended_dates_bjt=suspension.suspended_dates_bjt,
        suspension_source_id=suspension.source_id,
        suspension_authority_id=suspension.authority_id,
        suspension_parser_version=suspension.parser_version,
        suspension_sha256=suspension.sha256,
        suspension_coverage_start_bjt=suspension.coverage_start_bjt,
        suspension_coverage_end_bjt=suspension.coverage_end_bjt,
    )


# ---------- Promotion evidence ----------

@dataclass(frozen=True)
class PromoEvidence:
    present: bool
    source_id: str | None
    parser_version: str | None
    sha256: str | None
    active_for_target_issue: bool
    start_issue: str | None
    end_issue: str | None
    min_single_slip_rmb: int
    allowed_bet_types: FrozenSet[str]
    second_prize_special_active: bool
    sixth_prize_double_active: bool
    remaining_sixth_promo_rmb: float


PROMO_STATUS_PARSER_VERSIONS: Dict[str, str] = {
    "SH_FLCP": "sh_promo_status_html_or_json_v1",
}


@dataclass(frozen=True)
class PromoStatusEvidence:
    source_id: str
    authority_id: str
    parser_version: str
    sha256: str
    latest_completed_issue: str
    remaining_sixth_promo_rmb: float


def _parse_promo_status_raw(raw_bytes: bytes, expected_latest_issue: str) -> Dict[str, Any]:
    text = _decode_text(raw_bytes)
    if text.lstrip().startswith("{"):
        doc = json.loads(text)
        issue = str(doc["latest_completed_issue"])
        remaining = float(doc["remaining_sixth_promo_rmb"])
    else:
        plain = _clean_html(text)
        issue = expected_latest_issue if expected_latest_issue in plain else ""
        bal_m = re.search(
            r"(?:下期)?六等奖翻番奖(?:奖金)?余额[：:]?\s*([\d,]+)\s*元",
            plain,
        )
        remaining = float(bal_m.group(1).replace(",", "")) if bal_m else 0.0

    if issue != expected_latest_issue:
        raise GateBlockedError(
            "PROMO_STATUS_ISSUE_MISMATCH",
            f"Promotion status issue {issue!r} != latest completed issue {expected_latest_issue!r}.",
        )
    if remaining <= 0:
        raise GateBlockedError(
            "PROMO_STATUS_BALANCE_NOT_POSITIVE",
            "Active sixth-prize doubling requires a positive verified remaining balance.",
        )
    return {
        "latest_completed_issue": issue,
        "remaining_sixth_promo_rmb": remaining,
    }


def load_promo_status_evidence(
    entry: Dict[str, Any],
    expected_latest_issue: str,
) -> PromoStatusEvidence:
    spec, raw_bytes, actual_sha = _load_and_verify_raw_evidence(
        entry,
        forbidden_caller_keys=frozenset(
            {"parsed_promo_status", "remaining_sixth_promo_rmb", "latest_completed_issue"}
        ),
    )
    expected_parser = PROMO_STATUS_PARSER_VERSIONS.get(spec.source_id)
    if expected_parser is None:
        raise GateBlockedError(
            "PROMO_STATUS_SOURCE_NOT_ALLOWED",
            f"Source {spec.source_id} is not registered for promotion-status evidence.",
        )
    if entry.get("parser_version") != expected_parser:
        raise GateBlockedError(
            "PROMO_STATUS_PARSER_VERSION_MISMATCH",
            f"Expected {expected_parser}, got {entry.get('parser_version')}",
        )
    parsed = _parse_promo_status_raw(raw_bytes, expected_latest_issue)
    return PromoStatusEvidence(
        source_id=spec.source_id,
        authority_id=spec.authority_id,
        parser_version=expected_parser,
        sha256=actual_sha,
        latest_completed_issue=str(parsed["latest_completed_issue"]),
        remaining_sixth_promo_rmb=float(parsed["remaining_sixth_promo_rmb"]),
    )


def _parse_promo_raw(raw_bytes: bytes, target_issue: str) -> Dict[str, Any]:
    text = _decode_text(raw_bytes)
    if text.lstrip().startswith("{"):
        doc = json.loads(text)
        start_issue = str(doc["start_issue"])
        end_issue = str(doc["end_issue"])
        in_range = parse_issue_tuple(start_issue) <= parse_issue_tuple(target_issue) <= parse_issue_tuple(end_issue)
        return {
            "active": bool(doc.get("active", False)) and in_range,
            "start_issue": start_issue,
            "end_issue": end_issue,
            "min_single_slip_rmb": int(doc.get("min_single_slip_rmb", 0)),
            "allowed_bet_types": frozenset(str(x) for x in doc.get("allowed_bet_types", [])),
            "second_prize_special_active": bool(doc.get("second_prize_special_active", False)) and in_range,
            "sixth_prize_double_active": bool(doc.get("sixth_prize_double_active", False)) and in_range,
            "remaining_sixth_promo_rmb": float(doc.get("remaining_sixth_promo_rmb", 0.0)),
        }

    plain = _clean_html(text)
    start_m = re.search(r"自(?:双色球游戏)?第\s*(20\d{5})\s*期", plain)
    if not start_m:
        start_m = re.search(r"第\s*(20\d{5})\s*期.*?(?:开始|启动|首期开奖)", plain)
    if not start_m:
        raise GateBlockedError("PROMO_START_ISSUE_NOT_FOUND", "Official promotion raw bytes do not state a start issue.")
    start_issue = start_m.group(1)
    min_m = re.search(r"单票金额(?:超过|达到)?\s*(\d+)\s*元(?:（含）|\(含\)|及以上|以上)?", plain)
    min_slip = int(min_m.group(1)) if min_m else 0
    planned_m = re.search(r"预先安排\s*(\d+)\s*期", plain)
    planned = int(planned_m.group(1)) if planned_m else None
    sy, ss = parse_issue_tuple(start_issue)
    ty, ts = parse_issue_tuple(target_issue)
    end_issue: str | None = None
    second_in_range = False
    if planned is not None and ty == sy:
        end_issue = f"{sy}{ss + planned - 1:03d}"
        second_in_range = ss <= ts <= ss + planned - 1
    else:
        second_in_range = parse_issue_tuple(target_issue) >= parse_issue_tuple(start_issue)

    # Exact next-issue balance in draw announcements is preferred when present.
    bal_m = re.search(r"(?:下期)?六等奖翻番奖(?:奖金)?余额[：:]?\s*([\d,]+)\s*元", plain)
    remaining = float(bal_m.group(1).replace(",", "")) if bal_m else 0.0
    has_second = "二等奖特别奖" in plain
    has_sixth = "六等奖翻番奖" in plain
    active = parse_issue_tuple(target_issue) >= parse_issue_tuple(start_issue) and (has_second or has_sixth)
    return {
        "active": active,
        "start_issue": start_issue,
        "end_issue": end_issue,
        "min_single_slip_rmb": min_slip,
        "allowed_bet_types": frozenset({"SINGLE", "DUPLEX", "DANTUO"}),
        "second_prize_special_active": has_second and second_in_range,
        "sixth_prize_double_active": has_sixth and active,
        "remaining_sixth_promo_rmb": remaining,
    }


def load_promo_evidence(entry: Dict[str, Any] | None, target_issue: str) -> PromoEvidence:
    if entry is None:
        return PromoEvidence(
            present=False,
            source_id=None,
            parser_version=None,
            sha256=None,
            active_for_target_issue=False,
            start_issue=None,
            end_issue=None,
            min_single_slip_rmb=0,
            allowed_bet_types=frozenset(),
            second_prize_special_active=False,
            sixth_prize_double_active=False,
            remaining_sixth_promo_rmb=0.0,
        )

    spec, raw_bytes, actual_sha = _load_and_verify_raw_evidence(
        entry,
        forbidden_caller_keys=frozenset(
            {"active", "sixth_prize_double_active", "second_prize_special_active", "parsed_promo"}
        ),
    )
    if entry.get("parser_version") != spec.promo_parser_version:
        raise GateBlockedError(
            "PROMO_PARSER_VERSION_MISMATCH",
            f"Expected {spec.promo_parser_version}, got {entry.get('parser_version')}",
        )
    parsed = _parse_promo_raw(raw_bytes, target_issue)
    return PromoEvidence(
        present=True,
        source_id=spec.source_id,
        parser_version=spec.promo_parser_version,
        sha256=actual_sha,
        active_for_target_issue=bool(parsed["active"]),
        start_issue=parsed["start_issue"],
        end_issue=parsed["end_issue"],
        min_single_slip_rmb=int(parsed["min_single_slip_rmb"]),
        allowed_bet_types=frozenset(parsed["allowed_bet_types"]),
        second_prize_special_active=bool(parsed["second_prize_special_active"]),
        sixth_prize_double_active=bool(parsed["sixth_prize_double_active"]),
        remaining_sixth_promo_rmb=float(parsed["remaining_sixth_promo_rmb"]),
    )

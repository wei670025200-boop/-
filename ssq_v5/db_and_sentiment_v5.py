"""
db_and_sentiment_v5.py
Phase 2:
1. 只读历史数据库适配器（直连现有开奖库，校验全6奖级与销量字段）
2. 确定性日历脉冲派生（公历月/日 + 预冻结节假日数字表，零外部状态）
3. 开奖日 18:30-19:30 固定白名单舆情证据绑定（Raw Bytes -> 受信任解析器 -> SHA-256）
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Sequence, Set, Tuple
from urllib.parse import urlparse

BJT = timezone(timedelta(hours=8))


class V5GateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ==============================================================================
# 1. 现有历史库只读适配器（不改原表结构，只做强校验读取）
# ==============================================================================

@dataclass(frozen=True)
class HistoricalDrawRecord:
    issue: str
    draw_date_bjt: str
    reds: Tuple[int, int, int, int, int, int]
    blue: int
    sales_rmb: float
    pool_after_rmb: float
    tier1_count: int
    tier2_count: int
    tier3_count: int
    tier4_count: int
    tier5_count: int
    tier6_count: int
    promo_sixth_double: bool = False

    @property
    def sales_tickets(self) -> float:
        return self.sales_rmb / 2.0


def validate_draw_records(rows: Iterable[Dict[str, Any]]) -> List[HistoricalDrawRecord]:
    """校验并规范化来自任意现有数据库（SQLite/Postgres/JSONL）的全6奖级记录。"""
    records: List[HistoricalDrawRecord] = []
    seen_issues: Set[str] = set()

    for idx, r in enumerate(rows):
        issue = str(r["issue"])
        if len(issue) != 7 or not issue.isdigit():
            raise V5GateError("DB_INVALID_ISSUE", f"Row {idx}: bad issue {issue!r}")
        if issue in seen_issues:
            raise V5GateError("DB_DUPLICATE_ISSUE", f"Duplicate issue {issue}")
        seen_issues.add(issue)

        reds_raw = r["reds"]
        if isinstance(reds_raw, str):
            reds = tuple(sorted(int(x) for x in reds_raw.replace(",", " ").split()))
        else:
            reds = tuple(sorted(int(x) for x in reds_raw))
        if len(reds) != 6 or len(set(reds)) != 6 or reds[0] < 1 or reds[-1] > 33:
            raise V5GateError("DB_INVALID_REDS", f"Issue {issue}: bad reds {reds}")

        blue = int(r["blue"])
        if not (1 <= blue <= 16):
            raise V5GateError("DB_INVALID_BLUE", f"Issue {issue}: bad blue {blue}")

        sales = float(r["sales_rmb"])
        if sales <= 0:
            raise V5GateError("DB_INVALID_SALES", f"Issue {issue}: sales <= 0")

        # 核心强制项：V5 校准必须具备完整的 4/5/6 等奖注数
        counts = []
        for k in ("tier1_count", "tier2_count", "tier3_count", "tier4_count", "tier5_count", "tier6_count"):
            if r.get(k) is None:
                raise V5GateError("DB_MISSING_TIER_COUNT", f"Issue {issue}: missing {k}")
            val = int(r[k])
            if val < 0:
                raise V5GateError("DB_NEGATIVE_TIER_COUNT", f"Issue {issue}: {k}={val}")
            counts.append(val)

        if counts[5] == 0:
            raise V5GateError("DB_ZERO_TIER6_ANOMALY", f"Issue {issue}: tier6_count == 0")

        records.append(
            HistoricalDrawRecord(
                issue=issue,
                draw_date_bjt=str(r["draw_date_bjt"]),
                reds=reds,  # type: ignore[arg-type]
                blue=blue,
                sales_rmb=sales,
                pool_after_rmb=float(r["pool_after_rmb"]),
                tier1_count=counts[0],
                tier2_count=counts[1],
                tier3_count=counts[2],
                tier4_count=counts[3],
                tier5_count=counts[4],
                tier6_count=counts[5],
                promo_sixth_double=bool(r.get("promo_sixth_double", False)),
            )
        )

    records.sort(key=lambda x: x.issue)
    return records


def load_from_sqlite_readonly(
    db_path: Path,
    table_name: str = "ssq_draws",
    column_map: Dict[str, str] | None = None,
) -> List[HistoricalDrawRecord]:
    """以只读 URI 模式连接现有 SQLite 数据库并映射字段（可按实际表列名传 column_map）。"""
    cmap = {
        "issue": "issue",
        "draw_date_bjt": "draw_date_bjt",
        "reds": "reds",
        "blue": "blue",
        "sales_rmb": "sales_rmb",
        "pool_after_rmb": "pool_after_rmb",
        "tier1_count": "tier1_count",
        "tier2_count": "tier2_count",
        "tier3_count": "tier3_count",
        "tier4_count": "tier4_count",
        "tier5_count": "tier5_count",
        "tier6_count": "tier6_count",
        "promo_sixth_double": "promo_sixth_double",
    }
    if column_map:
        cmap.update(column_map)

    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols_sql = ", ".join(f"{src} AS {dst}" for dst, src in cmap.items())
        rows = conn.execute(f"SELECT {cols_sql} FROM {table_name} ORDER BY {cmap['issue']} ASC").fetchall()
        return validate_draw_records([dict(row) for row in rows])
    finally:
        conn.close()


# ==============================================================================
# 2. 确定性日历与节假日脉冲提取（不依赖外部 API）
# ==============================================================================

# 固定公历节假日脉冲数字表（月, 日 -> 关联大众手选热门红球集合）
SOLAR_HOLIDAY_PULSES: Dict[Tuple[int, int], Tuple[int, ...]] = {
    (1, 1): (1, 10, 11),
    (2, 14): (2, 14, 20, 21),
    (5, 1): (5, 1, 15),
    (5, 20): (5, 20, 13, 14),
    (6, 1): (6, 1, 16),
    (8, 8): (8, 18, 28),
    (10, 1): (10, 1, 17),
    (11, 11): (11, 1, 22),
    (12, 25): (12, 25),
}


def extract_calendar_pulse_numbers(
    draw_date_bjt: str,
    lunar_day_override: Tuple[int, int] | None = None,
) -> FrozenSet[int]:
    """提取开奖当日公历月/日、节假日及可选农历月/日对应的红球脉冲集合（1..33）。"""
    d = date.fromisoformat(draw_date_bjt)
    nums: Set[int] = {d.month, d.day}
    if (d.month, d.day) in SOLAR_HOLIDAY_PULSES:
        nums.update(SOLAR_HOLIDAY_PULSES[(d.month, d.day)])
    if lunar_day_override is not None:
        lm, ld = lunar_day_override
        if 1 <= lm <= 12:
            nums.add(lm)
        if 1 <= ld <= 30:
            nums.add(ld)
    return frozenset(n for n in nums if 1 <= n <= 33)


# ==============================================================================
# 3. 18:30-19:30 冻结白名单舆情证据采集与聚合器
# ==============================================================================

@dataclass(frozen=True)
class SentimentChannelSpec:
    channel_id: str
    provider_group: str      # 用于防同一上游多马甲重复计权
    allowed_domain: str
    parser_version: str


# 冻结白名单（实际部署时替换为经备案的固定公开渠道域名）
SENTIMENT_CHANNEL_REGISTRY: Dict[str, SentimentChannelSpec] = {
    "PORTAL_A": SentimentChannelSpec("PORTAL_A", "GROUP_ALPHA", "www.zhcw.com", "sent_json_v1"),
    "PORTAL_B": SentimentChannelSpec("PORTAL_B", "GROUP_BETA", "www.cjcp.com.cn", "sent_json_v1"),
    "PORTAL_C": SentimentChannelSpec("PORTAL_C", "GROUP_GAMMA", "www.500.com", "sent_json_v1"),
    "PORTAL_D": SentimentChannelSpec("PORTAL_D", "GROUP_DELTA", "sports.sina.com.cn", "sent_json_v1"),
}


def _parse_standard_sentiment_json(raw_bytes: bytes) -> Dict[str, Any]:
    doc = json.loads(raw_bytes.decode("utf-8"))
    hot_reds = sorted({int(x) for x in doc.get("hot_reds", []) if 1 <= int(x) <= 33})
    kill_reds = sorted({int(x) for x in doc.get("kill_reds", []) if 1 <= int(x) <= 33})
    hot_blues = sorted({int(x) for x in doc.get("hot_blues", []) if 1 <= int(x) <= 16})
    kill_blues = sorted({int(x) for x in doc.get("kill_blues", []) if 1 <= int(x) <= 16})
    double_dan_pairs = []
    for p in doc.get("double_dan_pairs", []):
        if isinstance(p, list) and len(p) == 2:
            a, b = sorted((int(p[0]), int(p[1])))
            if 1 <= a < b <= 33:
                double_dan_pairs.append((a, b))
    return {
        "target_issue": str(doc["target_issue"]),
        "hot_reds": hot_reds,
        "kill_reds": kill_reds,
        "hot_blues": hot_blues,
        "kill_blues": kill_blues,
        "double_dan_pairs": sorted(set(double_dan_pairs)),
    }


SENTIMENT_PARSERS: Dict[str, Callable[[bytes], Dict[str, Any]]] = {
    "sent_json_v1": _parse_standard_sentiment_json,
}


@dataclass(frozen=True)
class VerifiedSentimentSnapshot:
    target_issue: str
    status: str  # "VERIFIED" | "DEGRADED_EMPTY"
    agreed_channel_ids: Tuple[str, ...]
    rejected_channels: Dict[str, str]
    channel_hashes: Dict[str, str]
    snapshot_sha256: str
    consensus_hot_reds: FrozenSet[int]
    consensus_kill_reds: FrozenSet[int]
    consensus_hot_blues: FrozenSet[int]
    consensus_kill_blues: FrozenSet[int]
    consensus_double_dan_pairs: FrozenSet[Tuple[int, int]]


def build_verified_sentiment_snapshot(
    entries: Sequence[Dict[str, Any]],
    target_issue: str,
    draw_date_bjt: str,
    min_independent_groups: int = 2,
    consensus_ratio: float = 0.5,
    enforce_collection_window: bool = True,
) -> VerifiedSentimentSnapshot:
    """
    严格绑定原始舆情快照：
    - 禁止调用方传入预解析字段
    - 校验冻结白名单域名、解析器版本与原始字节 SHA-256
    - 校验采集时间位于开奖当日北京时间 18:30-19:30（可配置关闭用于历史回放）
    - 按 provider_group 去重统计独立渠道共识热胆与共识绝杀
    """
    seen_ids: Set[str] = set()
    seen_groups: Set[str] = set()
    rejected: Dict[str, str] = {}
    channel_hashes: Dict[str, str] = {}
    valid_payloads: List[Tuple[str, Dict[str, Any]]] = []

    win_start = datetime.fromisoformat(f"{draw_date_bjt}T18:30:00+08:00")
    win_end = datetime.fromisoformat(f"{draw_date_bjt}T19:30:00+08:00")

    for entry in entries:
        for bad in ("hot_reds", "kill_reds", "hot_blues", "kill_blues", "parsed_sentiment"):
            if bad in entry:
                raise V5GateError(
                    "SENTIMENT_CALLER_PARSED_FORBIDDEN",
                    f"Caller cannot supply pre-parsed sentiment key {bad!r}.",
                )

        cid = str(entry.get("channel_id", "")).strip()
        if cid in seen_ids:
            raise V5GateError("DUPLICATE_SENTIMENT_CHANNEL", f"Duplicate channel_id: {cid}")
        seen_ids.add(cid)

        spec = SENTIMENT_CHANNEL_REGISTRY.get(cid)
        if spec is None:
            raise V5GateError("UNREGISTERED_SENTIMENT_CHANNEL", f"Unknown channel_id: {cid}")

        if entry.get("parser_version") != spec.parser_version:
            rejected[cid] = "PARSER_VERSION_MISMATCH"
            continue

        parsed_url = urlparse(str(entry.get("url", "")))
        if parsed_url.scheme != "https" or (parsed_url.hostname or "").lower() != spec.allowed_domain:
            rejected[cid] = "DOMAIN_MISMATCH"
            continue

        try:
            fetched_dt = datetime.fromisoformat(str(entry["fetched_at_utc"]).replace("Z", "+00:00"))
            if fetched_dt.tzinfo is None:
                raise ValueError("naive")
            fetched_bjt = fetched_dt.astimezone(BJT)
            if enforce_collection_window and not (win_start <= fetched_bjt <= win_end):
                rejected[cid] = "OUTSIDE_1830_1930_WINDOW"
                continue
        except Exception:
            rejected[cid] = "INVALID_FETCHED_AT"
            continue

        raw_path = Path(str(entry.get("path", "")))
        if not raw_path.is_file():
            rejected[cid] = "RAW_FILE_MISSING"
            continue
        raw_bytes = raw_path.read_bytes()
        actual_sha = sha256_bytes(raw_bytes)
        if actual_sha != str(entry.get("sha256", "")).lower():
            rejected[cid] = "RAW_SHA256_MISMATCH"
            continue

        try:
            parsed = SENTIMENT_PARSERS[spec.parser_version](raw_bytes)
        except Exception as exc:
            rejected[cid] = f"PARSE_ERROR:{type(exc).__name__}"
            continue

        if parsed["target_issue"] != target_issue:
            rejected[cid] = "TARGET_ISSUE_MISMATCH"
            continue

        if spec.provider_group in seen_groups:
            rejected[cid] = "DUPLICATE_PROVIDER_GROUP"
            continue

        seen_groups.add(spec.provider_group)
        channel_hashes[cid] = actual_sha
        valid_payloads.append((cid, parsed))

    if len(valid_payloads) < min_independent_groups:
        empty_body = json.dumps(
            {"target_issue": target_issue, "status": "DEGRADED_EMPTY", "rejected": rejected},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return VerifiedSentimentSnapshot(
            target_issue=target_issue,
            status="DEGRADED_EMPTY",
            agreed_channel_ids=tuple(),
            rejected_channels=rejected,
            channel_hashes=channel_hashes,
            snapshot_sha256=sha256_bytes(empty_body),
            consensus_hot_reds=frozenset(),
            consensus_kill_reds=frozenset(),
            consensus_hot_blues=frozenset(),
            consensus_kill_blues=frozenset(),
            consensus_double_dan_pairs=frozenset(),
        )

    k = len(valid_payloads)
    min_votes = max(2, int(round(k * consensus_ratio + 1e-9)))

    hot_r = Counter(n for _, p in valid_payloads for n in p["hot_reds"])
    kill_r = Counter(n for _, p in valid_payloads for n in p["kill_reds"])
    hot_b = Counter(n for _, p in valid_payloads for n in p["hot_blues"])
    kill_b = Counter(n for _, p in valid_payloads for n in p["kill_blues"])
    pairs = Counter(pair for _, p in valid_payloads for pair in p["double_dan_pairs"])

    # 冲突裁决：同一号码同时进入热胆与绝杀共识时相互抵消，不强行偏置
    c_hot_r = {n for n, v in hot_r.items() if v >= min_votes}
    c_kill_r = {n for n, v in kill_r.items() if v >= min_votes}
    overlap_r = c_hot_r & c_kill_r
    c_hot_r -= overlap_r
    c_kill_r -= overlap_r

    c_hot_b = {n for n, v in hot_b.items() if v >= min_votes}
    c_kill_b = {n for n, v in kill_b.items() if v >= min_votes}
    overlap_b = c_hot_b & c_kill_b
    c_hot_b -= overlap_b
    c_kill_b -= overlap_b

    c_pairs = {pair for pair, v in pairs.items() if v >= min_votes}

    canonical_body = json.dumps(
        {
            "target_issue": target_issue,
            "channel_hashes": channel_hashes,
            "consensus_hot_reds": sorted(c_hot_r),
            "consensus_kill_reds": sorted(c_kill_r),
            "consensus_hot_blues": sorted(c_hot_b),
            "consensus_kill_blues": sorted(c_kill_b),
            "consensus_double_dan_pairs": [list(p) for p in sorted(c_pairs)],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return VerifiedSentimentSnapshot(
        target_issue=target_issue,
        status="VERIFIED",
        agreed_channel_ids=tuple(cid for cid, _ in valid_payloads),
        rejected_channels=rejected,
        channel_hashes=channel_hashes,
        snapshot_sha256=sha256_bytes(canonical_body),
        consensus_hot_reds=frozenset(c_hot_r),
        consensus_kill_reds=frozenset(c_kill_r),
        consensus_hot_blues=frozenset(c_hot_b),
        consensus_kill_blues=frozenset(c_kill_b),
        consensus_double_dan_pairs=frozenset(c_pairs),
    )


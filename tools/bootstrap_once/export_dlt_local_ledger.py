"""One-shot DLT ledger export from the approved 17500 plain-text archive.

The latest 780 source rows are scanned completely before any export decision.
A single unused fixed-award cell typo may be tolerated only after independent
proof that the row's ball columns, tier counts, epoch and monetary bounds are
intact. No source retry, no alternate source, no imputation, no WAF bypass.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import date as Date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "dlt_history_contract_once", ROOT / "src/dlt_v5/history_contract.py"
)
history = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(history)

SOURCE_URL = "https://www.17500.cn/getData/dlt.TXT"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/plain,*/*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
TARGET = 780
HARD = 650
CUT_ISSUE = 26014
CUT_DATE = Date(2026, 2, 2)
LATEST_REQUIRED = "26134"
KEY_ISSUES = ("25100", "26013", "26014", "26015", "26134")
OLD_FIXED = (10000, 3000, 300, 200, 100, 15, 5)
NEW_FIXED = ((5000, 300, 150, 15, 5), (6666, 380, 200, 18, 7))
TIERS = "一二三四五六七八九"


def fetch_once():
    try:
        with urlopen(Request(SOURCE_URL, headers=HEADERS), timeout=30) as response:
            raw = response.read()
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raw = exc.read(1200)
        print(
            f"SOURCE_HTTP status={exc.code} bytes={len(raw)} "
            f"body_prefix={raw[:240]!r}",
            flush=True,
        )
        raise RuntimeError(f"SOURCE_HTTP_FAIL:{exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        print(
            f"SOURCE_HTTP status=UNAVAILABLE error={type(exc).__name__}:{exc}",
            flush=True,
        )
        raise RuntimeError("SOURCE_HTTP_FAIL:UNAVAILABLE") from exc

    challenge = any(
        marker.lower() in raw.lower()
        for marker in (
            b"EO_Bot_Ssid",
            b"__jsl_clearance",
            b"captcha",
            b"cf-chl-",
            b"document.cookie",
        )
    )
    print(
        f"SOURCE_HTTP status={status} bytes={len(raw)} "
        f"content_type={content_type!r} challenge={str(challenge).lower()}",
        flush=True,
    )
    if status != 200 or challenge:
        raise RuntimeError(f"SOURCE_INVALID:status={status}:challenge={challenge}")
    return raw


def decode(raw):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError("SOURCE_DECODE_FAILED")


def integer(value, label, issue):
    if not re.fullmatch(r"\d+", str(value)):
        raise ValueError(f"INVALID_INTEGER:{issue}:{label}:{value}")
    return int(value)


def parse_source(raw):
    text = decode(raw)
    records = []
    unkeyed = []
    seen = defaultdict(list)
    total_lines = len(text.splitlines())
    nonempty = 0

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip():
            continue
        nonempty += 1
        columns = raw_line.strip().split()
        issue = columns[0] if columns else ""
        if not re.fullmatch(r"\d{5}", issue):
            unkeyed.append(
                {
                    "line_number": line_number,
                    "issue_token": issue,
                    "error": "UNKEYED_SOURCE_ROW",
                    "raw_line": raw_line,
                }
            )
            continue

        pre_errors = []
        if len(columns) != 45:
            pre_errors.append(f"COLUMN_COUNT:{len(columns)}!=45")
        draw_date = ""
        if len(columns) < 2:
            pre_errors.append("DATE_COLUMN_MISSING")
        else:
            try:
                draw_date = Date.fromisoformat(columns[1]).isoformat()
            except ValueError:
                pre_errors.append(f"DATE_FORMAT:{columns[1]}")
        if draw_date and issue[:2] != str(Date.fromisoformat(draw_date).year)[-2:]:
            pre_errors.append(f"ISSUE_DATE_YEAR:{issue}:{draw_date}")

        record = {
            "issue": issue,
            "date": draw_date,
            "c": columns,
            "raw_line": raw_line,
            "line_number": line_number,
            "pre_errors": pre_errors,
        }
        records.append(record)
        seen[issue].append(record)

    for issue, duplicates in seen.items():
        if len(duplicates) > 1:
            for record in duplicates:
                record["pre_errors"].append(
                    f"DUPLICATE_ISSUE:{issue}:occurrences={len(duplicates)}"
                )

    print(
        f"SOURCE_PARSE total_lines={total_lines} nonempty_lines={nonempty} "
        f"keyed_rows={len(records)} unkeyed_rows={len(unkeyed)}",
        flush=True,
    )
    for item in unkeyed:
        print("UNKEYED_SOURCE_ANOMALY " + json.dumps(item, ensure_ascii=False), flush=True)
    return records, {
        "total_lines": total_lines,
        "nonempty_lines": nonempty,
        "keyed_rows": len(records),
        "unkeyed_rows": len(unkeyed),
    }, unkeyed


def alignment_items(record):
    columns = record["c"]
    issue = record["issue"]
    if len(columns) < 40:
        return []
    output = []
    for tier, count_index, base_index, add_award_index in (
        ("g1_add", 36, 19, 37),
        ("g2_add", 38, 21, 39),
    ):
        try:
            add_count = integer(columns[count_index], f"c[{count_index}]", issue)
        except ValueError:
            continue
        if add_count <= 0:
            continue
        try:
            base_award = integer(columns[base_index], f"c[{base_index}]", issue)
            add_award = integer(columns[add_award_index], f"c[{add_award_index}]", issue)
            expected = round(base_award * 0.8)
            difference = abs(add_award - expected)
            output.append(
                {
                    "issue": issue,
                    "tier": tier,
                    "base_award": base_award,
                    "add_award": add_award,
                    "expected_80pct": expected,
                    "abs_diff": difference,
                    "match": difference <= 2,
                }
            )
        except ValueError as exc:
            output.append(
                {
                    "issue": issue,
                    "tier": tier,
                    "match": False,
                    "error": str(exc),
                }
            )
    return output


def fixed_award_audit(numbers, is_new, issue):
    if not is_new:
        specs = (
            (23, 22, 10000, "t3"),
            (25, 24, 3000, "t4"),
            (27, 26, 300, "t5"),
            (29, 28, 200, "t6"),
            (31, 30, 100, "t7"),
            (33, 32, 15, "t8"),
            (35, 34, 5, "t9"),
        )
        mismatches = []
        for award_index, count_index, fixed, label in specs:
            expected = fixed if numbers[count_index] > 0 or label in ("t8", "t9") else 0
            actual = numbers[award_index]
            if actual != expected:
                mismatches.append(
                    {
                        "column": f"c[{award_index}]",
                        "tier": label,
                        "actual": actual,
                        "expected": expected,
                    }
                )
        fingerprint_ok = (
            len(mismatches) <= 1
            and numbers[33] == 15
            and numbers[35] == 5
            and numbers[34] > 0
        )
        return mismatches, fingerprint_ok, tuple(numbers[i] for i in (23, 25, 27, 29, 31, 33, 35))

    if tuple(numbers[i] for i in range(32, 36)) != (0, 0, 0, 0):
        return (
            [
                {
                    "column": "c[32..35]",
                    "tier": "7TIER_TAIL",
                    "actual": tuple(numbers[i] for i in range(32, 36)),
                    "expected": (0, 0, 0, 0),
                }
            ],
            False,
            tuple(numbers[i] for i in (23, 25, 27, 29, 31)),
        )

    candidates = []
    for fixed in NEW_FIXED:
        specs = (
            (23, 22, fixed[0], "t3"),
            (25, 24, fixed[1], "t4"),
            (27, 26, fixed[2], "t5"),
            (29, 28, fixed[3], "t6"),
            (31, 30, fixed[4], "t7"),
        )
        mismatches = []
        for award_index, count_index, amount, label in specs:
            expected = amount if numbers[count_index] > 0 else 0
            actual = numbers[award_index]
            if actual != expected:
                mismatches.append(
                    {
                        "column": f"c[{award_index}]",
                        "tier": label,
                        "actual": actual,
                        "expected": expected,
                    }
                )
        g6_g7_exact = numbers[29] == fixed[3] and numbers[31] == fixed[4]
        candidates.append((not g6_g7_exact, len(mismatches), mismatches, fixed, g6_g7_exact))

    candidates.sort(key=lambda item: (item[0], item[1], item[3]))
    _, _, mismatches, fixed, g6_g7_exact = candidates[0]
    fingerprint_ok = (
        g6_g7_exact
        and len(mismatches) <= 1
        and 5 - len(mismatches) >= 4
    )
    return mismatches, fingerprint_ok, tuple(numbers[i] for i in (23, 25, 27, 29, 31))


def scan_record(record):
    issue = record["issue"]
    columns = record["c"]
    errors = list(record["pre_errors"])
    debug = {"issue": issue, "add_alignment": alignment_items(record)}

    if len(columns) != 45:
        return {
            "classification": "HARD_CORRUPT",
            "errors": errors or [f"COLUMN_COUNT:{len(columns)}!=45"],
            "row": None,
            "debug": debug,
            "award_mismatches": [],
        }

    try:
        draw_date = Date.fromisoformat(columns[1]).isoformat()
        numbers = {
            index: integer(columns[index], f"c[{index}]", issue)
            for index in range(2, 40)
        }
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
        return {
            "classification": "HARD_CORRUPT",
            "errors": errors,
            "row": None,
            "debug": debug,
            "award_mismatches": [],
        }

    def n(index):
        return numbers[index]

    if tuple(sorted(n(i) for i in range(2, 7))) != tuple(sorted(n(i) for i in range(9, 14))):
        errors.append("FRONT_REDUNDANCY")
    if tuple(sorted(n(i) for i in range(7, 9))) != tuple(sorted(n(i) for i in range(14, 16))):
        errors.append("BACK_REDUNDANCY")

    front = tuple(n(i) for i in range(2, 7))
    back = tuple(n(i) for i in range(7, 9))
    if front != tuple(sorted(front)) or len(set(front)) != 5 or not all(1 <= x <= 35 for x in front):
        errors.append("FRONT_CANONICAL")
    if back != tuple(sorted(back)) or len(set(back)) != 2 or not all(1 <= x <= 12 for x in back):
        errors.append("BACK_CANONICAL")

    if issue[:2] != str(Date.fromisoformat(draw_date).year)[-2:]:
        errors.append("ISSUE_DATE_YEAR")
    is_new = int(issue) >= CUT_ISSUE
    if (Date.fromisoformat(draw_date) >= CUT_DATE) != is_new:
        errors.append("RULE_EPOCH_CONFLICT")

    sales = n(16)
    pool = n(17)
    if not 100_000_000 <= sales <= 1_000_000_000:
        errors.append(f"SALES_RANGE:{sales}")
    if pool < 0:
        errors.append("POOL_NEGATIVE")

    g1, g1_add, g2, g2_add = n(18), n(36), n(20), n(38)
    if g1_add > g1:
        errors.append(f"G1_ADD_GT_G1:{g1_add}>{g1}")
    if g2_add > g2:
        errors.append(f"G2_ADD_GT_G2:{g2_add}>{g2}")

    if is_new:
        tier_counts = {
            "g3": n(22),
            "g4": n(24),
            "g5": n(26),
            "g6": n(28),
            "g7": n(30),
        }
        tier_indices = (22, 24, 26, 28, 30)
        epoch = "7TIER_2026"
    else:
        tier_counts = {
            "g3": n(22) + n(24),
            "g4": n(26),
            "g5": n(28) + n(30),
            "g6": n(32),
            "g7": n(34),
        }
        tier_indices = (22, 24, 26, 28, 30, 32, 34)
        epoch = "9TIER_2019"

    ordered_counts = [tier_counts[key] for key in ("g3", "g4", "g5", "g6", "g7")]
    if not (
        tier_counts["g7"] > tier_counts["g6"] > tier_counts["g5"]
        > tier_counts["g4"] > tier_counts["g3"] > 0
    ):
        errors.append(f"TIER_COUNT_ORDER:{tier_counts}")
    if tier_counts["g7"] < 1_000_000:
        errors.append(f"G7_SCALE:{tier_counts['g7']}<1000000")
    if tier_counts["g6"] < 100_000:
        errors.append(f"G6_SCALE:{tier_counts['g6']}<100000")

    prize_rows = [
        ("一等奖", str(g1)),
        ("一等奖追加", str(g1_add)),
        ("二等奖", str(g2)),
        ("二等奖追加", str(g2_add)),
    ]
    for tier_number, index in enumerate(tier_indices, 3):
        prize_rows.append((f"{TIERS[tier_number - 1]}等奖", str(n(index))))

    try:
        normalized = history.normalize_prize_rows(issue, draw_date, prize_rows)
        if normalized["rule_epoch"] != epoch:
            errors.append(
                f"RULE_EPOCH_CONFLICT:{normalized['rule_epoch']}!={epoch}"
            )
    except Exception as exc:
        normalized = None
        errors.append(f"HISTORY_CONTRACT:{type(exc).__name__}:{exc}")

    award_mismatches, fingerprint_ok, fixed_awards = fixed_award_audit(
        numbers, is_new, issue
    )
    if award_mismatches:
        debug["fixed_award_mismatches"] = award_mismatches
    debug.update(
        {
            "epoch": epoch,
            "base_awards": {"g1": n(19), "g2": n(21)},
            "add": {
                "g1_count": g1_add,
                "g1_award": n(37),
                "g2_count": g2_add,
                "g2_award": n(39),
            },
            "fixed_awards": fixed_awards,
            "frozen_counts": tier_counts,
            "fixed_fingerprint_ok": fingerprint_ok,
        }
    )

    if normalized is None:
        row = None
    else:
        normalized["promo_active"] = "0"
        for key, value in tier_counts.items():
            if int(normalized[key]) != value:
                errors.append(f"FROZEN_MAPPING_MISMATCH:{key}:{normalized[key]}!={value}")
        if int(normalized["g7"]) <= 0:
            errors.append("G7_NOT_POSITIVE")
        row = {
            "issue": issue,
            "date": draw_date,
            **{f"f{i + 1}": str(value) for i, value in enumerate(front)},
            **{f"b{i + 1}": str(value) for i, value in enumerate(back)},
            "sales_rmb": str(sales),
            "pool_after_rmb": str(pool),
            **normalized,
        }
        row = {key: row[key] for key in history.HISTORY_FIELDS}

    if not fingerprint_ok:
        errors.append(
            f"FIXED_AWARD_FINGERPRINT_INVALID:{json.dumps(award_mismatches, ensure_ascii=False)}"
        )
    if len(award_mismatches) > 1:
        errors.append(f"FIXED_AWARD_MISMATCH_COUNT:{len(award_mismatches)}>1")

    if errors or row is None:
        classification = "HARD_CORRUPT"
    elif len(award_mismatches) == 1:
        classification = "AWARD_TYPO_ONLY"
    elif len(award_mismatches) == 0:
        classification = "CLEAN"
    else:
        classification = "HARD_CORRUPT"

    return {
        "classification": classification,
        "errors": errors,
        "row": row if classification in ("CLEAN", "AWARD_TYPO_ONLY") else None,
        "debug": debug,
        "award_mismatches": award_mismatches,
    }


def audit_latest(records, source_meta, unkeyed):
    records_desc = sorted(records, key=lambda item: (int(item["issue"]), item["line_number"]), reverse=True)
    selected_desc = records_desc[:TARGET]
    selected = list(reversed(selected_desc))
    fatal = []

    if len(selected) < HARD:
        fatal.append(f"DATA_GATE_INSUFFICIENT_650:{len(selected)}")
    if selected and selected[-1]["issue"] != LATEST_REQUIRED:
        fatal.append(
            f"LATEST_ISSUE_MISMATCH:{selected[-1]['issue']}!={LATEST_REQUIRED}"
        )
    selected_issues = {item["issue"] for item in selected}
    for key in KEY_ISSUES:
        if key not in selected_issues:
            fatal.append(f"KEY_ISSUE_MISSING_OR_OUTSIDE_WINDOW:{key}")
    if unkeyed:
        fatal.append(f"UNKEYED_SOURCE_ROWS:{len(unkeyed)}")

    extension_nonzero = []
    for record in selected:
        columns = record["c"]
        if len(columns) >= 45:
            tail = columns[40:45]
            try:
                nonzero = any(integer(value, f"c[{i + 40}]", record["issue"]) != 0 for i, value in enumerate(tail))
            except ValueError:
                nonzero = True
            if nonzero:
                extension_nonzero.append(
                    {
                        "issue": record["issue"],
                        "line_number": record["line_number"],
                        "tail_c40_c44": tail,
                        "raw_line": record["raw_line"],
                    }
                )
    print(
        f"EXTENSION_AUDIT scanned_rows={len(selected)} "
        f"nonzero_rows={len(extension_nonzero)} promo_active=0 "
        "promo_flag_source=UNAVAILABLE_IN_17500_TXT_SET_0",
        flush=True,
    )
    for item in extension_nonzero:
        print("EXTENSION_NONZERO_RECORD " + json.dumps(item, ensure_ascii=False), flush=True)

    scanned = [scan_record(record) for record in selected]
    class_counts = Counter(item["classification"] for item in scanned)
    anomalies = []
    for record, result in zip(selected, scanned):
        if result["classification"] != "CLEAN":
            anomaly = {
                "issue": record["issue"],
                "line_number": record["line_number"],
                "classification": result["classification"],
                "errors": result["errors"],
                "award_mismatches": result["award_mismatches"],
                "raw_line": record["raw_line"],
            }
            anomalies.append(anomaly)
            print(
                "ROW_ANOMALY "
                + json.dumps(anomaly, ensure_ascii=False, sort_keys=True),
                flush=True,
            )

    alignment = [
        item
        for record in selected
        for item in alignment_items(record)
    ]
    alignment_total = len(alignment)
    alignment_matched = sum(bool(item.get("match")) for item in alignment)
    alignment_rate = alignment_matched / alignment_total if alignment_total else 0.0
    alignment_anomalies = [item for item in alignment if not item.get("match")]
    print(
        f"ADD_80PCT_ALIGNMENT matched={alignment_matched} total={alignment_total} "
        f"rate={alignment_rate:.9f} threshold=0.95 "
        f"anomalies={len(alignment_anomalies)}",
        flush=True,
    )
    for item in alignment_anomalies:
        print("ADD_80PCT_ANOMALY " + json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)
    if not alignment_total or alignment_matched * 100 < alignment_total * 95:
        fatal.append(
            f"ADD_80PCT_ALIGNMENT_BELOW_95PCT:{alignment_matched}/{alignment_total}"
        )

    hard_indices = [
        index for index, item in enumerate(scanned)
        if item["classification"] == "HARD_CORRUPT"
    ]
    typo_count = class_counts["AWARD_TYPO_ONLY"]
    if not hard_indices and typo_count <= 5:
        export_indices = list(range(len(scanned)))
        decision = "A_FULL_WINDOW"
        truncation = None
    else:
        suffix_reversed = []
        suffix_typos = 0
        for index in range(len(scanned) - 1, -1, -1):
            classification = scanned[index]["classification"]
            if classification == "HARD_CORRUPT":
                break
            if classification == "AWARD_TYPO_ONLY":
                if suffix_typos >= 5:
                    break
                suffix_typos += 1
            suffix_reversed.append(index)
        export_indices = list(reversed(suffix_reversed))
        decision = "B_LONGEST_CLEAN_SUFFIX"
        truncation = {
            "reason": (
                "EARLIER_HARD_CORRUPT_OR_AWARD_TYPO_ONLY_OVER_5"
            ),
            "scanned_start_issue": selected[0]["issue"] if selected else None,
            "scanned_end_issue": selected[-1]["issue"] if selected else None,
            "hard_corrupt_rows_total": class_counts["HARD_CORRUPT"],
            "award_typo_only_rows_total": typo_count,
            "suffix_start_issue": selected[export_indices[0]]["issue"] if export_indices else None,
            "suffix_end_issue": selected[export_indices[-1]]["issue"] if export_indices else None,
            "suffix_rows": len(export_indices),
            "suffix_award_typo_only_rows": suffix_typos,
        }
        if len(export_indices) < HARD:
            fatal.append(
                f"CLEAN_SUFFIX_BELOW_650:{len(export_indices)}"
            )

    export_results = [scanned[index] for index in export_indices]
    export_rows = [item["row"] for item in export_results if item["row"] is not None]
    if len(export_rows) != len(export_indices):
        fatal.append(
            f"EXPORT_ROW_MATERIALIZATION_MISMATCH:{len(export_rows)}!={len(export_indices)}"
        )

    ordered_rows = sorted(export_rows, key=lambda row: int(row["issue"]))
    if ordered_rows:
        medians = {
            tier: statistics.median(int(row[tier]) for row in ordered_rows)
            for tier in ("g3", "g4", "g5", "g6", "g7")
        }
        if not (
            medians["g7"] > medians["g6"] > medians["g5"]
            > medians["g4"] > medians["g3"]
        ):
            fatal.append("PRIZE_MEDIAN_ORDER:" + json.dumps(medians))
        scale_floor = {"g3": 1, "g4": 1, "g5": 1, "g6": 100000, "g7": 1000000}
        if any(medians[key] < floor for key, floor in scale_floor.items()):
            fatal.append("PRIZE_MEDIAN_SCALE:" + json.dumps({"median": medians, "floor": scale_floor}))
        g1_add_positive = sum(int(row["g1_add"]) > 0 for row in ordered_rows)
        g2_add_positive = sum(int(row["g2_add"]) > 0 for row in ordered_rows)
        if g1_add_positive <= 0 or g2_add_positive <= 0:
            fatal.append(f"ADDITIONAL_NONZERO_AUDIT:g1={g1_add_positive}:g2={g2_add_positive}")
    else:
        medians = {}
        g1_add_positive = 0
        g2_add_positive = 0

    key_debug = {}
    for key in KEY_ISSUES:
        matches = [
            (record, result)
            for record, result in zip(selected, scanned)
            if record["issue"] == key
        ]
        if matches:
            record, result = matches[-1]
            key_debug[key] = {
                "classification": result["classification"],
                "errors": result["errors"],
                "debug": result["debug"],
                "canonical": result["row"],
                "raw_line": record["raw_line"],
            }
            print(
                f"KEY_ISSUE issue={key} classification={result['classification']} "
                f"raw_line={record['raw_line']}",
                flush=True,
            )
        else:
            key_debug[key] = {"missing": True}

    if "26015" in key_debug and not key_debug["26015"].get("missing"):
        actual = tuple(key_debug["26015"]["debug"].get("fixed_awards", ()))
        if actual != (6666, 380, 200, 18, 7):
            fatal.append(f"ISSUE_26015_FIXED_AWARD:{actual}")
    else:
        fatal.append("ISSUE_26015_FIXED_AWARD_AUDIT_MISSING")

    report = {
        "source": SOURCE_URL,
        "source_meta": source_meta,
        "scanned_rows": len(selected),
        "scanned_start_issue": selected[0]["issue"] if selected else None,
        "scanned_end_issue": selected[-1]["issue"] if selected else None,
        "scanned_start_date": selected[0]["date"] if selected else None,
        "scanned_end_date": selected[-1]["date"] if selected else None,
        "classification_counts": {
            "CLEAN": class_counts["CLEAN"],
            "AWARD_TYPO_ONLY": class_counts["AWARD_TYPO_ONLY"],
            "HARD_CORRUPT": class_counts["HARD_CORRUPT"],
        },
        "all_anomalies": anomalies,
        "unkeyed_source_rows": unkeyed,
        "decision": decision,
        "truncation": truncation,
        "export_rows": len(ordered_rows),
        "export_start_issue": ordered_rows[0]["issue"] if ordered_rows else None,
        "export_start_date": ordered_rows[0]["date"] if ordered_rows else None,
        "export_end_issue": ordered_rows[-1]["issue"] if ordered_rows else None,
        "export_end_date": ordered_rows[-1]["date"] if ordered_rows else None,
        "buffer_780": "PASS" if len(ordered_rows) >= TARGET else "BELOW_TARGET",
        "hard_gate_650": "PASS" if len(ordered_rows) >= HARD else "FAIL",
        "g1_add_positive_rows": g1_add_positive,
        "g2_add_positive_rows": g2_add_positive,
        "median_counts": medians,
        "extension_nonzero_rows": len(extension_nonzero),
        "extension_nonzero_records": extension_nonzero,
        "promo_flag_source": "UNAVAILABLE_IN_17500_TXT_SET_0",
        "promo_active_all_rows": 0,
        "add_80pct_alignment": {
            "matched": alignment_matched,
            "total": alignment_total,
            "rate": alignment_rate,
            "threshold": 0.95,
            "anomaly_count": len(alignment_anomalies),
            "anomalies": alignment_anomalies,
        },
        "key_issue_audits": key_debug,
        "rule_epoch_conflicts": sum(
            any("RULE_EPOCH_CONFLICT" in error for error in result["errors"])
            for result in scanned
        ),
        "fatal_gates": fatal,
        "data_gate": "PASS" if not fatal else "FAIL",
        "real_oos_executed": False,
    }
    print("FULL_SCAN_SUMMARY " + json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return ordered_rows, report, fatal


def atomic_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(
            handle, fieldnames=history.HISTORY_FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
    try:
        if path.exists():
            raise RuntimeError("HISTORY_ALREADY_EXISTS")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit-out", default="dlt_oos_artifacts/source_selfcheck.json"
    )
    args = parser.parse_args()
    output = ROOT / "data/dlt_history.csv"
    claim = ROOT / "data/dlt_real_oos_once_claim.json"
    if output.exists() or claim.exists():
        raise RuntimeError("BOOTSTRAP_ALREADY_PRESENT")

    raw = fetch_once()
    records, source_meta, unkeyed = parse_source(raw)
    rows, report, fatal = audit_latest(records, source_meta, unkeyed)
    report["source_file_sha256"] = hashlib.sha256(raw).hexdigest()
    audit_path = ROOT / args.audit_out
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    if fatal:
        print(
            "FAIL_CLOSED " + json.dumps({"fatal_gates": fatal, "real_oos_executed": False}, ensure_ascii=False),
            flush=True,
        )
        raise RuntimeError("DLT_EXPORT_AUDIT_FAIL_CLOSED")

    atomic_csv(rows, output)
    csv_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    print(
        f"EXPORT_COMPLETE rows={len(rows)} csv_sha256={csv_sha} "
        f"real_oos_executed=false",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())

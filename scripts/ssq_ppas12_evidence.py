#!/usr/bin/env python3
"""Build a fail-closed PP-AS-1.2 evidence candidate from official raw bytes.

This program only parses, reconciles, hashes and audits evidence.  It contains
no PP-AS-1.2 model fitting code and never freezes a data manifest unless every
gate below passes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


WINDOW_START = "2021121"
WINDOW_END = "2026133"
EXPECTED_FIXED_COUNT = 600
AUDIT_SAMPLE_SIZE = 60
AUDIT_SEED = "PP-AS-1.2-OFFICIAL-SOURCE-AUDIT-v1|20261124"
BASE_PRIZE_LABELS = ("一等奖", "二等奖", "三等奖", "四等奖", "五等奖", "六等奖")
SPECIAL_MARKERS = ("特别奖", "翻番奖", "派奖", "加奖")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def first_int(value: str) -> int | None:
    m = re.search(r"\d[\d,]*", value)
    return int(m.group(0).replace(",", "")) if m else None


def parse_prize_rows(document: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document, flags=re.I | re.S):
        cells = [clean_text(x) for x in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)]
        if not cells:
            continue
        label = re.sub(r"\s+", "", cells[0])
        if not (label in BASE_PRIZE_LABELS or any(marker in label for marker in SPECIAL_MARKERS)):
            continue
        if len(cells) >= 5:
            count_cell = cells[2]
            money_cell = cells[4]
        elif len(cells) >= 3:
            count_cell = cells[1]
            money_cell = cells[2]
        else:
            continue
        rows.append(
            {
                "label": label,
                "national_count": first_int(count_cell),
                "single_prize_rmb": first_int(money_cell),
                "cells": cells,
            }
        )
    return rows


def prize_row(rows: Iterable[dict[str, Any]], label: str) -> dict[str, Any] | None:
    return next((row for row in rows if row["label"] == label), None)


def complete_base_table(rows: Iterable[dict[str, Any]]) -> bool:
    labels = {row["label"] for row in rows}
    return all(label in labels for label in BASE_PRIZE_LABELS)


def special_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["label"] not in BASE_PRIZE_LABELS]


def parse_sales_observation(text: str) -> tuple[int | None, int | None]:
    patterns = (
        r"本期全国销售额\s*([\d,]+)\s*元",
        r"本期销售金额[：:]?\s*([\d,]+)\s*元",
        r"本期全国投注总额\s*([\d.]+)\s*万元",
    )
    for index, pattern in enumerate(patterns):
        m = re.search(pattern, text)
        if not m:
            continue
        if index == 2:
            shown = m.group(1).replace(",", "")
            decimals = len(shown.partition(".")[2])
            return int(float(shown) * 10000), max(1, 10000 // (10**decimals))
        return int(m.group(1).replace(",", "")), 1
    return None, None


def parse_sales_rmb(text: str) -> int | None:
    return parse_sales_observation(text)[0]


def make_record(
    *,
    issue: str,
    date: str | None,
    document: str,
    source_id: str,
    source_url: str,
    raw_path: Path,
    raw_sha256: str,
    source_locator: str,
) -> dict[str, Any]:
    rows = parse_prize_rows(document)
    sixth = prize_row(rows, "六等奖")
    specials = special_rows(rows)
    sales_rmb, sales_resolution = parse_sales_observation(clean_text(document))
    return {
        "issue": issue,
        "date": date,
        "sales_rmb": sales_rmb,
        "sales_rmb_resolution": sales_resolution,
        "sixth_prize_count": sixth["national_count"] if sixth else None,
        "sixth_prize_rmb": sixth["single_prize_rmb"] if sixth else None,
        "base_prize_table_complete": complete_base_table(rows),
        "special_accounting_observation": (
            "EXPLICIT_SPECIAL_ACCOUNTING_IN_DRAW_TABLE"
            if specials
            else "NO_SPECIAL_ACCOUNTING_ROW_OBSERVED_IN_COMPLETE_PRIZE_TABLE"
            if complete_base_table(rows)
            else "UNKNOWN_INCOMPLETE_PRIZE_TABLE"
        ),
        "special_rows": specials,
        "source_id": source_id,
        "source_url": source_url,
        "raw_path": raw_path.as_posix(),
        "raw_sha256": raw_sha256,
        "source_locator": source_locator,
    }


def parse_jiangsu_list_pages(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_re = re.compile(
        r'<a\s+target="_blank"\s+href="([^"]*)"[^>]*>[^<]*第(20\d{5})期开奖公告</a>'
        r'.{0,500}?<span\s+class="articleDate"[^>]*>(\d{4}-\d{2}-\d{2})</span>',
        flags=re.S,
    )
    for path in sorted(root.glob("page_*.html"), key=lambda p: int(re.search(r"\d+", p.stem).group(0))):
        raw_sha = sha256_file(path)
        source_url = f"https://www.jslottery.com/winning_history_a?locale=zh-CN&lottery_type_id=10&page={int(re.search(r'\d+', path.stem).group(0))}&periods="
        page = path.read_text(encoding="utf-8", errors="replace")
        for index, match in enumerate(page_re.finditer(page), start=1):
            href, issue, date = match.groups()
            if not href.startswith("&lt;html"):
                continue
            embedded = html.unescape(href)
            records.append(
                make_record(
                    issue=issue,
                    date=date,
                    document=embedded,
                    source_id="JIANGSU_FC_OFFICIAL_EMBEDDED_ANNOUNCEMENT",
                    source_url=source_url,
                    raw_path=path,
                    raw_sha256=raw_sha,
                    source_locator=f"embedded_anchor_{index}",
                )
            )
    return records


def parse_hebei_details(index_path: Path, detail_root: Path) -> list[dict[str, Any]]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    mapping = {str(row["issueNo"]): str(row["link"]) for row in (index.get("data") or {}).get("rows") or []}
    records: list[dict[str, Any]] = []
    for path in sorted(detail_root.glob("*.html")):
        issue_match = re.search(r"(20\d{5})", path.stem)
        if not issue_match:
            continue
        issue = issue_match.group(1)
        document = path.read_text(encoding="utf-8", errors="replace")
        title_issue = re.search(r"第\s*(20\d{5})\s*期开奖公告", clean_text(document))
        if not title_issue or title_issue.group(1) != issue:
            continue
        date_match = re.search(r"开奖日期[：:]?\s*(20\d{2}-\d{2}-\d{2})", clean_text(document))
        link = mapping.get(issue)
        records.append(
            make_record(
                issue=issue,
                date=date_match.group(1) if date_match else None,
                document=document,
                source_id="HEBEI_FC_OFFICIAL_DETAIL",
                source_url=f"https://www.yzfcw.com{link}" if link else "UNKNOWN",
                raw_path=path,
                raw_sha256=sha256_file(path),
                source_locator="complete_detail_page",
            )
        )
    return records


def parse_sichuan_texts(map_path: Path | None, text_root: Path | None) -> list[dict[str, Any]]:
    if not map_path or not text_root or not map_path.exists() or not text_root.exists():
        return []
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    by_issue = {str(row["issue"]): row for row in mapping}
    records: list[dict[str, Any]] = []
    for path in sorted(text_root.glob("*.txt")):
        issue_match = re.search(r"(20\d{5})", path.stem)
        if not issue_match:
            continue
        issue = issue_match.group(1)
        text = path.read_text(encoding="utf-8", errors="replace")
        sixth_match = re.search(r"六等奖\s+(\d[\d,]*)\s+(\d[\d,]*)", re.sub(r"\s+", " ", text))
        if not sixth_match:
            continue
        meta = by_issue.get(issue) or {}
        pdf_path = Path(str(meta.get("pdf_path") or ""))
        base_complete = all(label in text for label in BASE_PRIZE_LABELS)
        records.append(
            {
                "issue": issue,
                "date": meta.get("date"),
                "sales_rmb": None,
                "sales_rmb_resolution": None,
                "sixth_prize_count": int(sixth_match.group(1).replace(",", "")),
                "sixth_prize_rmb": int(sixth_match.group(2).replace(",", "")),
                "base_prize_table_complete": base_complete,
                "special_accounting_observation": (
                    "EXPLICIT_SPECIAL_ACCOUNTING_IN_DRAW_TABLE"
                    if any(marker in text for marker in SPECIAL_MARKERS)
                    else "NO_SPECIAL_ACCOUNTING_ROW_OBSERVED_IN_COMPLETE_PRIZE_TABLE"
                    if base_complete
                    else "UNKNOWN_INCOMPLETE_PRIZE_TABLE"
                ),
                "special_rows": [],
                "source_id": "SICHUAN_FC_OFFICIAL_PDF",
                "source_url": meta.get("url"),
                "raw_path": pdf_path.as_posix(),
                "raw_sha256": meta.get("pdf_sha256"),
                "source_locator": "pdftotext_extraction",
            }
        )
    return records


def load_fixed_issues(path: Path | None) -> tuple[list[str], list[str]]:
    if not path or not path.exists():
        return [], ["FIXED_600_ISSUE_MANIFEST_MISSING"]
    issues = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.startswith("#")]
    errors: list[str] = []
    if len(issues) != EXPECTED_FIXED_COUNT:
        errors.append(f"FIXED_ISSUE_COUNT_{len(issues)}_EXPECTED_{EXPECTED_FIXED_COUNT}")
    if len(set(issues)) != len(issues):
        errors.append("FIXED_ISSUE_DUPLICATES")
    if issues and (min(issues) != WINDOW_START or max(issues) != WINDOW_END):
        errors.append(f"FIXED_ISSUE_BOUNDARY_{min(issues)}_{max(issues)}_EXPECTED_{WINDOW_START}_{WINDOW_END}")
    if any(not re.fullmatch(r"20\d{5}", issue) for issue in issues):
        errors.append("FIXED_ISSUE_FORMAT_INVALID")
    return issues, errors


def reconcile(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_issue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if WINDOW_START <= record["issue"] <= WINDOW_END:
            by_issue[record["issue"]].append(record)
    canonical: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for issue in sorted(by_issue):
        group = by_issue[issue]
        values = defaultdict(set)
        for record in group:
            for field in ("date", "sales_rmb", "sixth_prize_count", "sixth_prize_rmb"):
                if record.get(field) is not None:
                    values[field].add(record[field])
        field_conflicts = {
            field: sorted(items)
            for field, items in values.items()
            if field != "sales_rmb" and len(items) > 1
        }
        sales_observations = [
            (r["sales_rmb"], r.get("sales_rmb_resolution") or 1)
            for r in group
            if r.get("sales_rmb") is not None
        ]
        if sales_observations:
            lower = max(value - resolution / 2 for value, resolution in sales_observations)
            upper = min(value + resolution / 2 for value, resolution in sales_observations)
            if lower > upper:
                field_conflicts["sales_rmb"] = sorted({value for value, _ in sales_observations})
        if field_conflicts:
            conflicts.append({"issue": issue, "conflicts": field_conflicts, "sources": [r["source_id"] for r in group]})
        if len(group) > 1:
            duplicates.append(
                {
                    "issue": issue,
                    "source_count": len(group),
                    "sources": [r["source_id"] for r in group],
                    "sixth_counts": sorted({r["sixth_prize_count"] for r in group if r.get("sixth_prize_count") is not None}),
                    "exact_sixth_agreement": len(values["sixth_prize_count"]) <= 1,
                }
            )
        preferred = next((r for r in group if r["source_id"] == "HEBEI_FC_OFFICIAL_DETAIL"), group[0])
        precise_sales = min(
            (r for r in group if r.get("sales_rmb") is not None),
            key=lambda r: r.get("sales_rmb_resolution") or math.inf,
            default=preferred,
        )
        canonical.append(
            {
                "issue": issue,
                "date": next(iter(values["date"])) if len(values["date"]) == 1 else None,
                "sales_rmb": precise_sales.get("sales_rmb"),
                "sales_rmb_resolution": precise_sales.get("sales_rmb_resolution"),
                "sixth_prize_count": next(iter(values["sixth_prize_count"])) if len(values["sixth_prize_count"]) == 1 else None,
                "sixth_prize_rmb": next(iter(values["sixth_prize_rmb"])) if len(values["sixth_prize_rmb"]) == 1 else None,
                "special_accounting_observation": preferred["special_accounting_observation"],
                "special_rows": preferred["special_rows"],
                "source_count": len(group),
                "source_ids": sorted({r["source_id"] for r in group}),
                "source_urls": sorted({r["source_url"] for r in group}),
                "source_sha256": sorted({r["raw_sha256"] for r in group if r.get("raw_sha256")}),
            }
        )
    report = {
        "schema": "SSQ-PPAS12-DEDUP-v1",
        "candidate_issue_count": len(canonical),
        "duplicate_issue_count": len(duplicates),
        "conflict_issue_count": len(conflicts),
        "duplicates": duplicates,
        "conflicts": conflicts,
    }
    return canonical, report


def audit_overlaps(records: list[dict[str, Any]], fixed_issues: list[str]) -> dict[str, Any]:
    fixed_set = set(fixed_issues)
    fixed_scope_valid = len(fixed_issues) == EXPECTED_FIXED_COUNT and len(fixed_set) == EXPECTED_FIXED_COUNT
    by_issue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if (
            WINDOW_START <= record["issue"] <= WINDOW_END
            and record.get("sixth_prize_count") is not None
            and (not fixed_scope_valid or record["issue"] in fixed_set)
        ):
            by_issue[record["issue"]].append(record)
    eligible = [
        issue
        for issue, group in by_issue.items()
        if len({r["source_id"] for r in group}) >= 2
    ]
    eligible.sort(key=lambda issue: hashlib.sha256(f"{AUDIT_SEED}|{issue}".encode()).hexdigest())
    sampled = eligible[: min(AUDIT_SAMPLE_SIZE, len(eligible))]
    checks = []
    for issue in sampled:
        group = by_issue[issue]
        sixth = sorted({r["sixth_prize_count"] for r in group})
        prize = sorted({r["sixth_prize_rmb"] for r in group if r.get("sixth_prize_rmb") is not None})
        checks.append(
            {
                "issue": issue,
                "sources": sorted({r["source_id"] for r in group}),
                "sixth_prize_counts": sixth,
                "sixth_prize_rmb_values": prize,
                "pass": len(sixth) == 1 and len(prize) <= 1,
            }
        )
    return {
        "schema": "SSQ-PPAS12-INDEPENDENT-OFFICIAL-SOURCE-AUDIT-v1",
        "seed_text": AUDIT_SEED,
        "seed_sha256": hashlib.sha256(AUDIT_SEED.encode()).hexdigest(),
        "method": "SHA256-ranked deterministic sample from fixed-manifest issues having >=2 independent official sources",
        "fixed_manifest_scope_valid": fixed_scope_valid,
        "eligible_overlap_issues": len(eligible),
        "required_sample_size": AUDIT_SAMPLE_SIZE,
        "actual_sample_size": len(sampled),
        "pass_count": sum(check["pass"] for check in checks),
        "fail_count": sum(not check["pass"] for check in checks),
        "coverage_limitation": None if fixed_scope_valid else "The exact fixed-600 issue manifest is unavailable; any displayed overlap checks are diagnostic only.",
        "status": "PASS" if fixed_scope_valid and len(sampled) == AUDIT_SAMPLE_SIZE and all(check["pass"] for check in checks) else "BLOCKED_OR_FAILED",
        "checks": checks,
    }


def write_candidate_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "issue",
        "date",
        "sales_rmb",
        "sales_rmb_resolution",
        "sixth_prize_count",
        "sixth_prize_rmb",
        "special_accounting_observation",
        "source_count",
        "source_ids",
        "source_urls",
        "source_sha256",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {field: row.get(field) for field in fields}
            for field in ("source_ids", "source_urls", "source_sha256"):
                out[field] = json.dumps(out[field], ensure_ascii=False, separators=(",", ":"))
            writer.writerow(out)


def build_manifest(output_root: Path, raw_roots: list[Path], produced: list[Path], gate: dict[str, Any]) -> dict[str, Any]:
    files = []
    seen: set[Path] = set()
    for root in raw_roots:
        if root and root.exists():
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append({"role": "RAW_OFFICIAL_EVIDENCE", "path": path.as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)})
    for path in produced:
        files.append({"role": "DERIVED_AUDIT_ARTIFACT", "path": path.as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)})
    body = {
        "schema": "SSQ-PPAS12-EVIDENCE-MANIFEST-CANDIDATE-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_claim": {"start": WINDOW_START, "end": WINDOW_END, "expected_fixed_count": EXPECTED_FIXED_COUNT},
        "status": "FROZEN" if gate["data_gate"] == "PASS" else "CANDIDATE_NOT_FROZEN",
        "pp_as_1_2_fit_runs": 0,
        "data_gate_sha256": hashlib.sha256(canonical_json_bytes(gate)).hexdigest(),
        "files": files,
    }
    body["manifest_body_sha256"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jiangsu-list-dir", type=Path, required=True)
    parser.add_argument("--hebei-index", type=Path, required=True)
    parser.add_argument("--hebei-detail-dir", type=Path, required=True)
    parser.add_argument("--sichuan-map", type=Path)
    parser.add_argument("--sichuan-text-dir", type=Path)
    parser.add_argument("--fixed-issues", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = (
        parse_jiangsu_list_pages(args.jiangsu_list_dir)
        + parse_hebei_details(args.hebei_index, args.hebei_detail_dir)
        + parse_sichuan_texts(args.sichuan_map, args.sichuan_text_dir)
    )
    records.sort(key=lambda row: (row["issue"], row["source_id"], row["raw_sha256"] or ""))
    canonical, dedup = reconcile(records)
    fixed_issues, fixed_errors = load_fixed_issues(args.fixed_issues)
    audit = audit_overlaps(records, fixed_issues)

    canonical_by_issue = {row["issue"]: row for row in canonical}
    missing_fixed = [issue for issue in fixed_issues if issue not in canonical_by_issue]
    incomplete_fixed = [
        issue
        for issue in fixed_issues
        if issue in canonical_by_issue
        and (
            any(canonical_by_issue[issue].get(field) is None for field in ("date", "sales_rmb", "sixth_prize_count", "sixth_prize_rmb"))
            or canonical_by_issue[issue].get("special_accounting_observation") == "UNKNOWN_INCOMPLETE_PRIZE_TABLE"
            or not canonical_by_issue[issue].get("source_sha256")
        )
    ]
    blockers = list(fixed_errors)
    if dedup["conflict_issue_count"]:
        blockers.append(f"SOURCE_CONFLICTS_{dedup['conflict_issue_count']}")
    if missing_fixed:
        blockers.append(f"FIXED_ISSUES_MISSING_{len(missing_fixed)}")
    if incomplete_fixed:
        blockers.append(f"FIXED_ISSUES_INCOMPLETE_{len(incomplete_fixed)}")
    if audit["status"] != "PASS":
        blockers.append(f"INDEPENDENT_AUDIT_{audit['status']}")

    gate = {
        "schema": "SSQ-PPAS12-DATA-GATE-v1",
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_gate": "PASS" if not blockers else "BLOCKED",
        "manifest_frozen": not blockers,
        "pp_as_1_2_fit_runs": 0,
        "behavior_gate": "NOT_RUN_DATA_GATE_NOT_PASSED" if blockers else "ELIGIBLE_NOT_RUN",
        "economic_gate": "NOT_RUN",
        "fixed_issue_manifest_path": args.fixed_issues.as_posix() if args.fixed_issues else None,
        "fixed_issue_manifest_sha256": sha256_file(args.fixed_issues) if args.fixed_issues and args.fixed_issues.exists() else None,
        "fixed_issue_count": len(fixed_issues),
        "official_candidate_issue_count_in_claimed_boundaries": len(canonical),
        "candidate_first_issue": canonical[0]["issue"] if canonical else None,
        "candidate_last_issue": canonical[-1]["issue"] if canonical else None,
        "source_record_count": len(records),
        "source_ids": sorted({record["source_id"] for record in records}),
        "missing_fixed_issues": missing_fixed,
        "incomplete_fixed_issues": incomplete_fixed,
        "blockers": blockers,
        "hard_rule": "No PP-AS-1.2 model may run and no manifest may be frozen unless data_gate=PASS.",
    }

    source_records_path = args.output_dir / "SOURCE_RECORDS.jsonl"
    source_records_path.write_bytes(b"".join(canonical_json_bytes(row) for row in records))
    canonical_jsonl_path = args.output_dir / "CANONICAL_CANDIDATE.jsonl"
    canonical_jsonl_path.write_bytes(b"".join(canonical_json_bytes(row) for row in canonical))
    canonical_csv_path = args.output_dir / "CANONICAL_CANDIDATE.csv"
    write_candidate_csv(canonical_csv_path, canonical)
    dedup_path = args.output_dir / "DEDUP_RECONCILIATION.json"
    write_json(dedup_path, dedup)
    audit_path = args.output_dir / "INDEPENDENT_OFFICIAL_AUDIT.json"
    write_json(audit_path, audit)
    gate_path = args.output_dir / "DATA_GATE_REPORT.json"
    write_json(gate_path, gate)
    produced = [source_records_path, canonical_jsonl_path, canonical_csv_path, dedup_path, audit_path, gate_path]
    manifest = build_manifest(
        args.output_dir,
        [args.jiangsu_list_dir, args.hebei_index.parent, args.sichuan_map.parent if args.sichuan_map else None],
        produced,
        gate,
    )
    manifest_path = args.output_dir / "EVIDENCE_MANIFEST_CANDIDATE.json"
    write_json(manifest_path, manifest)

    print(json.dumps(gate, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


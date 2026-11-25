"""
Shared Local-First production safety shell for SSQ V4/V5.

This module is intentionally model-agnostic. Candidate tickets are produced by
V4 or V5, then both routes pass through the same schedule/time gate, rule-state
ledger chain, physical-slip validation, canonical commitment, four-stage durable
transaction, and crash recovery machinery.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
_V4_RC2 = _ROOT / "v4_rc2"
if str(_V4_RC2) not in sys.path:
    sys.path.insert(0, str(_V4_RC2))

from registry_and_parsers_v4 import GateBlockedError, PromoEvidence, sha256_bytes  # type: ignore
from rules_and_ev_v4 import (  # type: ignore
    build_draw_ev_context,
    confirm_physical_slips_post_purchase,
    evaluate_2026_rule_state_machine,
    validate_physical_slip_plan,
)
from runner_v4 import (  # type: ignore
    AppendOnlyLedger,
    _verify_staged_directory,
    _write_file_durable,
    canonical_json,
    recover_staged_publish,
)

BJT = ZoneInfo("Asia/Shanghai")
DEFAULT_SCHEDULE_PATH = _ROOT / "data" / "local_schedule_manifest.json"
DEFAULT_GENESIS_PATH = _ROOT / "data" / "production_ledger_genesis.json"
DEFAULT_LEDGER_PATH = _ROOT / "runtime" / "production_ledger.jsonl"
DEFAULT_OUTPUT_ROOT = _ROOT / "runtime" / "production_output"

REQUIRED_COMMITMENT_FIELDS = frozenset({
    "algorithm_version",
    "rng_model",
    "input_manifest_sha256",
    "twin_artifact_sha256",
    "policy_artifact_sha256",
    "sparse_pool_sha256",
    "circuit_breaker_status",
    "effective_route_mode",
    "rule_regime_state",
    "physical_slip_plan",
    "seed_sha256",
    "tickets",
})


@dataclass(frozen=True)
class LocalSchedule:
    manifest_sha256: str
    timezone_name: str
    draw_weekdays_bjt: Tuple[int, ...]
    draw_time_bjt: str
    coverage_start_bjt: str
    coverage_end_bjt: str
    years: Dict[str, Dict[str, Any]]
    unresolved_year_policy: str


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_local_schedule(path: str | Path = DEFAULT_SCHEDULE_PATH) -> LocalSchedule:
    p = Path(path)
    raw = p.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    if doc.get("schema") != "SSQ_LOCAL_SCHEDULE_MANIFEST_1.0":
        raise GateBlockedError("LOCAL_SCHEDULE_SCHEMA_INVALID", str(doc.get("schema")))
    weekdays = tuple(int(x) for x in doc["draw_weekdays_bjt"])
    if weekdays != (1, 3, 6) or str(doc["draw_time_bjt"]) != "21:15":
        raise GateBlockedError("LOCAL_SCHEDULE_CORE_RULE_MISMATCH", "Expected Tue/Thu/Sun 21:15 BJT.")
    return LocalSchedule(
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        timezone_name=str(doc["timezone"]),
        draw_weekdays_bjt=weekdays,
        draw_time_bjt=str(doc["draw_time_bjt"]),
        coverage_start_bjt=str(doc["coverage_start_bjt"]),
        coverage_end_bjt=str(doc["coverage_end_bjt"]),
        years=dict(doc["years"]),
        unresolved_year_policy=str(doc.get("unresolved_year_policy", "FAIL_CLOSED")),
    )


def _year_suspensions(schedule: LocalSchedule, year: int) -> frozenset[str]:
    y = schedule.years.get(str(year))
    if y is None:
        raise GateBlockedError("SUSPENSION_CALENDAR_YEAR_MISSING", str(year))
    status = str(y.get("lottery_suspension_status", ""))
    if status != "FROZEN_OFFICIAL":
        if schedule.unresolved_year_policy == "FAIL_CLOSED":
            raise GateBlockedError(
                "SUSPENSION_CALENDAR_NOT_FROZEN",
                f"{year} lottery suspension status={status!r}; update local manifest before production.",
            )
    return frozenset(str(x) for x in y.get("suspended_dates_bjt", []))


def next_issue_and_draw(
    latest_issue: str,
    latest_draw_date_bjt: str,
    *,
    schedule_path: str | Path = DEFAULT_SCHEDULE_PATH,
    now_utc: datetime | None = None,
) -> Dict[str, Any]:
    schedule = load_local_schedule(schedule_path)
    latest_date = date.fromisoformat(latest_draw_date_bjt)
    latest_year = int(latest_issue[:4])
    latest_seq = int(latest_issue[4:])
    if latest_date.year != latest_year:
        raise GateBlockedError(
            "LATEST_ISSUE_DATE_YEAR_MISMATCH",
            f"{latest_issue} vs {latest_draw_date_bjt}",
        )

    cov_start = date.fromisoformat(schedule.coverage_start_bjt)
    cov_end = date.fromisoformat(schedule.coverage_end_bjt)
    cursor = latest_date + timedelta(days=1)
    target_date: date | None = None
    while cursor <= cov_end:
        if cursor < cov_start:
            cursor += timedelta(days=1)
            continue
        suspended = _year_suspensions(schedule, cursor.year)
        if cursor.weekday() in schedule.draw_weekdays_bjt and cursor.isoformat() not in suspended:
            target_date = cursor
            break
        cursor += timedelta(days=1)
    if target_date is None:
        raise GateBlockedError("LOCAL_SCHEDULE_EXHAUSTED", f"No next draw after {latest_draw_date_bjt}.")

    if target_date.year == latest_year:
        target_issue = f"{latest_year}{latest_seq + 1:03d}"
    elif target_date.year == latest_year + 1:
        target_issue = f"{target_date.year}001"
    else:
        raise GateBlockedError("ISSUE_YEAR_JUMP_INVALID", f"{latest_issue} -> {target_date}")

    hour, minute = [int(x) for x in schedule.draw_time_bjt.split(":")]
    draw_bjt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=BJT)
    draw_utc = draw_bjt.astimezone(timezone.utc)
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise GateBlockedError("MISSING_TIMEZONE", "now_utc must be timezone-aware.")
    now = now.astimezone(timezone.utc)
    if now >= draw_utc:
        raise GateBlockedError(
            "DRAW_ALREADY_STARTED_OR_PASSED",
            f"now={now.isoformat()} >= draw_at_utc={draw_utc.isoformat()}",
        )
    return {
        "target_issue": target_issue,
        "target_draw_date_bjt": target_date.isoformat(),
        "draw_at_utc": draw_utc.isoformat(),
        "schedule_manifest_sha256": schedule.manifest_sha256,
        "schedule_calendar_status": str(schedule.years[str(target_date.year)]["lottery_suspension_status"]),
    }


def load_ledger_genesis(path: str | Path = DEFAULT_GENESIS_PATH) -> Dict[str, Any]:
    p = Path(path)
    doc = json.loads(p.read_text("utf-8"))
    if doc.get("schema") != "SSQ_PRODUCTION_LEDGER_GENESIS_1.0":
        raise GateBlockedError("LEDGER_GENESIS_SCHEMA_INVALID", str(doc.get("schema")))
    doc = dict(doc)
    doc["genesis_file_sha256"] = _sha256_file(p)
    return doc


def open_production_ledger(
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    genesis_path: str | Path = DEFAULT_GENESIS_PATH,
) -> AppendOnlyLedger:
    genesis = load_ledger_genesis(genesis_path)
    return AppendOnlyLedger(Path(ledger_path), genesis_prev_sha256=genesis["genesis_file_sha256"])


def inherit_special_rule_state(
    ledger: AppendOnlyLedger,
    latest_completed_issue: str,
    *,
    genesis_path: str | Path = DEFAULT_GENESIS_PATH,
) -> Tuple[bool, str]:
    ok, detail = ledger.verify_chain()
    if not ok:
        raise GateBlockedError("LEDGER_CORRUPTED", detail)
    inherited = ledger.inherited_special_rule_state(latest_completed_issue)
    if inherited is not None:
        return inherited, "PREVIOUS_ISSUE_LEDGER"
    records = ledger.load_records()
    genesis = load_ledger_genesis(genesis_path)
    if records:
        raise GateBlockedError(
            "LEDGER_PREVIOUS_ISSUE_STATE_MISSING",
            f"No committed rule state for latest completed issue {latest_completed_issue}.",
        )
    if latest_completed_issue != str(genesis["frozen_through_issue"]):
        raise GateBlockedError(
            "LEDGER_GENESIS_CANNOT_BRIDGE",
            f"Genesis bridges only {genesis['frozen_through_issue']}, not {latest_completed_issue}.",
        )
    return bool(genesis["special_rule_active"]), "FROZEN_LEDGER_GENESIS"


def default_physical_slip_plan(ticket_count: int, max_tickets_per_slip: int = 10) -> List[Dict[str, Any]]:
    if not isinstance(ticket_count, int) or ticket_count < 1:
        raise GateBlockedError("INVALID_TICKET_COUNT", repr(ticket_count))
    if max_tickets_per_slip < 1:
        raise GateBlockedError("INVALID_SLIP_CHUNK_SIZE", repr(max_tickets_per_slip))
    slips: List[Dict[str, Any]] = []
    for start in range(0, ticket_count, max_tickets_per_slip):
        indices = list(range(start, min(ticket_count, start + max_tickets_per_slip)))
        slips.append({
            "slip_id": f"SLIP_{len(slips)+1:03d}",
            "bet_type": "SINGLE",
            "ticket_indices": indices,
            "amount_rmb": 2 * len(indices),
        })
    return slips


def local_promo_evidence(
    *,
    active: bool,
    remaining_sixth_promo_rmb: float | None,
    min_single_slip_rmb: int = 20,
) -> PromoEvidence:
    remaining = 0.0 if remaining_sixth_promo_rmb is None else float(remaining_sixth_promo_rmb)
    if remaining < 0:
        raise GateBlockedError("INVALID_PROMO_REMAINING_RMB", str(remaining))
    return PromoEvidence(
        present=bool(active),
        source_id="LOCAL_HISTORY_PROMO_FLAG",
        parser_version="local_first_v1",
        sha256=None,
        active_for_target_issue=bool(active),
        start_issue=None,
        end_issue=None,
        min_single_slip_rmb=int(min_single_slip_rmb) if active else 0,
        allowed_bet_types=frozenset({"SINGLE", "DUPLEX", "DANTUO"}) if active else frozenset(),
        second_prize_special_active=bool(active),
        sixth_prize_double_active=bool(active),
        remaining_sixth_promo_rmb=remaining,
    )


def validate_and_classify_slips(
    *,
    physical_slip_plan: List[Dict[str, Any]] | None,
    ticket_count: int,
    promo: PromoEvidence,
    pool_after_prev_draw_rmb: float,
    rule_state: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    plan = physical_slip_plan if physical_slip_plan is not None else default_physical_slip_plan(ticket_count)
    validated = validate_physical_slip_plan(plan, ticket_count=ticket_count, budget_rmb=2 * ticket_count)
    context = build_draw_ev_context(
        pool_after_prev_draw_rmb=float(pool_after_prev_draw_rmb),
        regime=rule_state,
        promo=promo,
        validated_slip_plan=validated,
    )
    return validated, context


def commit_production_bundle(
    *,
    target_issue: str,
    output_root: str | Path,
    ledger: AppendOnlyLedger,
    system_version: str,
    seed: bytes,
    commitment_body: Dict[str, Any],
    tickets_doc: Dict[str, Any],
    receipt_doc: Dict[str, Any],
    sparse_pool_manifest: Dict[str, Any],
    ledger_meta: Dict[str, Any],
    _inject_failure_after_ledger_commit: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    missing = sorted(REQUIRED_COMMITMENT_FIELDS.difference(commitment_body))
    if missing:
        raise GateBlockedError("V5_COMMITMENT_FIELDS_MISSING", ",".join(missing))

    root = Path(output_root)
    final_dir = root / f"issue_{target_issue}"
    staging_dir = root / f".staging_{target_issue}"
    if ledger.find_issue_record(target_issue) is not None or final_dir.exists():
        raise GateBlockedError("REFUSE_DUPLICATE_ISSUE", f"Issue {target_issue} already exists.")
    if staging_dir.exists():
        raise GateBlockedError(
            "UNRECOVERED_STAGING_EXISTS",
            f"Run recover_staged_publish({target_issue!r}) before retrying.",
        )

    nonce = os.urandom(32)
    commitment = dict(commitment_body)
    commitment["nonce_hex"] = nonce.hex()
    commitment_sha = sha256_bytes(canonical_json(commitment))
    private_doc = {
        "seed_hex": seed.hex(),
        "nonce_hex": nonce.hex(),
        "commitment_body": commitment,
    }
    tickets_doc = {**tickets_doc, "commitment_sha256": commitment_sha}
    receipt_doc = {**receipt_doc, "commitment_sha256": commitment_sha}

    staging_dir.mkdir(parents=True, exist_ok=False)
    priv_sha = _write_file_durable(
        staging_dir / "PRIVATE_SEED_AND_NONCE.json",
        json.dumps(private_doc, indent=2, ensure_ascii=False).encode("utf-8"),
        mode=0o600,
    )
    tickets_sha = _write_file_durable(
        staging_dir / "TICKETS.json",
        json.dumps(tickets_doc, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    receipt_sha = _write_file_durable(
        staging_dir / "RECEIPT.json",
        json.dumps(receipt_doc, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    sparse_sha = _write_file_durable(
        staging_dir / "SPARSE_POOL_MANIFEST.json",
        canonical_json(sparse_pool_manifest),
    )
    if sparse_sha != str(commitment["sparse_pool_sha256"]):
        raise GateBlockedError(
            "SPARSE_POOL_SHA_MISMATCH",
            f"commitment={commitment['sparse_pool_sha256']} staged={sparse_sha}",
        )
    stage_manifest = {
        "schema": "SSQ_LOCAL_PRODUCTION_STAGE_MANIFEST_1.0",
        "target_issue": target_issue,
        "commitment_sha256": commitment_sha,
        "files": {
            "PRIVATE_SEED_AND_NONCE.json": priv_sha,
            "TICKETS.json": tickets_sha,
            "RECEIPT.json": receipt_sha,
            "SPARSE_POOL_MANIFEST.json": sparse_sha,
        },
    }
    stage_manifest_sha = _write_file_durable(
        staging_dir / "STAGE_MANIFEST.json",
        canonical_json(stage_manifest),
    )

    _verify_staged_directory(
        staging_dir=staging_dir,
        expected_commitment_sha256=commitment_sha,
        expected_stage_manifest_sha256=stage_manifest_sha,
    )
    ok, detail = ledger.verify_chain()
    if not ok:
        raise GateBlockedError("LEDGER_CORRUPTED", detail)

    record = ledger.commit_record(
        target_issue=target_issue,
        commitment_sha256=commitment_sha,
        stage_manifest_sha256=stage_manifest_sha,
        meta=ledger_meta,
        system_version=system_version,
    )
    if _inject_failure_after_ledger_commit is not None:
        _inject_failure_after_ledger_commit()

    os.replace(staging_dir, final_dir)
    return {
        "receipt": receipt_doc,
        "tickets": tickets_doc,
        "commitment_sha256": commitment_sha,
        "ledger_record_sha256": record["record_sha256"],
        "stage_manifest_sha256": stage_manifest_sha,
        "published_dir": str(final_dir),
    }


def confirm_published_purchase(
    published_dir: str | Path,
    actual_printed_slips: List[Dict[str, Any]],
) -> Dict[str, Any]:
    root = Path(published_dir)
    receipt = json.loads((root / "RECEIPT.json").read_text("utf-8"))
    promo_doc = receipt["local_promo"]
    promo = local_promo_evidence(
        active=bool(promo_doc["active"]),
        remaining_sixth_promo_rmb=float(promo_doc["remaining_sixth_promo_rmb"]),
        min_single_slip_rmb=int(promo_doc["min_single_slip_rmb"]),
    )
    return confirm_physical_slips_post_purchase(
        planned_slip_plan=receipt["physical_slip_plan"],
        actual_printed_slips=actual_printed_slips,
        promo=promo,
    )


__all__ = [
    "DEFAULT_SCHEDULE_PATH",
    "DEFAULT_GENESIS_PATH",
    "DEFAULT_LEDGER_PATH",
    "DEFAULT_OUTPUT_ROOT",
    "GateBlockedError",
    "AppendOnlyLedger",
    "recover_staged_publish",
    "load_local_schedule",
    "next_issue_and_draw",
    "open_production_ledger",
    "inherit_special_rule_state",
    "default_physical_slip_plan",
    "local_promo_evidence",
    "validate_and_classify_slips",
    "commit_production_bundle",
    "confirm_published_purchase",
    "evaluate_2026_rule_state_machine",
    "canonical_json",
    "sha256_bytes",
]

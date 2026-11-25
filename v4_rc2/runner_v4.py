"""
runner_v4.py
封口：P1-B08 (全量绑定 Commitment),
      P1-B09 (STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH + 无重抽崩溃恢复)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from registry_and_parsers_v4 import (
    GateBlockedError,
    bind_schedule_suspension,
    load_promo_evidence,
    load_promo_status_evidence,
    load_schedule_manifest_evidence,
    load_suspension_evidence,
    sha256_bytes,
    verify_2_of_n_official_quorum,
)
from rules_and_ev_v4 import (
    build_draw_ev_context,
    evaluate_2026_rule_state_machine,
    validate_physical_slip_plan,
    verify_schedule_and_continuity,
)
from sampler_v4 import (
    ALGORITHM_VERSION,
    RNG_MODEL_DECLARATION,
    DomainSeparatedDRBG,
    generate_v4_tickets,
)

SYSTEM_VERSION = "SSQ_V4_BALANCED_COVERAGE"
ALLOWED_MODE = "UNIFORM_MARGINAL_BALANCED_COVERAGE"


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _write_file_durable(path: Path, content: bytes, mode: int | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    if mode is not None:
        os.chmod(path, mode)
    return sha256_bytes(content)


class AppendOnlyLedger:
    def __init__(self, ledger_path: Path, genesis_prev_sha256: str) -> None:
        self.ledger_path = ledger_path
        self.genesis_prev_sha256 = genesis_prev_sha256

    def load_records(self) -> List[Dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    out.append(json.loads(s))
        return out

    def find_issue_record(self, target_issue: str) -> Dict[str, Any] | None:
        for rec in self.load_records():
            if rec.get("target_issue") == target_issue:
                return rec
        return None

    def inherited_special_rule_state(self, latest_completed_issue: str) -> bool | None:
        """Return the previous issue's committed rule state when available."""
        rec = self.find_issue_record(latest_completed_issue)
        if rec is None:
            return None
        state = (rec.get("meta") or {}).get("special_rule_active")
        if not isinstance(state, bool):
            raise GateBlockedError(
                "LEDGER_RULE_STATE_MISSING",
                f"Ledger record {latest_completed_issue} lacks boolean special_rule_active.",
            )
        return state

    def verify_chain(self) -> Tuple[bool, str]:
        records = self.load_records()
        expected_prev = self.genesis_prev_sha256
        for idx, rec in enumerate(records):
            if rec.get("prev_sha256") != expected_prev:
                return False, f"CHAIN_BREAK_AT_INDEX_{idx}"
            body = {k: v for k, v in rec.items() if k != "record_sha256"}
            actual_hash = sha256_bytes(canonical_json(body))
            if rec.get("record_sha256") != actual_hash:
                return False, f"HASH_MISMATCH_AT_INDEX_{idx}"
            expected_prev = actual_hash
        return True, expected_prev

    def commit_record(
        self,
        target_issue: str,
        commitment_sha256: str,
        stage_manifest_sha256: str,
        meta: Dict[str, Any],
        system_version: str = SYSTEM_VERSION,
    ) -> Dict[str, Any]:
        ok, head_hash = self.verify_chain()
        if not ok:
            raise GateBlockedError("LEDGER_CORRUPTED", head_hash)
        if self.find_issue_record(target_issue) is not None:
            raise GateBlockedError(
                "REFUSE_DUPLICATE_ISSUE",
                f"Issue {target_issue} already committed in ledger.",
            )

        body = {
            "prev_sha256": head_hash,
            "system_version": system_version,
            "target_issue": target_issue,
            "commitment_sha256": commitment_sha256,
            "stage_manifest_sha256": stage_manifest_sha256,
            "meta": meta,
        }
        record = {**body, "record_sha256": sha256_bytes(canonical_json(body))}
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        with self.ledger_path.open("ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        ok_after, _ = self.verify_chain()
        if not ok_after:
            raise GateBlockedError("LEDGER_POST_COMMIT_VERIFY_FAILED", "Chain check failed.")
        return record


def _verify_staged_directory(
    staging_dir: Path,
    expected_commitment_sha256: str,
    expected_stage_manifest_sha256: str | None = None,
) -> Dict[str, Any]:
    """Phase 2 (HASH VERIFY): 重新从磁盘读取全部暂存文件并校验 SHA-256、权限与 Commitment。"""
    manifest_path = staging_dir / "STAGE_MANIFEST.json"
    if not manifest_path.is_file():
        raise GateBlockedError("STAGE_MANIFEST_MISSING", f"Missing {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = sha256_bytes(manifest_bytes)
    if expected_stage_manifest_sha256 is not None and manifest_sha != expected_stage_manifest_sha256:
        raise GateBlockedError("STAGE_MANIFEST_SHA_MISMATCH", "Staged manifest tampered.")

    manifest = json.loads(manifest_bytes.decode("utf-8"))
    for fname, expected_sha in manifest["files"].items():
        fpath = staging_dir / fname
        if not fpath.is_file():
            raise GateBlockedError("STAGED_FILE_MISSING", f"Missing staged file: {fname}")
        actual_sha = sha256_bytes(fpath.read_bytes())
        if actual_sha != expected_sha:
            raise GateBlockedError("STAGED_FILE_SHA_MISMATCH", f"Tampered staged file: {fname}")

    priv_path = staging_dir / "PRIVATE_SEED_AND_NONCE.json"
    if (os.stat(priv_path).st_mode & 0o777) != 0o600:
        raise GateBlockedError("PRIVATE_FILE_MODE_INVALID", "PRIVATE file must be 0600.")

    priv_doc = json.loads(priv_path.read_bytes().decode("utf-8"))
    recomputed_comm = sha256_bytes(canonical_json(priv_doc["commitment_body"]))
    if recomputed_comm != expected_commitment_sha256:
        raise GateBlockedError("COMMITMENT_RECOMPUTE_MISMATCH", "Recomputed commitment mismatch.")

    return manifest


def recover_staged_publish(
    target_issue: str,
    output_root: Path,
    ledger: AppendOnlyLedger,
) -> Dict[str, Any]:
    """
    P1-B09 故障恢复入口（严禁重抽）：
    - 若账本已提交且正式目录未发布：核验 .staging_<issue> 哈希与账本一致后直接完成原子发布。
    - 若账本未提交但残留孤儿 .staging_<issue>：清理暂存目录，允许重新正常执行。
    """
    staging_dir = output_root / f".staging_{target_issue}"
    final_dir = output_root / f"issue_{target_issue}"
    rec = ledger.find_issue_record(target_issue)

    if rec is not None:
        if final_dir.exists():
            return {"recovery_action": "ALREADY_PUBLISHED", "final_dir": str(final_dir)}
        if not staging_dir.exists():
            raise GateBlockedError(
                "RECOVERY_IMPOSSIBLE_STAGING_LOST",
                f"Issue {target_issue} is in ledger but staging dir is missing. Redraw FORBIDDEN.",
            )
        _verify_staged_directory(
            staging_dir=staging_dir,
            expected_commitment_sha256=rec["commitment_sha256"],
            expected_stage_manifest_sha256=rec["stage_manifest_sha256"],
        )
        os.replace(staging_dir, final_dir)
        return {"recovery_action": "COMPLETED_ATOMIC_PUBLISH_NO_REDRAW", "final_dir": str(final_dir)}

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
        return {"recovery_action": "CLEANED_UNCOMMITTED_ORPHAN_STAGE"}
    return {"recovery_action": "NO_RECOVERY_NEEDED"}


def run_v4_production(
    input_data: Dict[str, Any],
    output_root: Path,
    ledger: AppendOnlyLedger,
    now_utc: datetime | None = None,
    _inject_failure_after_ledger_commit: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    """
    四阶段事务执行：STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH
    """
    # 0. 基础硬门禁
    if input_data.get("mode") != ALLOWED_MODE:
        raise GateBlockedError("INVALID_MODE", f"Only {ALLOWED_MODE} is permitted.")
    if input_data.get("apply_popularity_filter") is not False:
        raise GateBlockedError("POPULARITY_FILTER_BLOCKED", "Popularity filter blocked in V4.")

    target_issue = str(input_data["target_issue"])
    ticket_count = input_data.get("ticket_count")
    budget_rmb = input_data.get("budget_rmb")
    if not isinstance(ticket_count, int) or ticket_count < 1 or budget_rmb != 2 * ticket_count:
        raise GateBlockedError("INVALID_TICKET_OR_BUDGET", "budget_rmb must equal 2 * ticket_count.")

    final_dir = output_root / f"issue_{target_issue}"
    staging_dir = output_root / f".staging_{target_issue}"
    if ledger.find_issue_record(target_issue) is not None or final_dir.exists():
        raise GateBlockedError("REFUSE_DUPLICATE_ISSUE", f"Issue {target_issue} already exists.")
    if staging_dir.exists():
        raise GateBlockedError(
            "UNRECOVERED_STAGING_EXISTS",
            f"Run recover_staged_publish({target_issue!r}) before retrying.",
        )

    # 1. 原始证据绑定与独立源仲裁（P1-B01, P1-B02, P1-B06, Schedule Manifest）
    quorum = verify_2_of_n_official_quorum(input_data["official_sources"], min_quorum=2)
    schedule_base = load_schedule_manifest_evidence(input_data["schedule_manifest"])
    if "suspension_evidence" not in input_data:
        raise GateBlockedError(
            "SUSPENSION_EVIDENCE_REQUIRED",
            "A full-year official suspension notice must be supplied and hash-bound.",
        )
    suspension_ev = load_suspension_evidence(input_data["suspension_evidence"])
    schedule_ev = bind_schedule_suspension(schedule_base, suspension_ev)
    promo_ev = load_promo_evidence(input_data.get("promo_evidence"), target_issue=target_issue)
    latest_completed_issue = str(quorum.latest_draw["issue"])
    promo_status_ev = None
    if promo_ev.active_for_target_issue:
        status_entry = input_data.get("promo_status_evidence")
        if status_entry is None:
            raise GateBlockedError(
                "PROMO_STATUS_EVIDENCE_REQUIRED",
                "Active promotion requires current official promotion-status evidence.",
            )
        promo_status_ev = load_promo_status_evidence(
            status_entry,
            expected_latest_issue=latest_completed_issue,
        )
        if promo_ev.sixth_prize_double_active:
            promo_ev = replace(
                promo_ev,
                remaining_sixth_promo_rmb=promo_status_ev.remaining_sixth_promo_rmb,
            )

    draw_dt_utc = verify_schedule_and_continuity(
        target_issue=target_issue,
        draw_at_utc_str=str(input_data["draw_at_utc"]),
        latest_completed_draw=quorum.latest_draw,
        schedule=schedule_ev,
        now_utc=now_utc,
    )

    # 2. 2026 滞回规则状态机 + 物理分票计划 + EV 上下文（P1-B03, P1-B04, P1-B05）
    # Production rule-state inheritance: once the previous issue exists in this
    # ledger, caller input cannot override it.  An explicit input is accepted
    # only as a one-time migration/bootstrap path when no previous ledger record
    # exists yet; it is bound into the Commitment and receipt.
    inherited_state = ledger.inherited_special_rule_state(latest_completed_issue)
    caller_state = input_data.get("special_rule_active_previous")
    if inherited_state is not None:
        if caller_state is not None and caller_state is not inherited_state:
            raise GateBlockedError(
                "CALLER_RULE_STATE_CONFLICT",
                f"Caller state {caller_state!r} conflicts with ledger-inherited {inherited_state!r}.",
            )
        special_rule_previous = inherited_state
        rule_state_source = "PREVIOUS_ISSUE_LEDGER"
    else:
        if not isinstance(caller_state, bool):
            raise GateBlockedError(
                "MISSING_SPECIAL_RULE_PREV_STATE",
                "No previous-issue ledger state exists; one-time bootstrap boolean is required.",
            )
        special_rule_previous = caller_state
        rule_state_source = "ONE_TIME_MIGRATION_BOOTSTRAP"

    regime_state = evaluate_2026_rule_state_machine(
        pool_after_prev_draw_rmb=float(quorum.latest_draw["pool_after_rmb"]),
        special_rule_active_previous=special_rule_previous,
    )
    validated_slips = validate_physical_slip_plan(
        physical_slip_plan=input_data.get("physical_slip_plan", []),
        ticket_count=ticket_count,
        budget_rmb=budget_rmb,
    )
    ev_context = build_draw_ev_context(
        pool_after_prev_draw_rmb=float(quorum.latest_draw["pool_after_rmb"]),
        regime=regime_state,
        promo=promo_ev,
        validated_slip_plan=validated_slips,
    )

    # 3. CSPRNG 抽样与全量绑定 Commitment（P1-B07, P1-B08）
    seed = os.urandom(32)
    nonce = os.urandom(32)
    seed_sha = sha256_bytes(seed)
    input_manifest_sha = sha256_bytes(canonical_json(input_data))
    sample_out = generate_v4_tickets(ticket_count=ticket_count, drbg=DomainSeparatedDRBG(seed=seed))
    generated_utc = (now_utc or datetime.now(timezone.utc)).isoformat()

    commitment_body = {
        "schema": "SSQ_V4_COMMITMENT_2.0",
        "system_version": SYSTEM_VERSION,
        "route_mode": ALLOWED_MODE,
        "algorithm_version": ALGORITHM_VERSION,
        "rng_model": RNG_MODEL_DECLARATION,
        "issue": target_issue,
        "draw_at_utc": draw_dt_utc.isoformat(),
        "ticket_count": ticket_count,
        "budget_rmb": budget_rmb,
        "input_manifest_sha256": input_manifest_sha,
        "official_evidence_hashes": quorum.evidence_hashes,
        "schedule_manifest_sha256": schedule_ev.sha256,
        "suspension_evidence_sha256": schedule_ev.suspension_sha256,
        "promo_evidence_sha256": promo_ev.sha256,
        "promo_status_evidence_sha256": promo_status_ev.sha256 if promo_status_ev is not None else None,
        "seed_sha256": seed_sha,
        "rule_regime_state": {
            "rule_regime": regime_state.rule_regime,
            "transition": regime_state.transition,
            "special_rule_active": regime_state.special_rule_active,
            "fuyun_prize_active": regime_state.fuyun_prize_active,
            "state_source": rule_state_source,
        },
        "physical_slip_plan": validated_slips,
        "tickets": sample_out.tickets,
        "nonce_hex": nonce.hex(),
    }
    commitment_sha256 = sha256_bytes(canonical_json(commitment_body))

    private_doc = {
        "seed_hex": seed.hex(),
        "nonce_hex": nonce.hex(),
        "commitment_body": commitment_body,
    }
    tickets_doc = {
        "target_issue": target_issue,
        "lottery": "SSQ",
        "system_version": SYSTEM_VERSION,
        "route_mode": ALLOWED_MODE,
        "algorithm_version": ALGORITHM_VERSION,
        "rng_model": RNG_MODEL_DECLARATION,
        "anti_sharing": "BLOCKED",
        "ticket_count": ticket_count,
        "budget_rmb": budget_rmb,
        "physical_slip_plan": validated_slips,
        "tickets": sample_out.tickets,
        "commitment_sha256": commitment_sha256,
        "frozen": True,
    }
    receipt_doc = {
        "system_version": SYSTEM_VERSION,
        "route_mode": ALLOWED_MODE,
        "algorithm_version": ALGORITHM_VERSION,
        "rng_model": RNG_MODEL_DECLARATION,
        "predictive_edge": "NO_VALIDATED_EDGE",
        "target_issue": target_issue,
        "draw_at_utc": draw_dt_utc.isoformat(),
        "generated_utc": generated_utc,
        "ticket_count": ticket_count,
        "budget_rmb": budget_rmb,
        "seed_sha256": seed_sha,
        "input_manifest_sha256": input_manifest_sha,
        "commitment_sha256": commitment_sha256,
        "quorum_result": {
            "status": "PASS",
            "agreed_sources": quorum.agreed_source_ids,
            "agreed_authorities": quorum.agreed_authorities,
            "agreed_domains": quorum.agreed_domains,
            "rejected_sources": quorum.rejected_sources,
            "latest_completed_issue": quorum.latest_draw["issue"],
            "evidence_hashes": quorum.evidence_hashes,
            "parser_versions": quorum.parser_versions,
        },
        "schedule_evidence": {
            "source_id": schedule_ev.source_id,
            "parser_version": schedule_ev.parser_version,
            "sha256": schedule_ev.sha256,
            "suspension_source_id": schedule_ev.suspension_source_id,
            "suspension_authority_id": schedule_ev.suspension_authority_id,
            "suspension_parser_version": schedule_ev.suspension_parser_version,
            "suspension_sha256": schedule_ev.suspension_sha256,
            "suspension_coverage": [
                schedule_ev.suspension_coverage_start_bjt,
                schedule_ev.suspension_coverage_end_bjt,
            ],
        },
        "promo_evidence": {
            "present": promo_ev.present,
            "source_id": promo_ev.source_id,
            "parser_version": promo_ev.parser_version,
            "sha256": promo_ev.sha256,
            "issue_window": [promo_ev.start_issue, promo_ev.end_issue],
            "status_source_id": promo_status_ev.source_id if promo_status_ev is not None else None,
            "status_authority_id": promo_status_ev.authority_id if promo_status_ev is not None else None,
            "status_parser_version": promo_status_ev.parser_version if promo_status_ev is not None else None,
            "status_sha256": promo_status_ev.sha256 if promo_status_ev is not None else None,
            "status_latest_completed_issue": (
                promo_status_ev.latest_completed_issue if promo_status_ev is not None else None
            ),
            "remaining_sixth_promo_rmb": promo_ev.remaining_sixth_promo_rmb,
        },
        "physical_slip_plan": validated_slips,
        "metrics": {
            "prize_coverage": sample_out.prize_coverage,
            "exact_red_4plus_union_count": sample_out.exact_red_4plus_union_count,
            "prize_coverage_note": sample_out.prize_coverage_note,
            "profit_guarantee": "NONE",
            "first_prize_space_share": sample_out.first_prize_space_share,
            "coverage_diagnostics": sample_out.coverage_diagnostics,
        },
        "draw_ev_context": ev_context,
        "rule_state_source": rule_state_source,
        "external_time_anchor": "LOCAL_FREEZE_ONLY",
    }

    # ---------- PHASE 1: STAGE ----------
    staging_dir.mkdir(parents=True, exist_ok=False)
    priv_sha = _write_file_durable(
        staging_dir / "PRIVATE_SEED_AND_NONCE.json",
        json.dumps(private_doc, indent=2, ensure_ascii=False).encode("utf-8"),
        mode=0o600,
    )
    tkt_sha = _write_file_durable(
        staging_dir / "TICKETS.json",
        json.dumps(tickets_doc, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    rcpt_sha = _write_file_durable(
        staging_dir / "RECEIPT.json",
        json.dumps(receipt_doc, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    stage_manifest = {
        "schema": "SSQ_V4_STAGE_MANIFEST_1.0",
        "target_issue": target_issue,
        "commitment_sha256": commitment_sha256,
        "files": {
            "PRIVATE_SEED_AND_NONCE.json": priv_sha,
            "TICKETS.json": tkt_sha,
            "RECEIPT.json": rcpt_sha,
        },
    }
    stage_manifest_sha = _write_file_durable(
        staging_dir / "STAGE_MANIFEST.json",
        canonical_json(stage_manifest),
    )

    # ---------- PHASE 2: HASH VERIFY ----------
    _verify_staged_directory(
        staging_dir=staging_dir,
        expected_commitment_sha256=commitment_sha256,
        expected_stage_manifest_sha256=stage_manifest_sha,
    )
    ok_chain, _ = ledger.verify_chain()
    if not ok_chain:
        raise GateBlockedError("LEDGER_CORRUPTED", "Pre-commit chain verification failed.")

    # ---------- PHASE 3: LEDGER COMMIT ----------
    ledger_record = ledger.commit_record(
        target_issue=target_issue,
        commitment_sha256=commitment_sha256,
        stage_manifest_sha256=stage_manifest_sha,
        meta={
            "route_mode": ALLOWED_MODE,
            "algorithm_version": ALGORITHM_VERSION,
            "ticket_count": ticket_count,
            "budget_rmb": budget_rmb,
            "agreed_authorities": quorum.agreed_authorities,
            "rule_regime": regime_state.rule_regime,
            "special_rule_active": regime_state.special_rule_active,
            "rule_state_source": rule_state_source,
            "generated_utc": generated_utc,
        },
    )

    if _inject_failure_after_ledger_commit is not None:
        _inject_failure_after_ledger_commit()

    # ---------- PHASE 4: ATOMIC PUBLISH ----------
    os.replace(staging_dir, final_dir)

    return {
        "receipt": receipt_doc,
        "ledger_record_sha256": ledger_record["record_sha256"],
        "stage_manifest_sha256": stage_manifest_sha,
        "published_dir": str(final_dir),
    }

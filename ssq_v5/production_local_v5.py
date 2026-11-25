"""
Hardened Local-First SSQ V5 production runner.

The Phase-3/Phase-4 model is unchanged. This module restores the production
safety shell around it: local schedule/time lock, append-only rule-state ledger,
physical-slip plan, strict OOS->CUSUM->Policy routing, canonical commitment,
four-stage durable transaction, duplicate prevention, and V4 transactional
fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from local_history import DEFAULT_HISTORY_PATH
from ssq_v5.offline_local_v5 import _to_records
from ssq_v5.digital_twin_v5 import (
    build_causal_context_from_history,
    generate_all_red_combinations_1based,
    run_expanding_window_oos_calibration,
    score_full_space_density,
)
from ssq_v5.liability_and_pockets_v5 import (
    RegimeRoutingPolicy,
    build_frozen_sparse_pool,
    sample_from_sparse_pockets,
)
from ssq_v5.phase4_real_policy_gate_v5 import (
    PHASE3_FROZEN_ARTIFACT,
    _combo_lex_index,
    evaluate_cusum_circuit_breaker,
)
from ssq_v5.policy_selector_v5 import AUXILIARY_OBJECTIVE
from ssq_v5.statutory_liability_v5 import (
    compute_exact_all_neighborhoods,
    compute_single_ticket_rmb_breakdown,
    compute_statutory_rmb_liability_surface,
    load_statutory_rule_table,
)
from v4.local_production_shell import (
    DEFAULT_GENESIS_PATH,
    DEFAULT_LEDGER_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SCHEDULE_PATH,
    GateBlockedError,
    canonical_json,
    commit_production_bundle,
    inherit_special_rule_state,
    local_promo_evidence,
    next_issue_and_draw,
    open_production_ledger,
    sha256_bytes,
    validate_and_classify_slips,
)

_ROOT = Path(__file__).resolve().parents[1]
_V4 = _ROOT / "v4_rc2"
if str(_V4) not in sys.path:
    sys.path.insert(0, str(_V4))

from rules_and_ev_v4 import evaluate_2026_rule_state_machine  # type: ignore
from sampler_v4 import (  # type: ignore
    ALGORITHM_VERSION as V4_ALGORITHM_VERSION,
    RNG_MODEL_DECLARATION,
    DomainSeparatedDRBG,
    compute_exact_batch_coverage,
    generate_v4_tickets,
)

POLICY_RESULT_PATH = _ROOT / "results" / "PHASE4_POLICY_GATE_RESULT.json"
RULE_PATH = _ROOT / "data" / "ssq_statutory_rule_table_v1.json"
PHASE3_RESULT_PATH = _ROOT / "results" / "PHASE3_LOCAL_FIRST_RESULT.json"
V4_FALLBACK_ROUTE = "UNIFORM_MARGINAL_BALANCED_COVERAGE"
V5_ROUTE_PROD = "ANTI_CROWD_SPARSE_ZONE_V1"
V5_ALGORITHM_VERSION = "SSQ_V5_LOCAL_FIRST_PHASE4_1.0"
SYSTEM_VERSION = "SSQ_V5_LOCAL_FIRST_HARDENED_1.0"
TRAINED_THROUGH_ISSUE = "2026135"


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_history_prefix(path: str | Path, trained_through_issue: str) -> str:
    raw = Path(path).read_bytes()
    lines = raw.splitlines(keepends=True)
    if not lines:
        raise RuntimeError("V5_POLICY_HISTORY_EMPTY")
    kept = [lines[0]]
    found = False
    for line in lines[1:]:
        issue = line.split(b",", 1)[0].decode("ascii", errors="strict").strip()
        kept.append(line)
        if issue == trained_through_issue:
            found = True
            break
    if not found:
        raise RuntimeError(f"V5_POLICY_TRAINED_ISSUE_MISSING:{trained_through_issue}")
    return hashlib.sha256(b"".join(kept)).hexdigest()


def _verify_policy_result(
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    result_path: str | Path = POLICY_RESULT_PATH,
    rule_path: str | Path = RULE_PATH,
) -> Dict[str, Any]:
    result = json.loads(Path(result_path).read_text("utf-8"))
    art = result["policy_gate_artifact"]
    if art["policy_gate_status"] != "PASS":
        raise RuntimeError("V5_POLICY_GATE_NOT_PASS")
    if int(art["outer_oos_draws"]) != 400 or float(art["outer_ci95"][0]) <= 0.0:
        raise RuntimeError("V5_POLICY_GATE_ARTIFACT_INVALID")
    trained_issue = str(art["trained_through_issue"])
    if trained_issue != TRAINED_THROUGH_ISSUE:
        raise RuntimeError("V5_POLICY_TRAINED_ISSUE_CHANGED")
    if _sha256_history_prefix(history_path, trained_issue) != art["history_csv_sha256"]:
        raise RuntimeError("V5_POLICY_HISTORY_PREFIX_HASH_MISMATCH")
    if _sha256_file(rule_path) != art["rule_document_sha256"]:
        raise RuntimeError("V5_POLICY_RULE_HASH_MISMATCH")
    if art["twin_artifact_sha256"] != PHASE3_FROZEN_ARTIFACT:
        raise RuntimeError("V5_POLICY_TWIN_ARTIFACT_MISMATCH")

    body = {
        "protocol_version": art["protocol_version"],
        "objective": art["objective"],
        "trained_through_issue": art["trained_through_issue"],
        "policy_gate_status": art["policy_gate_status"],
        "selected_policy": art["selected_policy"],
        "outer_oos_draws": art["outer_oos_draws"],
        "outer_mean_score": art["outer_mean_score"],
        "outer_ci95": art["outer_ci95"],
        "auxiliary_objective": AUXILIARY_OBJECTIVE,
        "auxiliary_mean_score": art["auxiliary_mean_score"],
        "auxiliary_ci95": art["auxiliary_ci95"],
        "candidate_selection_counts": art["candidate_selection_counts"],
        "protocol_hyperparameters": art["protocol_hyperparameters"],
        "history_csv_sha256": art["history_csv_sha256"],
        "twin_artifact_sha256": art["twin_artifact_sha256"],
        "rule_document_sha256": art["rule_document_sha256"],
        "git_commit_sha": art["git_commit_sha"],
        "frozen_candidate_grid": art["frozen_candidate_grid"],
        "oos_trace_sha256": art["oos_trace_sha256"],
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != art["integrity_summary_sha256"]:
        raise RuntimeError("V5_POLICY_INTEGRITY_SUMMARY_MISMATCH")
    return result


def validate_v5_production_artifacts(history_path: str | Path = DEFAULT_HISTORY_PATH) -> Dict[str, Any]:
    result = _verify_policy_result(history_path)
    art = result["policy_gate_artifact"]
    return {
        "phase3_artifact_sha256": PHASE3_FROZEN_ARTIFACT,
        "policy_gate_status": art["policy_gate_status"],
        "policy_integrity_summary_sha256": art["integrity_summary_sha256"],
        "selected_policy": art["selected_policy"],
        "trained_through_issue": art["trained_through_issue"],
        "training_history_prefix_sha256": art["history_csv_sha256"],
        "current_history_csv_sha256": _sha256_file(history_path),
        "rule_document_sha256": result["rule_document_sha256"],
    }


def _phase3_and_cusum(records: List[Any]) -> Tuple[Any, Any, List[Dict[str, Any]], Dict[str, Any]]:
    trained_pos = next((i for i, r in enumerate(records) if r.issue == TRAINED_THROUGH_ISSUE), None)
    if trained_pos is None:
        return None, None, [], {
            "phase3_oos_gate": "FAIL",
            "fallback_reason": f"V5_TRAINING_PREFIX_MISSING:{TRAINED_THROUGH_ISSUE}",
        }
    frozen_records = records[: trained_pos + 1]
    frozen_twin, frozen_trace = run_expanding_window_oos_calibration(frozen_records)
    if frozen_twin.oos_gate_status != "PASS" or frozen_twin.artifact_sha256 != PHASE3_FROZEN_ARTIFACT:
        return frozen_twin, frozen_twin, frozen_trace, {
            "phase3_oos_gate": "FAIL",
            "fallback_reason": "V5_OOS_GATE_LOCKED",
        }

    if len(records) > len(frozen_records):
        twin, trace = run_expanding_window_oos_calibration(records)
    else:
        twin, trace = frozen_twin, frozen_trace
    recent = [float(x["abs_log_err_combined"]) for x in trace]
    cb = evaluate_cusum_circuit_breaker(
        recent,
        float(frozen_twin.oos_metrics["oos_mean_abs_log_err"]),
    )
    new_oos_count = max(0, len(trace) - len(frozen_trace))
    return frozen_twin, twin, trace, {
        "phase3_oos_gate": "PASS",
        "cusum_drift_gate": cb.status,
        "circuit_breaker": cb.__dict__,
        "new_oos_error_count": new_oos_count,
        "latest_oos_abs_log_error": recent[-1] if recent else None,
        "fallback_reason": f"CIRCUIT_BREAKER_DOWNGRADE:{cb.reason}" if cb.tripped else None,
    }


def _v4_candidate(ticket_count: int, seed: bytes) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str, Dict[str, Any]]:
    out = generate_v4_tickets(ticket_count=ticket_count, drbg=DomainSeparatedDRBG(seed=seed))
    sparse_manifest = {
        "schema": "SSQ_V4_NO_SPARSE_POOL_1.0",
        "route_mode": V4_FALLBACK_ROUTE,
        "ticket_count": ticket_count,
        "pocket_counts": {"V4_BALANCED": ticket_count},
    }
    sparse_sha = sha256_bytes(canonical_json(sparse_manifest))
    metrics = {
        "prize_coverage": out.prize_coverage,
        "prize_coverage_note": out.prize_coverage_note,
        "profit_guarantee": "NONE",
        "exact_red_4plus_union_count": out.exact_red_4plus_union_count,
        "first_prize_space_share": out.first_prize_space_share,
        "coverage_diagnostics": out.coverage_diagnostics,
        "pocket_distribution": {"V4_BALANCED": ticket_count},
        "sparse_pool_sha256": sparse_sha,
    }
    return out.tickets, sparse_manifest, sparse_sha, metrics


def run_v5_local_production(
    ticket_count: int,
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    remaining_sixth_promo_rmb: float | None = None,
    physical_slip_plan: List[Dict[str, Any]] | None = None,
    schedule_path: str | Path = DEFAULT_SCHEDULE_PATH,
    genesis_path: str | Path = DEFAULT_GENESIS_PATH,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    now_utc: datetime | None = None,
    seed: bytes | None = None,
    _inject_failure_after_ledger_commit: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    if not isinstance(ticket_count, int) or ticket_count < 1:
        raise ValueError("ticket_count must be a positive integer.")
    if remaining_sixth_promo_rmb is not None and float(remaining_sixth_promo_rmb) < 0:
        raise GateBlockedError("INVALID_PROMO_REMAINING_RMB", str(remaining_sixth_promo_rmb))

    records = _to_records(history_path)
    latest = records[-1]
    timing = next_issue_and_draw(
        latest.issue,
        latest.draw_date_bjt,
        schedule_path=schedule_path,
        now_utc=now_utc,
    )
    ledger = open_production_ledger(ledger_path=ledger_path, genesis_path=genesis_path)
    previous_state, state_source = inherit_special_rule_state(
        ledger,
        latest.issue,
        genesis_path=genesis_path,
    )
    regime = evaluate_2026_rule_state_machine(
        pool_after_prev_draw_rmb=float(latest.pool_after_rmb),
        special_rule_active_previous=previous_state,
    )
    promo_active = bool(latest.promo_sixth_double)
    promo = local_promo_evidence(
        active=promo_active,
        remaining_sixth_promo_rmb=remaining_sixth_promo_rmb,
    )
    slips, ev_context = validate_and_classify_slips(
        physical_slip_plan=physical_slip_plan,
        ticket_count=ticket_count,
        promo=promo,
        pool_after_prev_draw_rmb=float(latest.pool_after_rmb),
        rule_state=regime,
    )

    # Lock 1 -> Lock 2: Phase-3 frozen prefix replay then live CUSUM.
    frozen_twin, twin, trace, lock_info = _phase3_and_cusum(records)
    route = V5_ROUTE_PROD
    fallback_reason = lock_info.get("fallback_reason")
    policy_result: Dict[str, Any] | None = None
    art: Dict[str, Any] | None = None

    if lock_info.get("phase3_oos_gate") != "PASS" or lock_info.get("cusum_drift_gate") not in (None, "OK"):
        route = V4_FALLBACK_ROUTE
    else:
        # Lock 3: Policy verification occurs only after OOS and CUSUM.
        try:
            policy_result = _verify_policy_result(history_path)
            art = policy_result["policy_gate_artifact"]
            lock_info["policy_gate"] = "PASS"
        except Exception as exc:
            route = V4_FALLBACK_ROUTE
            fallback_reason = f"V5_POLICY_GATE_LOCKED:{type(exc).__name__}:{exc}"
            lock_info["policy_gate"] = "FAIL"

    if route == V5_ROUTE_PROD and promo_active and remaining_sixth_promo_rmb is None:
        route = V4_FALLBACK_ROUTE
        fallback_reason = "LOCAL_PROMO_REMAINING_RMB_REQUIRED"

    seed = seed or os.urandom(32)
    policy_artifact_sha = (
        str(art["integrity_summary_sha256"])
        if art is not None
        else _sha256_file(POLICY_RESULT_PATH)
    )

    if route == V5_ROUTE_PROD:
        assert twin is not None and art is not None
        ctx = build_causal_context_from_history(
            records,
            timing["target_issue"],
            timing["target_draw_date_bjt"],
            sentiment=None,
        )
        combos = generate_all_red_combinations_1based()
        red_density, blue_density, weird = score_full_space_density(combos, ctx, twin)
        nb = compute_exact_all_neighborhoods(combos, red_density)
        rule = load_statutory_rule_table(RULE_PATH)
        red_exp, blue_exp = compute_statutory_rmb_liability_surface(
            nb=nb,
            blue_density=blue_density,
            weird_mask=weird,
            sales_rmb=float(latest.sales_rmb),
            pool_before_rmb=float(latest.pool_after_rmb),
            special_rule_active=bool(regime.special_rule_active),
            fuyun_prize_active=bool(regime.fuyun_prize_active),
            remaining_sixth_promo_rmb=float(remaining_sixth_promo_rmb or 0.0),
            rule=rule,
        )
        sel = art["selected_policy"]
        keep = 16 if ticket_count >= 16 else max(int(sel["blue_keep_count"]), ticket_count)
        policy = RegimeRoutingPolicy(
            regime_name=f"NESTED_OOS_VERIFIED:{sel['policy_id']}",
            red_percentile_low=float(sel["red_percentile_low"]),
            red_percentile_high=float(sel["red_percentile_high"]),
            blue_keep_count=keep,
        )
        pool = build_frozen_sparse_pool(combos, red_exp, blue_exp, policy)
        sampled = sample_from_sparse_pockets(
            combos_1based=combos,
            red_liability=red_exp,
            sparse_pool=pool,
            ticket_count=ticket_count,
            drbg=DomainSeparatedDRBG(seed=seed),
        )
        tickets: List[Dict[str, Any]] = []
        for t in sampled:
            idx = _combo_lex_index(t["reds"])
            tickets.append({
                **t,
                "plugin_statutory_rmb_breakdown": compute_single_ticket_rmb_breakdown(
                    nb=nb,
                    row_idx=idx,
                    blue_1based=int(t["blue"]),
                    blue_density=blue_density,
                    sales_rmb=float(latest.sales_rmb),
                    pool_before_rmb=float(latest.pool_after_rmb),
                    special_rule_active=bool(regime.special_rule_active),
                    fuyun_prize_active=bool(regime.fuyun_prize_active),
                    remaining_sixth_promo_rmb=float(remaining_sixth_promo_rmb or 0.0),
                    rule=rule,
                ),
            })
        reds = [t["reds"] for t in tickets]
        blues = [int(t["blue"]) for t in tickets]
        prize_cov, union_cnt, coverage_note = compute_exact_batch_coverage(reds, blues)
        pocket_distribution = dict(sorted(Counter(str(t["pocket_key"]) for t in tickets).items()))
        sparse_manifest = pool.manifest_dict
        sparse_sha = pool.manifest_sha256
        metrics = {
            "prize_coverage": round(float(prize_cov), 10),
            "prize_coverage_note": coverage_note,
            "profit_guarantee": "NONE",
            "exact_red_4plus_union_count": int(union_cnt),
            "pocket_distribution": pocket_distribution,
            "sparse_pool_sha256": sparse_sha,
        }
        algorithm_version = V5_ALGORITHM_VERSION
    else:
        tickets, sparse_manifest, sparse_sha, metrics = _v4_candidate(ticket_count, seed)
        algorithm_version = V4_ALGORITHM_VERSION

    input_manifest = {
        "history_csv_sha256": _sha256_file(history_path),
        "schedule_manifest_sha256": timing["schedule_manifest_sha256"],
        "ledger_genesis_sha256": _sha256_file(genesis_path),
        "latest_completed_issue": latest.issue,
        "latest_draw_date_bjt": latest.draw_date_bjt,
        "ticket_count": ticket_count,
        "budget_rmb": 2 * ticket_count,
        "remaining_sixth_promo_rmb": remaining_sixth_promo_rmb,
        "phase3_expected_artifact_sha256": PHASE3_FROZEN_ARTIFACT,
        "policy_result_file_sha256": _sha256_file(POLICY_RESULT_PATH),
    }
    seed_sha = hashlib.sha256(seed).hexdigest()
    rule_state = {
        "special_rule_active_previous": regime.special_rule_active_previous,
        "special_rule_active": regime.special_rule_active,
        "fuyun_prize_active": regime.fuyun_prize_active,
        "transition": regime.transition,
        "rule_regime": regime.rule_regime,
        "state_source": state_source,
    }
    cb_status = lock_info.get("circuit_breaker") or {"status": lock_info.get("cusum_drift_gate", "NOT_RUN")}
    commitment = {
        "schema": "SSQ_V5_COMMITMENT_1.0",
        "algorithm_version": algorithm_version,
        "rng_model": RNG_MODEL_DECLARATION,
        "input_manifest_sha256": sha256_bytes(canonical_json(input_manifest)),
        "twin_artifact_sha256": PHASE3_FROZEN_ARTIFACT,
        "policy_artifact_sha256": policy_artifact_sha,
        "sparse_pool_sha256": sparse_sha,
        "circuit_breaker_status": cb_status,
        "effective_route_mode": route,
        "rule_regime_state": rule_state,
        "physical_slip_plan": slips,
        "seed_sha256": seed_sha,
        "tickets": tickets,
        "target_issue": timing["target_issue"],
        "draw_at_utc": timing["draw_at_utc"],
    }
    receipt = {
        "schema": "SSQ_LOCAL_PRODUCTION_RECEIPT_1.0",
        "system_version": SYSTEM_VERSION,
        "effective_route_mode": route,
        "fallback_reason": fallback_reason,
        "target_issue": timing["target_issue"],
        "target_draw_date_bjt": timing["target_draw_date_bjt"],
        "draw_at_utc": timing["draw_at_utc"],
        "latest_completed_issue": latest.issue,
        "seed_sha256": seed_sha,
        "three_locks": {
            "phase3_oos_gate": lock_info.get("phase3_oos_gate", "FAIL"),
            "cusum_drift_gate": lock_info.get("cusum_drift_gate", "NOT_RUN"),
            "policy_gate": lock_info.get("policy_gate", "NOT_RUN"),
        },
        "circuit_breaker": lock_info.get("circuit_breaker"),
        "new_oos_error_count": lock_info.get("new_oos_error_count", 0),
        "latest_oos_abs_log_error": lock_info.get("latest_oos_abs_log_error"),
        "policy_id": art["selected_policy"]["policy_id"] if art is not None else None,
        "policy_artifact_sha256": policy_artifact_sha,
        "physical_slip_plan": slips,
        "promo_eligibility": ev_context["promo_eligibility"],
        "draw_ev_context": ev_context,
        "local_promo": {
            "active": promo.active_for_target_issue,
            "remaining_sixth_promo_rmb": promo.remaining_sixth_promo_rmb,
            "min_single_slip_rmb": promo.min_single_slip_rmb,
        },
        "rule_regime_state": rule_state,
        "schedule_manifest_sha256": timing["schedule_manifest_sha256"],
        "metrics": metrics,
    }
    tickets_doc = {
        "schema": "SSQ_LOCAL_TICKETS_1.0",
        "effective_route_mode": route,
        "fallback_reason": fallback_reason,
        "target_issue": timing["target_issue"],
        "tickets": tickets,
        "physical_slip_plan": slips,
        **metrics,
    }
    tx = commit_production_bundle(
        target_issue=timing["target_issue"],
        output_root=output_root,
        ledger=ledger,
        system_version=SYSTEM_VERSION,
        seed=seed,
        commitment_body=commitment,
        tickets_doc=tickets_doc,
        receipt_doc=receipt,
        sparse_pool_manifest=sparse_manifest,
        ledger_meta={
            "route_mode": route,
            "algorithm_version": algorithm_version,
            "ticket_count": ticket_count,
            "budget_rmb": 2 * ticket_count,
            "rule_regime": regime.rule_regime,
            "special_rule_active": regime.special_rule_active,
            "fuyun_prize_active": regime.fuyun_prize_active,
            "rule_state_source": state_source,
            "fallback_reason": fallback_reason,
            "generated_utc": (now_utc or datetime.now().astimezone()).isoformat(),
        },
        _inject_failure_after_ledger_commit=_inject_failure_after_ledger_commit,
    )
    return {
        **tx,
        "effective_route_mode": route,
        "fallback_reason": fallback_reason,
        "target_issue": timing["target_issue"],
        "target_draw_date_bjt": timing["target_draw_date_bjt"],
        "tickets": tickets,
        "metrics": metrics,
        "three_locks": receipt["three_locks"],
        "new_oos_error_count": receipt["new_oos_error_count"],
        "latest_oos_abs_log_error": receipt["latest_oos_abs_log_error"],
    }

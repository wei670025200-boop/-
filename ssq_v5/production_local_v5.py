"""
production_local_v5.py
Local-First production entrypoint for the Phase-4 verified V5 route.

No lottery-network access.
Production inputs are local files plus an optional local runtime value:
remaining_sixth_promo_rmb. When the latest ledger row says the sixth-prize
doubling promotion is active, an exact local remaining balance is required for
V5; otherwise the route fails closed to V4.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

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
    _next_draw_date,
    _next_issue,
    _regime_states,
    evaluate_cusum_circuit_breaker,
)
from ssq_v5.policy_selector_v5 import AUXILIARY_OBJECTIVE
from ssq_v5.statutory_liability_v5 import (
    compute_exact_all_neighborhoods,
    compute_single_ticket_rmb_breakdown,
    compute_statutory_rmb_liability_surface,
    load_statutory_rule_table,
)
from v4.offline_local_v4 import run_v4_local

import sys
_ROOT = Path(__file__).resolve().parents[1]
_V4 = _ROOT / "v4_rc2"
if str(_V4) not in sys.path:
    sys.path.insert(0, str(_V4))
from rules_and_ev_v4 import evaluate_2026_rule_state_machine
from sampler_v4 import DomainSeparatedDRBG

POLICY_RESULT_PATH = Path("results/PHASE4_POLICY_GATE_RESULT.json")
RULE_PATH = Path("data/ssq_statutory_rule_table_v1.json")
V4_FALLBACK_ROUTE = "UNIFORM_MARGINAL_BALANCED_COVERAGE"
V5_ROUTE_PROD = "ANTI_CROWD_SPARSE_ZONE_V1"


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_history_prefix(path: str | Path, trained_through_issue: str) -> str:
    """Hash the immutable raw CSV prefix through trained_through_issue, allowing later rows to append."""
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


def _load_and_verify_policy_result(
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    result_path: str | Path = POLICY_RESULT_PATH,
    rule_path: str | Path = RULE_PATH,
) -> Dict[str, Any]:
    result=json.loads(Path(result_path).read_text("utf-8"))
    art=result["policy_gate_artifact"]
    if art["policy_gate_status"]!="PASS":
        raise RuntimeError("V5_POLICY_GATE_NOT_PASS")
    if int(art["outer_oos_draws"])!=400 or float(art["outer_ci95"][0])<=0.0:
        raise RuntimeError("V5_POLICY_GATE_ARTIFACT_INVALID")

    trained_issue=str(art["trained_through_issue"])
    history_prefix_sha=_sha256_history_prefix(history_path, trained_issue)
    rule_sha=_sha256_file(rule_path)
    if history_prefix_sha!=art["history_csv_sha256"]:
        raise RuntimeError("V5_POLICY_HISTORY_PREFIX_HASH_MISMATCH")
    if rule_sha!=art["rule_document_sha256"]:
        raise RuntimeError("V5_POLICY_RULE_HASH_MISMATCH")
    if art["twin_artifact_sha256"]!=PHASE3_FROZEN_ARTIFACT:
        raise RuntimeError("V5_POLICY_TWIN_ARTIFACT_MISMATCH")

    body={
        "protocol_version":art["protocol_version"],
        "objective":art["objective"],
        "trained_through_issue":art["trained_through_issue"],
        "policy_gate_status":art["policy_gate_status"],
        "selected_policy":art["selected_policy"],
        "outer_oos_draws":art["outer_oos_draws"],
        "outer_mean_score":art["outer_mean_score"],
        "outer_ci95":art["outer_ci95"],
        "auxiliary_objective":AUXILIARY_OBJECTIVE,
        "auxiliary_mean_score":art["auxiliary_mean_score"],
        "auxiliary_ci95":art["auxiliary_ci95"],
        "candidate_selection_counts":art["candidate_selection_counts"],
        "protocol_hyperparameters":art["protocol_hyperparameters"],
        "history_csv_sha256":art["history_csv_sha256"],
        "twin_artifact_sha256":art["twin_artifact_sha256"],
        "rule_document_sha256":art["rule_document_sha256"],
        "git_commit_sha":art["git_commit_sha"],
        "frozen_candidate_grid":art["frozen_candidate_grid"],
        "oos_trace_sha256":art["oos_trace_sha256"],
    }
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if digest!=art["integrity_summary_sha256"]:
        raise RuntimeError("V5_POLICY_INTEGRITY_SUMMARY_MISMATCH")
    return result


def validate_v5_production_artifacts(
    history_path: str | Path = DEFAULT_HISTORY_PATH,
) -> Dict[str, Any]:
    result=_load_and_verify_policy_result(history_path)
    return {
        "phase3_artifact_sha256":PHASE3_FROZEN_ARTIFACT,
        "policy_gate_status":result["policy_gate_artifact"]["policy_gate_status"],
        "policy_integrity_summary_sha256":result["policy_gate_artifact"]["integrity_summary_sha256"],
        "selected_policy":result["policy_gate_artifact"]["selected_policy"],
        "trained_through_issue":result["policy_gate_artifact"]["trained_through_issue"],
        "training_history_prefix_sha256":result["policy_gate_artifact"]["history_csv_sha256"],
        "current_history_csv_sha256":_sha256_file(history_path),
        "rule_document_sha256":result["rule_document_sha256"],
    }


def run_v5_local_production(
    ticket_count: int,
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    remaining_sixth_promo_rmb: float | None = None,
    seed: bytes | None = None,
) -> Dict[str, Any]:
    if not isinstance(ticket_count,int) or ticket_count<1:
        raise ValueError("ticket_count must be a positive integer.")
    policy_result=_load_and_verify_policy_result(history_path)
    art=policy_result["policy_gate_artifact"]
    records=_to_records(history_path)

    trained_issue=str(art["trained_through_issue"])
    trained_pos=next((i for i,r in enumerate(records) if r.issue==trained_issue), None)
    if trained_pos is None:
        return {
            "effective_route":V4_FALLBACK_ROUTE,
            "fallback_reason":f"V5_TRAINING_PREFIX_MISSING:{trained_issue}",
            "v4":run_v4_local(ticket_count,history_path,seed=seed),
        }
    frozen_records=records[:trained_pos+1]
    frozen_twin,frozen_trace=run_expanding_window_oos_calibration(frozen_records)
    if frozen_twin.oos_gate_status!="PASS" or frozen_twin.artifact_sha256!=PHASE3_FROZEN_ARTIFACT:
        return {
            "effective_route":V4_FALLBACK_ROUTE,
            "fallback_reason":"V5_OOS_GATE_LOCKED",
            "v4":run_v4_local(ticket_count,history_path,seed=seed),
        }

    # Later completed draws are legitimate append-only updates under the same frozen algorithm.
    # They update the production fit and drift trace, but do not rewrite the frozen Phase-3 gate.
    if len(records)>len(frozen_records):
        twin,trace=run_expanding_window_oos_calibration(records)
    else:
        twin,trace=frozen_twin,frozen_trace

    recent=[float(x["abs_log_err_combined"]) for x in trace]
    cb=evaluate_cusum_circuit_breaker(recent,float(frozen_twin.oos_metrics["oos_mean_abs_log_err"]))
    if cb.tripped:
        return {
            "effective_route":V4_FALLBACK_ROUTE,
            "fallback_reason":f"CIRCUIT_BREAKER_DOWNGRADE:{cb.reason}",
            "circuit_breaker":cb.__dict__,
            "v4":run_v4_local(ticket_count,history_path,seed=seed),
        }

    if art["policy_gate_status"]!="PASS":
        return {
            "effective_route":V4_FALLBACK_ROUTE,
            "fallback_reason":f"V5_POLICY_GATE_LOCKED:{art['policy_gate_status']}",
            "v4":run_v4_local(ticket_count,history_path,seed=seed),
        }

    latest=records[-1]
    promo_active=bool(latest.promo_sixth_double)
    if promo_active and remaining_sixth_promo_rmb is None:
        return {
            "effective_route":V4_FALLBACK_ROUTE,
            "fallback_reason":"LOCAL_PROMO_REMAINING_RMB_REQUIRED",
            "v4":run_v4_local(ticket_count,history_path,seed=seed),
        }
    promo_remaining=max(0.0,float(remaining_sixth_promo_rmb or 0.0))

    target_issue=_next_issue(latest.issue)
    target_date=_next_draw_date(latest.draw_date_bjt)
    ctx=build_causal_context_from_history(records,target_issue,target_date,sentiment=None)
    combos=generate_all_red_combinations_1based()
    red_density,blue_density,weird=score_full_space_density(combos,ctx,twin)
    nb=compute_exact_all_neighborhoods(combos,red_density)

    regimes=_regime_states(records)
    state=evaluate_2026_rule_state_machine(
        pool_after_prev_draw_rmb=float(latest.pool_after_rmb),
        special_rule_active_previous=bool(regimes[-1][0]),
    )
    rule=load_statutory_rule_table(RULE_PATH)
    red_exp,blue_exp=compute_statutory_rmb_liability_surface(
        nb=nb,
        blue_density=blue_density,
        weird_mask=weird,
        sales_rmb=float(latest.sales_rmb),
        pool_before_rmb=float(latest.pool_after_rmb),
        special_rule_active=bool(state.special_rule_active),
        fuyun_prize_active=bool(state.fuyun_prize_active),
        remaining_sixth_promo_rmb=promo_remaining,
        rule=rule,
    )

    sel=art["selected_policy"]
    keep=16 if ticket_count>=16 else max(int(sel["blue_keep_count"]),ticket_count)
    policy=RegimeRoutingPolicy(
        regime_name=f"NESTED_OOS_VERIFIED:{sel['policy_id']}",
        red_percentile_low=float(sel["red_percentile_low"]),
        red_percentile_high=float(sel["red_percentile_high"]),
        blue_keep_count=keep,
    )
    pool=build_frozen_sparse_pool(combos,red_exp,blue_exp,policy)
    seed=seed or os.urandom(32)
    tickets=sample_from_sparse_pockets(
        combos_1based=combos,
        red_liability=red_exp,
        sparse_pool=pool,
        ticket_count=ticket_count,
        drbg=DomainSeparatedDRBG(seed=seed),
    )
    details=[]
    for t in tickets:
        idx=_combo_lex_index(t["reds"])
        details.append({
            **t,
            "plugin_statutory_rmb_breakdown":compute_single_ticket_rmb_breakdown(
                nb=nb,row_idx=idx,blue_1based=int(t["blue"]),blue_density=blue_density,
                sales_rmb=float(latest.sales_rmb),pool_before_rmb=float(latest.pool_after_rmb),
                special_rule_active=bool(state.special_rule_active),
                fuyun_prize_active=bool(state.fuyun_prize_active),
                remaining_sixth_promo_rmb=promo_remaining,rule=rule,
            ),
        })

    return {
        "effective_route":V5_ROUTE_PROD,
        "mode":"LOCAL_FIRST_OFFLINE",
        "target_issue":target_issue,
        "target_draw_date":target_date,
        "latest_completed_issue":latest.issue,
        "ticket_count":ticket_count,
        "seed_sha256":hashlib.sha256(seed).hexdigest(),
        "three_locks":{"phase3_oos_gate":"PASS","cusum_drift_gate":"OK","policy_gate":"PASS"},
        "policy_id":sel["policy_id"],
        "policy_integrity_summary_sha256":art["integrity_summary_sha256"],
        "candidate_blue_pool":list(pool.candidate_blues),
        "blue_unique":len({int(t["blue"]) for t in tickets})==ticket_count if ticket_count<=16 else None,
        "tickets":details,
        "promo_sixth_double":promo_active,
        "remaining_sixth_promo_rmb":promo_remaining,
    }

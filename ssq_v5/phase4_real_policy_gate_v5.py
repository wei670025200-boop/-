"""
phase4_real_policy_gate_v5.py
Single-run Local-First Phase-4 policy gate and offline E2E exercise.

No lottery network access. Inputs:
- data/ssq_history.csv
- results/PHASE3_LOCAL_FIRST_RESULT.json
- data/ssq_statutory_rule_table_v1.json
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from local_history import DEFAULT_HISTORY_PATH
from ssq_v5.offline_local_v5 import _to_records
from ssq_v5.db_and_sentiment_v5 import HistoricalDrawRecord
from ssq_v5.digital_twin_v5 import (
    BLUE_FEATURE_NAMES,
    P_TIER4,
    P_TIER5,
    P_TIER6,
    CalibratedTwinParameters,
    _compute_tier_targets,
    _fit_winsorized_ridge,
    build_causal_context_from_history,
    extract_blue_features_matrix,
    extract_red_features_matrix,
    generate_all_red_combinations_1based,
    run_expanding_window_oos_calibration,
    score_full_space_density,
)
from ssq_v5.liability_and_pockets_v5 import (
    RegimeRoutingPolicy,
    build_frozen_sparse_pool,
    sample_from_sparse_pockets,
)
from ssq_v5.policy_selector_v5 import (
    CausalDrawPolicyRecord,
    PolicyGateArtifact,
    run_nested_walk_forward_policy_selection,
)
from ssq_v5.statutory_liability_v5 import (
    compute_exact_all_neighborhoods,
    compute_single_ticket_rmb_breakdown,
    compute_statutory_rmb_liability_surface,
    load_statutory_rule_table,
)
_ROOT = Path(__file__).resolve().parents[1]
_V4 = _ROOT / "v4_rc2"
if str(_V4) not in sys.path:
    sys.path.insert(0, str(_V4))
from rules_and_ev_v4 import evaluate_2026_rule_state_machine
from sampler_v4 import DomainSeparatedDRBG

PHASE3_FROZEN_ARTIFACT = "2d8a9c0ce6e32d653311356f20d1329d9c2ed8f6c7c544899b5b6d6ccf0f957a"


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _combo_lex_index(reds: Sequence[int]) -> int:
    vals = tuple(int(x) for x in reds)
    if len(vals) != 6 or tuple(sorted(vals)) != vals:
        raise ValueError("reds must be six sorted values.")
    rank = 0
    prev = 0
    for i, a in enumerate(vals):
        for x in range(prev + 1, a):
            rank += math.comb(33 - x, 5 - i)
        prev = a
    return rank


def _next_issue(issue: str) -> str:
    y = int(issue[:4])
    n = int(issue[4:])
    return f"{y}{n+1:03d}"


def _next_draw_date(last_date: str) -> str:
    d = date.fromisoformat(last_date)
    # SSQ normal draw weekdays: Tue(1), Thu(3), Sun(6)
    for k in range(1, 8):
        cand = d + timedelta(days=k)
        if cand.weekday() in {1, 3, 6}:
            return cand.isoformat()
    raise RuntimeError("no next draw date")


@dataclass(frozen=True)
class CircuitBreakerDecision:
    tripped: bool
    status: str
    reason: str
    cusum_stat: float
    rolling_20_mae: float


def evaluate_cusum_circuit_breaker(
    recent_abs_log_errors: Sequence[float],
    baseline_oos_mae: float,
    window_size: int = 20,
    slack_k_ratio: float = 0.15,
    cusum_threshold_h_ratio: float = 3.5,
) -> CircuitBreakerDecision:
    if len(recent_abs_log_errors) < window_size:
        return CircuitBreakerDecision(
            tripped=False,
            status="OK",
            reason="WARMUP_INSUFFICIENT_WINDOW",
            cusum_stat=0.0,
            rolling_20_mae=round(float(np.mean(recent_abs_log_errors)), 6) if recent_abs_log_errors else 0.0,
        )
    win = list(recent_abs_log_errors[-window_size:])
    mu0 = max(float(baseline_oos_mae), 1e-4)
    k = slack_k_ratio * mu0
    h = cusum_threshold_h_ratio * mu0
    s = 0.0
    max_s = 0.0
    for err in win:
        s = max(0.0, s + (float(err) - mu0 - k))
        max_s = max(max_s, s)
    win_mae = float(np.mean(win))
    if max_s > h:
        return CircuitBreakerDecision(True, "V5_CIRCUIT_BREAKER_TRIPPED", f"CUSUM_DRIFT_EXCEEDED:{max_s:.4f}>{h:.4f}", round(max_s, 6), round(win_mae, 6))
    if win_mae > 1.35 * mu0:
        return CircuitBreakerDecision(True, "V5_CIRCUIT_BREAKER_TRIPPED", f"ROLLING_MAE_DEGRADED:{win_mae:.4f}>{1.35*mu0:.4f}", round(max_s, 6), round(win_mae, 6))
    return CircuitBreakerDecision(False, "OK", "WITHIN_DRIFT_BOUNDS", round(max_s, 6), round(win_mae, 6))


def _precompute_observed_arrays(records: Sequence[HistoricalDrawRecord]):
    n = len(records)
    x_red = np.zeros((n, 11), dtype=np.float64)
    x_blue = np.zeros((n, len(BLUE_FEATURE_NAMES)), dtype=np.float64)
    y_red = np.zeros(n, dtype=np.float64)
    y_blue = np.zeros(n, dtype=np.float64)
    blues = np.zeros(n, dtype=np.int32)
    for t in range(1, n):
        ctx = build_causal_context_from_history(
            history_before_t=records[:t],
            target_issue=records[t].issue,
            draw_date_bjt=records[t].draw_date_bjt,
            sentiment=None,
        )
        xr, _ = extract_red_features_matrix(np.asarray([records[t].reds], dtype=np.uint8), ctx)
        xb = extract_blue_features_matrix(ctx)
        x_red[t] = xr[0]
        x_blue[t] = xb[records[t].blue - 1]
        y_red[t], y_blue[t] = _compute_tier_targets(records[t])
        blues[t] = records[t].blue
    return x_red, x_blue, y_red, y_blue, blues


def _fit_snapshot(
    records: Sequence[HistoricalDrawRecord],
    t: int,
    x_red: np.ndarray,
    x_blue: np.ndarray,
    y_red: np.ndarray,
    y_blue: np.ndarray,
    blues: np.ndarray,
) -> CalibratedTwinParameters:
    tr = slice(1, t)
    r_b0, r_w = _fit_winsorized_ridge(x_red[tr], y_red[tr], l2_alpha=15.0)

    b_prior = float(np.mean(y_blue[tr]))
    eb = np.zeros(16, dtype=np.float64)
    for b in range(1, 17):
        mask = blues[tr] == b
        cnt = int(np.sum(mask))
        if cnt:
            eb[b - 1] = (cnt * (float(np.mean(y_blue[tr][mask])) - b_prior)) / (cnt + 20.0)
    resid = y_blue[tr] - eb[blues[tr] - 1]
    b_b0, b_w = _fit_winsorized_ridge(x_blue[tr], resid, l2_alpha=12.0)

    return CalibratedTwinParameters(
        model_version="SSQ_V5_DIGITAL_TWIN_1.0_CAUSAL_SNAPSHOT",
        trained_through_issue=records[t - 1].issue,
        training_draw_count=t,
        red_intercept=float(r_b0),
        red_weights=tuple(float(v) for v in r_w),
        blue_intercept=float(b_b0),
        blue_weights=tuple(float(v) for v in b_w),
        blue_static_shrinkage=tuple(float(v) for v in eb),
        oos_gate_status="CAUSAL_SNAPSHOT",
        oos_metrics={},
        artifact_sha256="",
    )


def _regime_states(records: Sequence[HistoricalDrawRecord]) -> List[Tuple[bool, bool]]:
    out: List[Tuple[bool, bool]] = [(False, False)] * len(records)
    prev_active = False
    for t in range(1, len(records)):
        if records[t].draw_date_bjt < "2026-02-01":
            out[t] = (False, False)
            continue
        state = evaluate_2026_rule_state_machine(
            pool_after_prev_draw_rmb=float(records[t - 1].pool_after_rmb),
            special_rule_active_previous=prev_active,
        )
        out[t] = (bool(state.special_rule_active), bool(state.fuyun_prize_active))
        prev_active = bool(state.special_rule_active)
    return out


def _realized_ratios(rec: HistoricalDrawRecord) -> Tuple[float, float]:
    n = rec.sales_tickets
    r4 = max(rec.tier4_count, 1) / (n * P_TIER4)
    r5 = max(rec.tier5_count, 1) / (n * P_TIER5)
    red = 0.5 * r4 + 0.5 * r5
    blue = max(rec.tier6_count, 1) / (n * P_TIER6)
    return float(red), float(blue)


def _policy_records(
    records: Sequence[HistoricalDrawRecord],
    rule,
    *,
    start_index: int = 100,
) -> List[CausalDrawPolicyRecord]:
    combos = generate_all_red_combinations_1based()
    x_red, x_blue, y_red, y_blue, blues = _precompute_observed_arrays(records)
    regimes = _regime_states(records)
    out: List[CausalDrawPolicyRecord] = []

    for t in range(start_index, len(records)):
        params = _fit_snapshot(records, t, x_red, x_blue, y_red, y_blue, blues)
        ctx = build_causal_context_from_history(
            history_before_t=records[:t],
            target_issue=records[t].issue,
            draw_date_bjt=records[t].draw_date_bjt,
            sentiment=None,
        )
        red_density, blue_density, weird = score_full_space_density(combos, ctx, params)
        nb = compute_exact_all_neighborhoods(combos, red_density)

        sales_proxy = float(records[t - 1].sales_rmb)
        pool_before = float(records[t - 1].pool_after_rmb)
        special, fuyun = regimes[t]
        promo_active = bool(records[t].promo_sixth_double)
        # Local ledger has only the active flag, not historical remaining balance.
        # Use a causal non-binding upper bound derived from previous-draw sales.
        promo_budget = (
            (sales_proxy / 2.0) * rule.promo_sixth_extra_rmb_per_ticket
            if promo_active else 0.0
        )
        red_exp, blue_exp = compute_statutory_rmb_liability_surface(
            nb=nb,
            blue_density=blue_density,
            weird_mask=weird,
            sales_rmb=sales_proxy,
            pool_before_rmb=pool_before,
            special_rule_active=special,
            fuyun_prize_active=fuyun,
            remaining_sixth_promo_rmb=promo_budget,
            rule=rule,
        )

        row_idx = _combo_lex_index(records[t].reds)
        actual = float(red_exp[row_idx])
        finite_mask = np.isfinite(red_exp)
        if not np.isfinite(actual):
            red_pct = 100.0
        else:
            red_pct = 100.0 * float(np.count_nonzero(finite_mask & (red_exp <= actual))) / float(np.count_nonzero(finite_mask))

        order = np.argsort(blue_exp)
        ranks = np.empty(16, dtype=np.int32)
        ranks[order] = np.arange(16, dtype=np.int32)
        blue_rank = int(ranks[records[t].blue - 1])
        red_ratio, blue_ratio = _realized_ratios(records[t])
        out.append(CausalDrawPolicyRecord(
            issue=records[t].issue,
            actual_red_liability_pct=red_pct,
            actual_blue_crowding_rank=blue_rank,
            realized_red_crowding_ratio=red_ratio,
            realized_blue_crowding_ratio=blue_ratio,
        ))
        if (t - start_index + 1) % 25 == 0:
            print(f"POLICY_RECORD_PROGRESS {t-start_index+1}/{len(records)-start_index} issue={records[t].issue}", flush=True)
    return out


def _current_e2e(
    records: Sequence[HistoricalDrawRecord],
    twin_params: CalibratedTwinParameters,
    phase3_trace: Sequence[Dict[str, Any]],
    policy_artifact: PolicyGateArtifact,
    rule,
) -> Dict[str, Any]:
    combos = generate_all_red_combinations_1based()
    target_issue = _next_issue(records[-1].issue)
    target_date = _next_draw_date(records[-1].draw_date_bjt)
    ctx = build_causal_context_from_history(records, target_issue, target_date, sentiment=None)
    red_density, blue_density, weird = score_full_space_density(combos, ctx, twin_params)
    nb = compute_exact_all_neighborhoods(combos, red_density)

    regimes = _regime_states(records)
    latest_special = regimes[-1][0]
    next_state = evaluate_2026_rule_state_machine(
        pool_after_prev_draw_rmb=float(records[-1].pool_after_rmb),
        special_rule_active_previous=latest_special,
    )
    promo_active = bool(records[-1].promo_sixth_double)
    promo_budget = (
        (float(records[-1].sales_rmb) / 2.0) * rule.promo_sixth_extra_rmb_per_ticket
        if promo_active else 0.0
    )

    red_exp, blue_exp = compute_statutory_rmb_liability_surface(
        nb=nb,
        blue_density=blue_density,
        weird_mask=weird,
        sales_rmb=float(records[-1].sales_rmb),
        pool_before_rmb=float(records[-1].pool_after_rmb),
        special_rule_active=bool(next_state.special_rule_active),
        fuyun_prize_active=bool(next_state.fuyun_prize_active),
        remaining_sixth_promo_rmb=promo_budget,
        rule=rule,
    )

    recent_errors = [float(x["abs_log_err_combined"]) for x in phase3_trace]
    cb = evaluate_cusum_circuit_breaker(
        recent_errors,
        baseline_oos_mae=float(twin_params.oos_metrics["oos_mean_abs_log_err"]),
    )
    locks = {
        "phase3_oos_gate": twin_params.oos_gate_status,
        "cusum_drift_gate": cb.status,
        "policy_gate": policy_artifact.policy_gate_status,
    }
    production_eligible = (
        twin_params.oos_gate_status == "PASS"
        and not cb.tripped
        and policy_artifact.policy_gate_status == "PASS"
    )

    sel = policy_artifact.selected_policy
    exercises: Dict[str, Any] = {}
    for ticket_count in (2, 8):
        keep = 16 if ticket_count >= 16 else max(int(sel.blue_keep_count), ticket_count)
        policy = RegimeRoutingPolicy(
            regime_name=f"NESTED_OOS_VERIFIED:{sel.policy_id}",
            red_percentile_low=float(sel.red_percentile_low),
            red_percentile_high=float(sel.red_percentile_high),
            blue_keep_count=keep,
        )
        pool = build_frozen_sparse_pool(combos, red_exp, blue_exp, policy)
        seed = hashlib.sha256(f"SSQ_V5_PHASE4_E2E|{target_issue}|{ticket_count}".encode()).digest()
        tickets = sample_from_sparse_pockets(
            combos_1based=combos,
            red_liability=red_exp,
            sparse_pool=pool,
            ticket_count=ticket_count,
            drbg=DomainSeparatedDRBG(seed=seed),
        )
        details = []
        for tkt in tickets:
            idx = _combo_lex_index(tkt["reds"])
            details.append({
                **tkt,
                "plugin_statutory_rmb_breakdown": compute_single_ticket_rmb_breakdown(
                    nb=nb,
                    row_idx=idx,
                    blue_1based=int(tkt["blue"]),
                    blue_density=blue_density,
                    sales_rmb=float(records[-1].sales_rmb),
                    pool_before_rmb=float(records[-1].pool_after_rmb),
                    special_rule_active=bool(next_state.special_rule_active),
                    fuyun_prize_active=bool(next_state.fuyun_prize_active),
                    remaining_sixth_promo_rmb=promo_budget,
                    rule=rule,
                ),
            })
        pocket_counts: Dict[str, int] = {}
        for tkt in tickets:
            pocket_counts[tkt["pocket_key"]] = pocket_counts.get(tkt["pocket_key"], 0) + 1
        exercises[str(ticket_count)] = {
            "route": "V5_PRODUCTION_ELIGIBLE" if production_eligible else "V5_SHADOW_ONLY_LOCKED",
            "ticket_count": ticket_count,
            "selected_policy_id": sel.policy_id,
            "candidate_blue_pool": list(pool.candidate_blues),
            "blue_values": [int(t["blue"]) for t in tickets],
            "blue_unique": len({int(t["blue"]) for t in tickets}) == ticket_count,
            "pocket_distribution": pocket_counts,
            "tickets": details,
        }

    return {
        "target_issue": target_issue,
        "target_draw_date": target_date,
        "locks": locks,
        "circuit_breaker": asdict(cb),
        "production_eligible": production_eligible,
        "special_rule_active": bool(next_state.special_rule_active),
        "fuyun_prize_active": bool(next_state.fuyun_prize_active),
        "promo_flag_carried_from_latest_local_row": promo_active,
        "promo_remaining_mode": "LOCAL_FLAG_ONLY_CAUSAL_NONBINDING_UPPER_BOUND_FROM_LATEST_SALES",
        "exercises": exercises,
    }


def run(
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    phase3_result_path: str | Path = "results/PHASE3_LOCAL_FIRST_RESULT.json",
    rule_path: str | Path = "data/ssq_statutory_rule_table_v1.json",
    output_path: str | Path = "results/PHASE4_POLICY_GATE_RESULT.json",
    *,
    git_commit_sha: str | None = None,
) -> Dict[str, Any]:
    records = _to_records(history_path)
    if len(records) != 600 or records[0].issue != "2022139" or records[-1].issue != "2026135":
        raise RuntimeError("REAL_GATE_REQUIRES_FROZEN_600_DRAW_LOCAL_WINDOW")

    frozen = json.loads(Path(phase3_result_path).read_text("utf-8"))
    if frozen["artifact_sha256"] != PHASE3_FROZEN_ARTIFACT or frozen["oos_gate_status"] != "PASS":
        raise RuntimeError("PHASE3_FROZEN_ARTIFACT_MISMATCH")

    twin_params, phase3_trace = run_expanding_window_oos_calibration(records)
    if twin_params.artifact_sha256 != PHASE3_FROZEN_ARTIFACT or twin_params.oos_gate_status != "PASS":
        raise RuntimeError("PHASE3_REPRODUCTION_FAILED")

    history_sha = _sha256_file(history_path)
    frozen_ledger_sha = str(frozen["ledger_sha256"])
    # The Local-First CI later normalized numeric text formatting, changing only CSV bytes.
    # Semantic equivalence is enforced by exact reproduction of the frozen Phase-3 artifact
    # on the current parsed 600-draw ledger above. Do not mutate/refit Phase-3 parameters.
    phase3_semantic_reproduction = (
        twin_params.artifact_sha256 == PHASE3_FROZEN_ARTIFACT
        and twin_params.oos_gate_status == "PASS"
    )
    if not phase3_semantic_reproduction:
        raise RuntimeError("PHASE3_SEMANTIC_REPRODUCTION_FAILED")

    rule = load_statutory_rule_table(rule_path)
    commit_sha = git_commit_sha or os.environ.get("GITHUB_SHA") or "LOCAL_UNKNOWN"

    policy_records = _policy_records(records, rule, start_index=100)
    policy_artifact, policy_trace = run_nested_walk_forward_policy_selection(
        policy_records,
        history_csv_sha256=history_sha,
        twin_artifact_sha256=twin_params.artifact_sha256,
        rule_document_sha256=rule.rule_document_sha256,
        git_commit_sha=commit_sha,
        inner_warmup_draws=100,
        min_outer_oos_draws=400,
    )

    policy_records_body = [asdict(x) for x in policy_records]
    policy_records_sha = hashlib.sha256(
        json.dumps(policy_records_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    result = {
        "schema": "SSQ_V5_PHASE4_REAL_POLICY_GATE_1.0",
        "execution_mode": "LOCAL_FIRST_OFFLINE",
        "history_draws": len(records),
        "history_range": [records[0].issue, records[-1].issue],
        "phase3_frozen_artifact_sha256": twin_params.artifact_sha256,
        "phase3_frozen_ledger_sha256": frozen_ledger_sha,
        "history_csv_sha256": history_sha,
        "phase3_semantic_reproduction": phase3_semantic_reproduction,
        "rule_document_sha256": rule.rule_document_sha256,
        "git_commit_sha": commit_sha,
        "policy_records_count": len(policy_records),
        "policy_records_sha256": policy_records_sha,
        "policy_gate_artifact": asdict(policy_artifact),
        "policy_trace": policy_trace,
        "historical_promo_remaining_mode": "LOCAL_FLAG_ONLY_CAUSAL_NONBINDING_UPPER_BOUND_FROM_PREVIOUS_SALES",
        "e2e": _current_e2e(records, twin_params, phase3_trace, policy_artifact, rule),
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    print("PHASE4_REAL_GATE_RESULT", json.dumps({
        "policy_gate_status": policy_artifact.policy_gate_status,
        "selected_policy_id": policy_artifact.selected_policy.policy_id,
        "outer_mean_score": policy_artifact.outer_mean_score,
        "outer_ci95": policy_artifact.outer_ci95,
        "auxiliary_mean_score": policy_artifact.auxiliary_mean_score,
        "auxiliary_ci95": policy_artifact.auxiliary_ci95,
        "candidate_selection_counts": policy_artifact.candidate_selection_counts,
        "integrity_summary_sha256": policy_artifact.integrity_summary_sha256,
        "production_eligible": result["e2e"]["production_eligible"],
    }, sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    run(git_commit_sha=os.environ.get("GITHUB_SHA"))

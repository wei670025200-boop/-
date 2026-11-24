"""
runner_v5.py
Phase 4:
1. 20期滑动窗口 CUSUM 盘面漂移熔断器
2. OOS 门禁硬锁 + 自动无感降级至 Phase 1 V4 均衡覆盖底座
3. 四阶段原子事务发布（STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH）
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

import numpy as np

# 允许与 ssq_v4_rc2 并列目录导入（实际部署可按统一包名调整）
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT / "ssq_v4_rc2") not in sys.path:
    sys.path.insert(0, str(_PARENT / "ssq_v4_rc2"))

from registry_and_parsers_v4 import (  # noqa: E402
    GateBlockedError,
    load_promo_evidence,
    load_schedule_manifest_evidence,
    sha256_bytes,
    verify_2_of_n_official_quorum,
)
from rules_and_ev_v4 import (  # noqa: E402
    build_draw_ev_context,
    evaluate_2026_rule_state_machine,
    validate_physical_slip_plan,
    verify_schedule_and_continuity,
)
from runner_v4 import (  # noqa: E402
    AppendOnlyLedger,
    _verify_staged_directory,
    _write_file_durable,
    canonical_json,
)
from sampler_v4 import (  # noqa: E402
    ALGORITHM_VERSION as V4_ALGORITHM_VERSION,
    RNG_MODEL_DECLARATION,
    DomainSeparatedDRBG,
    compute_exact_batch_coverage,
    generate_v4_tickets,
)

from db_and_sentiment_v5 import (  # noqa: E402
    HistoricalDrawRecord,
    build_verified_sentiment_snapshot,
)
from digital_twin_v5 import (  # noqa: E402
    CalibratedTwinParameters,
    build_causal_context_from_history,
    generate_all_red_combinations_1based,
    score_full_space_density,
)
from liability_and_pockets_v5 import (  # noqa: E402
    build_frozen_sparse_pool,
    compute_exact_neighborhood_densities,
    compute_red_liability_score,
    sample_from_sparse_pockets,
    select_regime_policy,
)

V5_SYSTEM_VERSION = "SSQ_V5_ANTI_CROWD_SPARSE_ZONE"
V5_ROUTE_PROD = "ANTI_CROWD_SPARSE_ZONE_V1"
V5_ROUTE_SHADOW = "V5_SHADOW_V4_EXECUTION"
V4_FALLBACK_ROUTE = "UNIFORM_MARGINAL_BALANCED_COVERAGE"


@dataclass(frozen=True)
class CircuitBreakerDecision:
    tripped: bool
    status: str  # "OK" | "V5_CIRCUIT_BREAKER_TRIPPED"
    reason: str
    cusum_stat: float
    rolling_20_mae: float


def evaluate_cusum_circuit_breaker(
    recent_abs_log_errors: Sequence[float],
    baseline_oos_mae: float,
    consecutive_sentiment_failures: int = 0,
    require_sentiment: bool = False,
    window_size: int = 20,
    slack_k_ratio: float = 0.15,
    cusum_threshold_h_ratio: float = 3.5,
) -> CircuitBreakerDecision:
    """
    单边上向 CUSUM 概念漂移熔断器：
    - 当近 20 期全奖级重构误差出现持续抬升（CUSUM > h）或均值超标 > 35%，立即熔断
    - 当开启舆情强依赖且连续 3 期采集失败，立即熔断
    """
    if require_sentiment and consecutive_sentiment_failures >= 3:
        return CircuitBreakerDecision(
            tripped=True,
            status="V5_CIRCUIT_BREAKER_TRIPPED",
            reason=f"SENTIMENT_FAILED_{consecutive_sentiment_failures}_CONSECUTIVE_DRAWS",
            cusum_stat=0.0,
            rolling_20_mae=0.0,
        )

    if len(recent_abs_log_errors) < window_size:
        return CircuitBreakerDecision(
            tripped=False,
            status="OK",
            reason="WARMUP_INSUFFICIENT_WINDOW",
            cusum_stat=0.0,
            rolling_20_mae=round(float(np.mean(recent_abs_log_errors)), 6) if recent_abs_log_errors else 0.0,
        )

    win = list(recent_abs_log_errors[-window_size:])
    mu0 = max(baseline_oos_mae, 1e-4)
    k = slack_k_ratio * mu0
    h = cusum_threshold_h_ratio * mu0

    s = 0.0
    max_s = 0.0
    for err in win:
        s = max(0.0, s + (err - mu0 - k))
        if s > max_s:
            max_s = s

    win_mae = float(np.mean(win))
    if max_s > h:
        return CircuitBreakerDecision(
            tripped=True,
            status="V5_CIRCUIT_BREAKER_TRIPPED",
            reason=f"CUSUM_DRIFT_EXCEEDED:{max_s:.4f}>{h:.4f}",
            cusum_stat=round(max_s, 6),
            rolling_20_mae=round(win_mae, 6),
        )
    if win_mae > 1.35 * mu0:
        return CircuitBreakerDecision(
            tripped=True,
            status="V5_CIRCUIT_BREAKER_TRIPPED",
            reason=f"ROLLING_MAE_DEGRADED:{win_mae:.4f}>{1.35 * mu0:.4f}",
            cusum_stat=round(max_s, 6),
            rolling_20_mae=round(win_mae, 6),
        )

    return CircuitBreakerDecision(
        tripped=False,
        status="OK",
        reason="WITHIN_DRIFT_BOUNDS",
        cusum_stat=round(max_s, 6),
        rolling_20_mae=round(win_mae, 6),
    )


def run_v5_production(
    input_data: Dict[str, Any],
    history_records: Sequence[HistoricalDrawRecord],
    twin_params: CalibratedTwinParameters,
    recent_oos_errors: Sequence[float],
    output_root: Path,
    ledger: AppendOnlyLedger,
    now_utc: datetime | None = None,
    combos_cache: np.ndarray | None = None,
    _inject_failure_after_ledger_commit: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    """
    V5 正式生产执行器（含影子模式、OOS 门禁锁、CUSUM 漂移自动降级 V4 与四阶段原子发布）。
    """
    requested_mode = str(input_data.get("mode", ""))
    if requested_mode not in {V5_ROUTE_PROD, V5_ROUTE_SHADOW}:
        raise GateBlockedError("INVALID_V5_MODE", f"Unsupported V5 mode: {requested_mode!r}")

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
        raise GateBlockedError("UNRECOVERED_STAGING_EXISTS", f"Recover .staging_{target_issue} first.")

    # 1. 继承 V4 全部硬门禁（2-of-N 原始字节仲裁、外部赛程、派奖证据、2026滞回状态机、物理分票计划）
    quorum = verify_2_of_n_official_quorum(input_data["official_sources"], min_quorum=2)
    schedule_ev = load_schedule_manifest_evidence(input_data["schedule_manifest"])
    promo_ev = load_promo_evidence(input_data.get("promo_evidence"), target_issue=target_issue)
    draw_dt_utc = verify_schedule_and_continuity(
        target_issue=target_issue,
        draw_at_utc_str=str(input_data["draw_at_utc"]),
        latest_completed_draw=quorum.latest_draw,
        schedule=schedule_ev,
        now_utc=now_utc,
    )
    if "special_rule_active_previous" not in input_data:
        raise GateBlockedError("MISSING_SPECIAL_RULE_PREV_STATE", "Missing special_rule_active_previous.")
    regime_state = evaluate_2026_rule_state_machine(
        pool_after_prev_draw_rmb=float(quorum.latest_draw["pool_after_rmb"]),
        special_rule_active_previous=input_data["special_rule_active_previous"],
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

    # 严格防泄露：历史记录末期必须 <= 最新官方已开奖期号
    if not history_records or history_records[-1].issue > quorum.latest_draw["issue"]:
        raise GateBlockedError(
            "HISTORY_LOOKAHEAD_VIOLATION",
            "history_records contains issue newer than verified latest_completed_draw.",
        )

    # 2. Phase 2 实时舆情证据验证
    draw_date_bjt = draw_dt_utc.astimezone(timezone(timedelta_hours := __import__("datetime").timedelta(hours=8))).strftime("%Y-%m-%d")
    sentiment = build_verified_sentiment_snapshot(
        entries=input_data.get("sentiment_entries", []),
        target_issue=target_issue,
        draw_date_bjt=draw_date_bjt,
        enforce_collection_window=bool(input_data.get("enforce_sentiment_window", True)),
    )

    # 3. 安全锁裁决：OOS 门禁 + CUSUM 漂移熔断
    cb = evaluate_cusum_circuit_breaker(
        recent_abs_log_errors=recent_oos_errors,
        baseline_oos_mae=float(twin_params.oos_metrics.get("oos_mean_abs_log_err", 0.15)),
        consecutive_sentiment_failures=int(input_data.get("consecutive_sentiment_failures", 0)),
        require_sentiment=bool(input_data.get("require_sentiment", False)),
    )

    fallback_reason: str | None = None
    if requested_mode == V5_ROUTE_SHADOW:
        effective_route = V4_FALLBACK_ROUTE
        fallback_reason = "SHADOW_MODE_EXPLICIT_V4_EXECUTION"
    elif twin_params.oos_gate_status != "PASS":
        effective_route = V4_FALLBACK_ROUTE
        fallback_reason = f"V5_OOS_GATE_LOCKED:{twin_params.oos_gate_status}"
    elif cb.tripped:
        effective_route = V4_FALLBACK_ROUTE
        fallback_reason = f"CIRCUIT_BREAKER_DOWNGRADE:{cb.reason}"
    else:
        effective_route = V5_ROUTE_PROD

    # 4. 始终运行数字孪生与稀疏池构建（即使在 Shadow/降级模式也留存诊断快照用于事后审计）
    combos = combos_cache if combos_cache is not None else generate_all_red_combinations_1based()
    ctx = build_causal_context_from_history(
        history_before_t=history_records,
        target_issue=target_issue,
        draw_date_bjt=draw_date_bjt,
        sentiment=sentiment if sentiment.status == "VERIFIED" else None,
    )
    red_density, blue_density, weird_mask = score_full_space_density(combos, ctx, twin_params)
    n5, n4 = compute_exact_neighborhood_densities(combos, red_density)
    red_liability = compute_red_liability_score(red_density, n5, n4, weird_mask)
    policy = select_regime_policy(
        pool_after_prev_rmb=float(quorum.latest_draw["pool_after_rmb"]),
        special_rule_active=regime_state.special_rule_active,
        promo_active=promo_ev.active_for_target_issue,
        ticket_count=ticket_count,
    )
    sparse_pool = build_frozen_sparse_pool(combos, red_liability, blue_density, policy)

    # 5. 按裁决路由执行 CSPRNG 抽样
    seed = os.urandom(32)
    nonce = os.urandom(32)
    seed_sha = sha256_bytes(seed)
    drbg = DomainSeparatedDRBG(seed=seed)

    if effective_route == V5_ROUTE_PROD:
        tickets = sample_from_sparse_pockets(
            combos_1based=combos,
            red_liability=red_liability,
            sparse_pool=sparse_pool,
            ticket_count=ticket_count,
            drbg=drbg,
        )
        red_sets = [t["reds"] for t in tickets]
        blues = [t["blue"] for t in tickets]
        prize_cov, union_cnt, cov_note = compute_exact_batch_coverage(red_sets, blues)
        algo_ver = "SSQ_V5_SPARSE_POCKET_ALGO_1.0"
        rng_decl = "CSPRNG_UNIFORM_WITHIN_FROZEN_SPARSE_POCKETS"
    else:
        v4_out = generate_v4_tickets(ticket_count=ticket_count, drbg=drbg)
        tickets = v4_out.tickets
        prize_cov = v4_out.prize_coverage
        union_cnt = v4_out.exact_red_4plus_union_count
        cov_note = v4_out.prize_coverage_note
        algo_ver = V4_ALGORITHM_VERSION
        rng_decl = RNG_MODEL_DECLARATION

    generated_utc = (now_utc or datetime.now(timezone.utc)).isoformat()
    input_manifest_sha = sha256_bytes(canonical_json(input_data))

    commitment_body = {
        "schema": "SSQ_V5_COMMITMENT_1.0",
        "system_version": V5_SYSTEM_VERSION,
        "requested_mode": requested_mode,
        "effective_route_mode": effective_route,
        "fallback_reason": fallback_reason,
        "algorithm_version": algo_ver,
        "rng_model": rng_decl,
        "issue": target_issue,
        "draw_at_utc": draw_dt_utc.isoformat(),
        "ticket_count": ticket_count,
        "budget_rmb": budget_rmb,
        "input_manifest_sha256": input_manifest_sha,
        "official_evidence_hashes": quorum.evidence_hashes,
        "schedule_manifest_sha256": schedule_ev.sha256,
        "promo_evidence_sha256": promo_ev.sha256,
        "sentiment_snapshot_sha256": sentiment.snapshot_sha256,
        "twin_artifact_sha256": twin_params.artifact_sha256,
        "sparse_pool_sha256": sparse_pool.manifest_sha256,
        "circuit_breaker_status": cb.status,
        "seed_sha256": seed_sha,
        "rule_regime_state": {
            "rule_regime": regime_state.rule_regime,
            "transition": regime_state.transition,
            "special_rule_active": regime_state.special_rule_active,
        },
        "physical_slip_plan": validated_slips,
        "tickets": tickets,
        "nonce_hex": nonce.hex(),
    }
    commitment_sha256 = sha256_bytes(canonical_json(commitment_body))

    private_doc = {"seed_hex": seed.hex(), "nonce_hex": nonce.hex(), "commitment_body": commitment_body}
    tickets_doc = {
        "target_issue": target_issue,
        "lottery": "SSQ",
        "system_version": V5_SYSTEM_VERSION,
        "effective_route_mode": effective_route,
        "fallback_reason": fallback_reason,
        "ticket_count": ticket_count,
        "budget_rmb": budget_rmb,
        "physical_slip_plan": validated_slips,
        "tickets": tickets,
        "commitment_sha256": commitment_sha256,
        "frozen": True,
    }
    receipt_doc = {
        "system_version": V5_SYSTEM_VERSION,
        "requested_mode": requested_mode,
        "effective_route_mode": effective_route,
        "fallback_reason": fallback_reason,
        "algorithm_version": algo_ver,
        "rng_model": rng_decl,
        "target_issue": target_issue,
        "draw_at_utc": draw_dt_utc.isoformat(),
        "generated_utc": generated_utc,
        "ticket_count": ticket_count,
        "budget_rmb": budget_rmb,
        "commitment_sha256": commitment_sha256,
        "quorum_result": {
            "agreed_sources": quorum.agreed_source_ids,
            "agreed_authorities": quorum.agreed_authorities,
            "latest_completed_issue": quorum.latest_draw["issue"],
        },
        "sentiment_evidence": {
            "status": sentiment.status,
            "agreed_channels": list(sentiment.agreed_channel_ids),
            "snapshot_sha256": sentiment.snapshot_sha256,
        },
        "digital_twin_evidence": {
            "model_version": twin_params.model_version,
            "oos_gate_status": twin_params.oos_gate_status,
            "artifact_sha256": twin_params.artifact_sha256,
            "sparse_pool_sha256": sparse_pool.manifest_sha256,
            "sparse_pool_summary": sparse_pool.manifest_dict,
        },
        "circuit_breaker": {
            "status": cb.status,
            "tripped": cb.tripped,
            "reason": cb.reason,
            "cusum_stat": cb.cusum_stat,
            "rolling_20_mae": cb.rolling_20_mae,
        },
        "metrics": {
            "prize_coverage": round(prize_cov, 10),
            "exact_red_4plus_union_count": union_cnt,
            "prize_coverage_note": cov_note,
            "profit_guarantee": "NONE",
            "first_prize_space_share": f"{ticket_count} / 17721088",
        },
        "draw_ev_context": ev_context,
    }

    # ---------- 四阶段原子事务：STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH ----------
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
    pool_sha = _write_file_durable(
        staging_dir / "SPARSE_POOL_MANIFEST.json",
        canonical_json(sparse_pool.manifest_dict),
    )

    stage_manifest = {
        "schema": "SSQ_V5_STAGE_MANIFEST_1.0",
        "target_issue": target_issue,
        "commitment_sha256": commitment_sha256,
        "files": {
            "PRIVATE_SEED_AND_NONCE.json": priv_sha,
            "TICKETS.json": tkt_sha,
            "RECEIPT.json": rcpt_sha,
            "SPARSE_POOL_MANIFEST.json": pool_sha,
        },
    }
    stage_manifest_sha = _write_file_durable(
        staging_dir / "STAGE_MANIFEST.json",
        canonical_json(stage_manifest),
    )

    _verify_staged_directory(
        staging_dir=staging_dir,
        expected_commitment_sha256=commitment_sha256,
        expected_stage_manifest_sha256=stage_manifest_sha,
    )

    ledger_record = ledger.commit_record(
        target_issue=target_issue,
        commitment_sha256=commitment_sha256,
        stage_manifest_sha256=stage_manifest_sha,
        meta={
            "effective_route_mode": effective_route,
            "fallback_reason": fallback_reason,
            "circuit_breaker_status": cb.status,
            "twin_artifact_sha256": twin_params.artifact_sha256,
            "sparse_pool_sha256": sparse_pool.manifest_sha256,
            "generated_utc": generated_utc,
        },
    )

    if _inject_failure_after_ledger_commit is not None:
        _inject_failure_after_ledger_commit()

    os.replace(staging_dir, final_dir)
    return {
        "receipt": receipt_doc,
        "ledger_record_sha256": ledger_record["record_sha256"],
        "published_dir": str(final_dir),
    }


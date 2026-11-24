"""
policy_selector_v5.py
Phase 4 nested walk-forward policy gate.

Protocol hyperparameters are preregistered, not claimed to be parameter-free:
- primary objective red/blue weights: 0.5 / 0.5
- frozen candidate grid: 9 policies
- moving-block bootstrap block size: 12
- bootstrap replicates: 2000
- deterministic bootstrap seed: 20261124
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

PRIMARY_OBJECTIVE = "ANTI_SHARING_CROWDING_SUPPRESSION"
AUXILIARY_OBJECTIVE = "SPARSE_CAPTURE_LIFT"

PROTOCOL_HYPERPARAMETERS: Dict[str, Any] = {
    "primary_red_weight": 0.5,
    "primary_blue_weight": 0.5,
    "bootstrap_block_size": 12,
    "bootstrap_replicates": 2000,
    "bootstrap_seed": 20261124,
    "policy_record_start_index": 100,
    "outer_oos_start_index": 200,
    "outer_oos_draws": 400,
}


@dataclass(frozen=True)
class PolicyCandidate:
    policy_id: str
    red_percentile_low: float
    red_percentile_high: float
    blue_keep_count: int

    @property
    def red_space_share(self) -> float:
        return (self.red_percentile_high - self.red_percentile_low) / 100.0

    @property
    def blue_space_share(self) -> float:
        return self.blue_keep_count / 16.0


FROZEN_POLICY_GRID: Tuple[PolicyCandidate, ...] = tuple(
    PolicyCandidate(
        policy_id=f"P_R{int(lo):02d}_{int(hi):03d}_B{b:02d}",
        red_percentile_low=lo,
        red_percentile_high=hi,
        blue_keep_count=b,
    )
    for lo, hi in ((1.0, 5.0), (1.0, 10.0), (1.0, 20.0))
    for b in (5, 6, 8)
)


@dataclass(frozen=True)
class CausalDrawPolicyRecord:
    issue: str
    actual_red_liability_pct: float
    actual_blue_crowding_rank: int
    realized_red_crowding_ratio: float
    realized_blue_crowding_ratio: float


def _membership_lift(cand: PolicyCandidate, rec: CausalDrawPolicyRecord) -> Tuple[float, float]:
    in_red = cand.red_percentile_low <= rec.actual_red_liability_pct <= cand.red_percentile_high
    in_blue = rec.actual_blue_crowding_rank < cand.blue_keep_count
    red_lift = (1.0 if in_red else 0.0) / cand.red_space_share - 1.0
    blue_lift = (1.0 if in_blue else 0.0) / cand.blue_space_share - 1.0
    return red_lift, blue_lift


def score_policy_on_draw(
    cand: PolicyCandidate,
    rec: CausalDrawPolicyRecord,
    objective: str,
) -> float:
    red_lift, blue_lift = _membership_lift(cand, rec)
    rw = float(PROTOCOL_HYPERPARAMETERS["primary_red_weight"])
    bw = float(PROTOCOL_HYPERPARAMETERS["primary_blue_weight"])

    if objective == AUXILIARY_OBJECTIVE:
        return rw * red_lift + bw * blue_lift
    if objective == PRIMARY_OBJECTIVE:
        red_cov = (1.0 - rec.realized_red_crowding_ratio) * red_lift
        blue_cov = (1.0 - rec.realized_blue_crowding_ratio) * blue_lift
        return rw * red_cov + bw * blue_cov
    raise ValueError(f"Unsupported objective: {objective!r}")


def moving_block_bootstrap_ci(
    values: np.ndarray,
    *,
    block_size: int | None = None,
    n_boot: int | None = None,
    seed: int | None = None,
) -> Tuple[float, float, float]:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1 or len(arr) == 0:
        raise ValueError("values must be non-empty 1D.")
    block_size = int(block_size or PROTOCOL_HYPERPARAMETERS["bootstrap_block_size"])
    n_boot = int(n_boot or PROTOCOL_HYPERPARAMETERS["bootstrap_replicates"])
    seed = int(seed or PROTOCOL_HYPERPARAMETERS["bootstrap_seed"])
    if block_size < 1 or n_boot < 100:
        raise ValueError("invalid bootstrap settings.")

    n = len(arr)
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_size))
    max_start = max(1, n - block_size + 1)
    means = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        starts = rng.integers(0, max_start, size=n_blocks)
        sample = np.concatenate([arr[s : s + block_size] for s in starts])[:n]
        means[b] = float(np.mean(sample))
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(np.mean(arr)), float(lo), float(hi)


@dataclass(frozen=True)
class PolicyGateArtifact:
    protocol_version: str
    objective: str
    trained_through_issue: str
    policy_gate_status: str
    selected_policy: PolicyCandidate
    outer_oos_draws: int
    outer_mean_score: float
    outer_ci95: Tuple[float, float]
    auxiliary_mean_score: float
    auxiliary_ci95: Tuple[float, float]
    candidate_selection_counts: Dict[str, int]
    protocol_hyperparameters: Dict[str, Any]
    history_csv_sha256: str
    twin_artifact_sha256: str
    rule_document_sha256: str
    git_commit_sha: str
    frozen_candidate_grid: Tuple[Dict[str, Any], ...]
    oos_trace_sha256: str
    integrity_summary_sha256: str


def run_nested_walk_forward_policy_selection(
    records: Sequence[CausalDrawPolicyRecord],
    *,
    history_csv_sha256: str,
    twin_artifact_sha256: str,
    rule_document_sha256: str,
    git_commit_sha: str,
    inner_warmup_draws: int = 100,
    min_outer_oos_draws: int = 400,
    candidates: Sequence[PolicyCandidate] = FROZEN_POLICY_GRID,
) -> Tuple[PolicyGateArtifact, List[Dict[str, Any]]]:
    """
    records must begin at the preregistered policy_record_start_index=100.
    With 500 records and inner_warmup_draws=100, the outer gate is exactly
    Phase-3 OOS draws t=200..599 (400 draws).
    """
    if len(records) < inner_warmup_draws + min_outer_oos_draws:
        raise ValueError(
            f"Need >= {inner_warmup_draws + min_outer_oos_draws} policy records, got {len(records)}."
        )
    if len(records) != inner_warmup_draws + min_outer_oos_draws:
        raise ValueError("REAL_POLICY_GATE_REQUIRES_EXACTLY_500_RECORDS_FOR_100_INNER_PLUS_400_OUTER.")
    if tuple(candidates) != FROZEN_POLICY_GRID:
        raise ValueError("Candidate grid differs from preregistered FROZEN_POLICY_GRID.")

    k = len(candidates)
    primary_mat = np.empty((len(records), k), dtype=np.float64)
    aux_mat = np.empty((len(records), k), dtype=np.float64)
    for t, rec in enumerate(records):
        for j, cand in enumerate(candidates):
            primary_mat[t, j] = score_policy_on_draw(cand, rec, PRIMARY_OBJECTIVE)
            aux_mat[t, j] = score_policy_on_draw(cand, rec, AUXILIARY_OBJECTIVE)

    primary_outer: List[float] = []
    aux_outer: List[float] = []
    selection_counts: Dict[str, int] = {c.policy_id: 0 for c in candidates}
    trace: List[Dict[str, Any]] = []

    for t in range(inner_warmup_draws, len(records)):
        inner_means = np.mean(primary_mat[:t], axis=0)
        best_j = int(np.argmax(inner_means))
        chosen = candidates[best_j]
        selection_counts[chosen.policy_id] += 1
        p = float(primary_mat[t, best_j])
        a = float(aux_mat[t, best_j])
        primary_outer.append(p)
        aux_outer.append(a)
        trace.append({
            "issue": records[t].issue,
            "selected_policy_id": chosen.policy_id,
            "primary_score": round(p, 8),
            "auxiliary_score": round(a, 8),
            "actual_red_liability_pct": round(records[t].actual_red_liability_pct, 8),
            "actual_blue_crowding_rank": int(records[t].actual_blue_crowding_rank),
            "realized_red_crowding_ratio": round(records[t].realized_red_crowding_ratio, 8),
            "realized_blue_crowding_ratio": round(records[t].realized_blue_crowding_ratio, 8),
        })

    primary_arr = np.asarray(primary_outer, dtype=np.float64)
    aux_arr = np.asarray(aux_outer, dtype=np.float64)
    p_mean, p_lo, p_hi = moving_block_bootstrap_ci(primary_arr)
    a_mean, a_lo, a_hi = moving_block_bootstrap_ci(aux_arr)

    gate_pass = len(primary_outer) == min_outer_oos_draws and p_mean > 0.0 and p_lo > 0.0
    gate_status = "PASS" if gate_pass else "REJECTED_POLICY_GATE_FAILED"

    final_j = int(np.argmax(np.mean(primary_mat, axis=0)))
    final_policy = candidates[final_j]
    trace_bytes = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    trace_sha = hashlib.sha256(trace_bytes).hexdigest()
    grid_body = tuple(asdict(c) for c in candidates)

    body = {
        "protocol_version": "SSQ_V5_NESTED_POLICY_GATE_2.0_LOCAL_FIRST",
        "objective": PRIMARY_OBJECTIVE,
        "trained_through_issue": records[-1].issue,
        "policy_gate_status": gate_status,
        "selected_policy": asdict(final_policy),
        "outer_oos_draws": len(primary_outer),
        "outer_mean_score": round(p_mean, 8),
        "outer_ci95": [round(p_lo, 8), round(p_hi, 8)],
        "auxiliary_objective": AUXILIARY_OBJECTIVE,
        "auxiliary_mean_score": round(a_mean, 8),
        "auxiliary_ci95": [round(a_lo, 8), round(a_hi, 8)],
        "candidate_selection_counts": selection_counts,
        "protocol_hyperparameters": PROTOCOL_HYPERPARAMETERS,
        "history_csv_sha256": history_csv_sha256,
        "twin_artifact_sha256": twin_artifact_sha256,
        "rule_document_sha256": rule_document_sha256,
        "git_commit_sha": git_commit_sha,
        "frozen_candidate_grid": grid_body,
        "oos_trace_sha256": trace_sha,
    }
    integrity_sha = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    artifact = PolicyGateArtifact(
        protocol_version=body["protocol_version"],
        objective=PRIMARY_OBJECTIVE,
        trained_through_issue=records[-1].issue,
        policy_gate_status=gate_status,
        selected_policy=final_policy,
        outer_oos_draws=len(primary_outer),
        outer_mean_score=body["outer_mean_score"],
        outer_ci95=(body["outer_ci95"][0], body["outer_ci95"][1]),
        auxiliary_mean_score=body["auxiliary_mean_score"],
        auxiliary_ci95=(body["auxiliary_ci95"][0], body["auxiliary_ci95"][1]),
        candidate_selection_counts=selection_counts,
        protocol_hyperparameters=dict(PROTOCOL_HYPERPARAMETERS),
        history_csv_sha256=history_csv_sha256,
        twin_artifact_sha256=twin_artifact_sha256,
        rule_document_sha256=rule_document_sha256,
        git_commit_sha=git_commit_sha,
        frozen_candidate_grid=grid_body,
        oos_trace_sha256=trace_sha,
        integrity_summary_sha256=integrity_sha,
    )
    return artifact, trace

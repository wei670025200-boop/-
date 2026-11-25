from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

from local_history import DEFAULT_HISTORY_PATH, latest_completed
from v4.local_production_shell import (
    DEFAULT_GENESIS_PATH,
    DEFAULT_LEDGER_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SCHEDULE_PATH,
    canonical_json,
    commit_production_bundle,
    default_physical_slip_plan,
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
    ALGORITHM_VERSION,
    RNG_MODEL_DECLARATION,
    DomainSeparatedDRBG,
    generate_v4_tickets,
)

SYSTEM_VERSION = "SSQ_V4_LOCAL_FIRST_HARDENED_1.0"
ROUTE_MODE = "UNIFORM_MARGINAL_BALANCED_COVERAGE"


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_v4_local(
    ticket_count: int,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    *,
    offline_local_mode: bool = True,
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
    """Hardened Local-First V4 production entry; no network inputs are accepted."""
    if offline_local_mode is not True:
        raise ValueError("Local-First entry requires offline_local_mode=True")
    if not isinstance(ticket_count, int) or ticket_count < 1:
        raise ValueError("ticket_count must be a positive integer.")

    latest = latest_completed(history_path)
    timing = next_issue_and_draw(
        latest.issue,
        latest.date,
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

    promo = local_promo_evidence(
        active=bool(latest.promo_sixth_double),
        remaining_sixth_promo_rmb=remaining_sixth_promo_rmb,
    )
    slips, ev_context = validate_and_classify_slips(
        physical_slip_plan=physical_slip_plan,
        ticket_count=ticket_count,
        promo=promo,
        pool_after_prev_draw_rmb=float(latest.pool_after_rmb),
        rule_state=regime,
    )

    seed = seed or os.urandom(32)
    sampled = generate_v4_tickets(
        ticket_count=ticket_count,
        drbg=DomainSeparatedDRBG(seed=seed),
    )
    sparse_manifest = {
        "schema": "SSQ_V4_NO_SPARSE_POOL_1.0",
        "route_mode": ROUTE_MODE,
        "ticket_count": ticket_count,
        "pocket_counts": {"V4_BALANCED": ticket_count},
    }
    sparse_sha = sha256_bytes(canonical_json(sparse_manifest))
    input_manifest = {
        "history_csv_sha256": _sha256_file(history_path),
        "schedule_manifest_sha256": timing["schedule_manifest_sha256"],
        "ledger_genesis_sha256": _sha256_file(genesis_path),
        "latest_completed_issue": latest.issue,
        "latest_draw_date": latest.date,
        "latest_pool_after_rmb": latest.pool_after_rmb,
        "ticket_count": ticket_count,
        "budget_rmb": 2 * ticket_count,
        "remaining_sixth_promo_rmb": remaining_sixth_promo_rmb,
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
    commitment = {
        "schema": "SSQ_V5_COMMITMENT_1.0",
        "algorithm_version": ALGORITHM_VERSION,
        "rng_model": RNG_MODEL_DECLARATION,
        "input_manifest_sha256": sha256_bytes(canonical_json(input_manifest)),
        "twin_artifact_sha256": "NOT_APPLICABLE_V4",
        "policy_artifact_sha256": "NOT_APPLICABLE_V4",
        "sparse_pool_sha256": sparse_sha,
        "circuit_breaker_status": "NOT_APPLICABLE_V4",
        "effective_route_mode": ROUTE_MODE,
        "rule_regime_state": rule_state,
        "physical_slip_plan": slips,
        "seed_sha256": seed_sha,
        "tickets": sampled.tickets,
        "target_issue": timing["target_issue"],
        "draw_at_utc": timing["draw_at_utc"],
    }
    pocket_distribution = {"V4_BALANCED": ticket_count}
    metrics = {
        "prize_coverage": sampled.prize_coverage,
        "prize_coverage_note": sampled.prize_coverage_note,
        "profit_guarantee": "NONE",
        "exact_red_4plus_union_count": sampled.exact_red_4plus_union_count,
        "first_prize_space_share": sampled.first_prize_space_share,
        "coverage_diagnostics": sampled.coverage_diagnostics,
        "pocket_distribution": pocket_distribution,
        "sparse_pool_sha256": sparse_sha,
    }
    receipt = {
        "schema": "SSQ_LOCAL_PRODUCTION_RECEIPT_1.0",
        "system_version": SYSTEM_VERSION,
        "effective_route_mode": ROUTE_MODE,
        "fallback_reason": None,
        "target_issue": timing["target_issue"],
        "target_draw_date_bjt": timing["target_draw_date_bjt"],
        "draw_at_utc": timing["draw_at_utc"],
        "latest_completed_issue": latest.issue,
        "seed_sha256": seed_sha,
        "physical_slip_plan": slips,
        "promo_eligibility": ev_context["promo_eligibility"],
        "draw_ev_context": ev_context,
        "local_promo": {
            "active": promo.active_for_target_issue,
            "remaining_sixth_promo_rmb": promo.remaining_sixth_promo_rmb,
            "min_single_slip_rmb": promo.min_single_slip_rmb,
        },
        "rule_regime_state": rule_state,
        "metrics": metrics,
    }
    tickets_doc = {
        "schema": "SSQ_LOCAL_TICKETS_1.0",
        "effective_route_mode": ROUTE_MODE,
        "target_issue": timing["target_issue"],
        "tickets": sampled.tickets,
        "physical_slip_plan": slips,
        **metrics,
    }
    return commit_production_bundle(
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
            "route_mode": ROUTE_MODE,
            "algorithm_version": ALGORITHM_VERSION,
            "ticket_count": ticket_count,
            "budget_rmb": 2 * ticket_count,
            "rule_regime": regime.rule_regime,
            "special_rule_active": regime.special_rule_active,
            "fuyun_prize_active": regime.fuyun_prize_active,
            "rule_state_source": state_source,
            "generated_utc": (now_utc or datetime.now().astimezone()).isoformat(),
        },
        _inject_failure_after_ledger_commit=_inject_failure_after_ledger_commit,
    )

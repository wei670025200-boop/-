"""
SSQ V5 Local-First production entrypoint.

Phase 3 OOS and Phase 4 nested policy gate are both frozen PASS.
No lottery-network access is used. If any of the three locks fails, or if an
active sixth-prize promotion lacks an exact local remaining balance, execution
fails closed to the frozen V4 Local-First route.
"""

from __future__ import annotations

from ssq_v5.offline_local_v5 import run_phase3_local
from ssq_v5.production_local_v5 import (
    V4_FALLBACK_ROUTE,
    V5_ROUTE_PROD,
    run_v5_local_production,
    validate_v5_production_artifacts,
)
from v4.local_production_shell import confirm_published_purchase, recover_staged_publish, open_production_ledger

ACTIVE_MODE = "LOCAL_FIRST_OFFLINE"
PHASE4_STATUS = "PASS_PRODUCTION_ELIGIBLE"
V5_POLICY_GATE_STATUS = "PASS"

run_v5 = run_v5_local_production

__all__ = [
    "ACTIVE_MODE",
    "PHASE4_STATUS",
    "V5_POLICY_GATE_STATUS",
    "V4_FALLBACK_ROUTE",
    "V5_ROUTE_PROD",
    "run_phase3_local",
    "validate_v5_production_artifacts",
    "run_v5_local_production",
    "confirm_published_purchase",
    "recover_staged_publish",
    "open_production_ledger",
    "run_v5",
]

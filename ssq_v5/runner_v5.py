"""
SSQ V5 Local-First entrypoint.

Phase 4 remains locked. The only active V5 operation is the frozen Phase-3
calibration/diagnostic path reading data/ssq_history.csv with no network access.
"""

from __future__ import annotations

from ssq_v5.offline_local_v5 import run_phase3_local

ACTIVE_MODE = "LOCAL_FIRST_OFFLINE"
PHASE4_STATUS = "LOCKED"

# Canonical V5 entrypoint while Phase 4 remains locked.
run_v5 = run_phase3_local

__all__ = ["ACTIVE_MODE", "PHASE4_STATUS", "run_phase3_local", "run_v5"]

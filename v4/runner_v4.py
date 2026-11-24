"""
SSQ V4 Local-First entrypoint.

Default execution is offline and reads only data/ssq_history.csv.
The frozen RC2.1 production implementation remains importable for audit/recovery,
but is no longer the default entrypoint on this branch.
"""

from __future__ import annotations

import sys
from pathlib import Path

ACTIVE_RELEASE = "SSQ_V4_RC2_1"
ACTIVE_MODE = "LOCAL_FIRST_OFFLINE"
ACTIVE_IMPLEMENTATION_DIR = (Path(__file__).resolve().parents[1] / "v4_rc2").resolve()

_impl = str(ACTIVE_IMPLEMENTATION_DIR)
if _impl not in sys.path:
    sys.path.insert(0, _impl)

from runner_v4 import (  # type: ignore  # noqa: E402,F401
    ALLOWED_MODE,
    SYSTEM_VERSION,
    AppendOnlyLedger,
    canonical_json,
    recover_staged_publish,
    run_v4_production,
)
from v4.offline_local_v4 import run_v4_local  # noqa: E402,F401

# Canonical Local-First entrypoint on this branch.
run_v4 = run_v4_local

__all__ = [
    "ACTIVE_RELEASE",
    "ACTIVE_MODE",
    "ACTIVE_IMPLEMENTATION_DIR",
    "ALLOWED_MODE",
    "SYSTEM_VERSION",
    "AppendOnlyLedger",
    "canonical_json",
    "recover_staged_publish",
    "run_v4_production",
    "run_v4_local",
    "run_v4",
]

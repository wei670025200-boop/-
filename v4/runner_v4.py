"""
SSQ V4 live production entrypoint.

ACTIVE RELEASE: V4-RC2.1
Validated implementation lives in ../v4_rc2/.
The previous draft implementation is preserved as v4/legacy_runner_draft.py
and MUST NOT be used for production generation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ACTIVE_RELEASE = "SSQ_V4_RC2_1"
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

__all__ = [
    "ACTIVE_RELEASE",
    "ACTIVE_IMPLEMENTATION_DIR",
    "ALLOWED_MODE",
    "SYSTEM_VERSION",
    "AppendOnlyLedger",
    "canonical_json",
    "recover_staged_publish",
    "run_v4_production",
]

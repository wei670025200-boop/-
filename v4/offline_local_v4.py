from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict

from local_history import DEFAULT_HISTORY_PATH, latest_completed
from v4_rc2.sampler_v4 import DomainSeparatedDRBG, generate_v4_tickets


def run_v4_local(
    ticket_count: int,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    *,
    offline_local_mode: bool = True,
    seed: bytes | None = None,
) -> Dict[str, Any]:
    """Local-First V4 entry. No URL, HTTP, HTML or source-quorum inputs exist in this path."""
    if offline_local_mode is not True:
        raise ValueError("Local-First entry requires offline_local_mode=True")
    latest = latest_completed(history_path)
    seed = seed or os.urandom(32)
    out = generate_v4_tickets(ticket_count=ticket_count, drbg=DomainSeparatedDRBG(seed=seed))
    return {
        "mode": "LOCAL_FIRST_OFFLINE",
        "latest_completed_issue": latest.issue,
        "latest_draw_date": latest.date,
        "latest_pool_after_rmb": latest.pool_after_rmb,
        "ticket_count": ticket_count,
        "seed_sha256": hashlib.sha256(seed).hexdigest(),
        "tickets": out.tickets,
        "prize_coverage": out.prize_coverage,
        "exact_red_4plus_union_count": out.exact_red_4plus_union_count,
        "coverage_diagnostics": out.coverage_diagnostics,
    }
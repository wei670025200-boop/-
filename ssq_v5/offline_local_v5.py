from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from local_history import DEFAULT_HISTORY_PATH, full_tier_history
from db_and_sentiment_v5 import HistoricalDrawRecord
from digital_twin_v5 import RED_FEATURE_NAMES, run_expanding_window_oos_calibration


def _to_records(history_path: str | Path) -> list[HistoricalDrawRecord]:
    rows = full_tier_history(history_path)
    return [HistoricalDrawRecord(
        issue=r.issue, draw_date_bjt=r.date, reds=r.reds, blue=r.blue,
        sales_rmb=r.sales_rmb, pool_after_rmb=r.pool_after_rmb,
        tier1_count=r.t1, tier2_count=r.t2, tier3_count=int(r.t3), tier4_count=int(r.t4),
        tier5_count=int(r.t5), tier6_count=int(r.t6), promo_sixth_double=bool(r.promo_sixth_double),
    ) for r in rows]


def run_phase3_local(
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    *,
    offline_local_mode: bool = True,
) -> Dict[str, Any]:
    """Run the frozen Phase-3 calibration using only the local CSV ledger."""
    if offline_local_mode is not True:
        raise ValueError("Local-First entry requires offline_local_mode=True")
    records = _to_records(history_path)
    params, trace = run_expanding_window_oos_calibration(records)
    return {
        "mode": "LOCAL_FIRST_OFFLINE",
        "draw_count": len(records),
        "first_issue": records[0].issue,
        "last_issue": records[-1].issue,
        "oos_gate_status": params.oos_gate_status,
        "oos_metrics": params.oos_metrics,
        "artifact_sha256": params.artifact_sha256,
        "red_feature_weights": dict(zip(RED_FEATURE_NAMES, params.red_weights)),
        "blue_static_shrinkage": {f"{i:02d}": v for i, v in enumerate(params.blue_static_shrinkage, 1)},
        "oos_trace": trace,
    }
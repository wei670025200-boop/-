from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ssq_v5.runner_v5 import run_v5


def _load_slip_plan(path: str | None):
    if not path:
        return None
    doc = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(doc, list):
        raise SystemExit("--slip-plan-json must contain a JSON list")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="SSQ V5 Local-First hardened producer")
    ap.add_argument("--tickets", type=int, required=True)
    ap.add_argument("--promo-remaining", type=float, default=None)
    ap.add_argument("--slip-plan-json", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now-utc", default=None, help="ISO-8601 test override; production normally omits this")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now_utc.replace("Z", "+00:00")) if args.now_utc else None
    kwargs = {
        "remaining_sixth_promo_rmb": args.promo_remaining,
        "physical_slip_plan": _load_slip_plan(args.slip_plan_json),
        "now_utc": now,
    }
    if args.dry_run:
        root = Path(tempfile.mkdtemp(prefix="ssq_v5_dryrun_"))
        kwargs["ledger_path"] = root / "production_ledger.jsonl"
        kwargs["output_root"] = root / "production_output"

    out = run_v5(args.tickets, **kwargs)
    printable = {
        "effective_route_mode": out["effective_route_mode"],
        "fallback_reason": out.get("fallback_reason"),
        "target_issue": out["target_issue"],
        "target_draw_date_bjt": out["target_draw_date_bjt"],
        "commitment_sha256": out["commitment_sha256"],
        "ledger_record_sha256": out["ledger_record_sha256"],
        "published_dir": out["published_dir"],
        "three_locks": out["three_locks"],
        "metrics": out["metrics"],
        "tickets": out["tickets"],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

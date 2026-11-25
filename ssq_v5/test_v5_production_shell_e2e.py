from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from ssq_v5.runner_v5 import run_v5, confirm_published_purchase
from v4.local_production_shell import (
    GateBlockedError,
    next_issue_and_draw,
    open_production_ledger,
    recover_staged_publish,
    validate_local_draw_date,
)
from v4.offline_local_v4 import run_v4_local

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "ssq_history.csv"
SCHEDULE = ROOT / "data" / "local_schedule_manifest.json"
GENESIS = ROOT / "data" / "production_ledger_genesis.json"


def _now(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _append_136(path: Path) -> None:
    with path.open("ab") as f:
        f.write(
            b"2026136,2026-11-26,3,9,14,18,25,31,6,410000000,1130000000,"
            b"8,166,1800,90000,1600000,12000000,1\n"
        )


def _frozen_2027_schedule(td: Path) -> Path:
    doc = json.loads(SCHEDULE.read_text("utf-8"))
    doc["years"]["2027"]["lottery_suspension_status"] = "FROZEN_OFFICIAL"
    doc["years"]["2027"]["source_document"] = "TEST_FIXTURE_ONLY"
    doc["years"]["2027"]["suspended_dates_bjt"] = []
    p = td / "schedule.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return p


class TestV5ProductionShellE2E(unittest.TestCase):
    def test_01_append_2026136_then_real_2026137_with_new_cusum_error(self) -> None:
        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            hist = td / "ssq_history.csv"
            hist.write_bytes(HISTORY.read_bytes())
            ledger = td / "ledger.jsonl"
            output = td / "out"

            first = run_v5(
                2,
                history_path=hist,
                remaining_sixth_promo_rmb=560_000_000.0,
                ledger_path=ledger,
                output_root=output,
                now_utc=_now("2026-11-25T00:00:00Z"),
                seed=bytes.fromhex("51" * 32),
            )
            self.assertEqual(first["target_issue"], "2026136")
            self.assertEqual(first["effective_route_mode"], "ANTI_CROWD_SPARSE_ZONE_V1")

            _append_136(hist)
            second = run_v5(
                2,
                history_path=hist,
                remaining_sixth_promo_rmb=550_000_000.0,
                ledger_path=ledger,
                output_root=output,
                now_utc=_now("2026-11-27T00:00:00Z"),
                seed=bytes.fromhex("52" * 32),
            )
            self.assertEqual(second["target_issue"], "2026137")
            self.assertEqual(second["effective_route_mode"], "ANTI_CROWD_SPARSE_ZONE_V1")
            self.assertEqual(second["new_oos_error_count"], 1)
            self.assertIsInstance(second["latest_oos_abs_log_error"], float)
            self.assertEqual(second["receipt"]["rule_regime_state"]["state_source"], "PREVIOUS_ISSUE_LEDGER")
            print("EDGE1_APPEND_CUSUM", json.dumps({
                "target_issue": second["target_issue"],
                "effective_route_mode": second["effective_route_mode"],
                "new_oos_error_count": second["new_oos_error_count"],
                "latest_oos_abs_log_error": second["latest_oos_abs_log_error"],
                "tickets": [t["formatted"] for t in second["tickets"]],
            }, ensure_ascii=False, sort_keys=True))

    def test_02_missing_promo_fallback_is_transactional_and_negative_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            ledger = td / "ledger.jsonl"
            output = td / "out"
            out = run_v5(
                2,
                remaining_sixth_promo_rmb=None,
                ledger_path=ledger,
                output_root=output,
                now_utc=_now("2026-11-25T00:00:00Z"),
                seed=bytes.fromhex("61" * 32),
            )
            self.assertEqual(out["effective_route_mode"], "UNIFORM_MARGINAL_BALANCED_COVERAGE")
            self.assertEqual(out["fallback_reason"], "LOCAL_PROMO_REMAINING_RMB_REQUIRED")
            pub = Path(out["published_dir"])
            expected = {
                "PRIVATE_SEED_AND_NONCE.json",
                "TICKETS.json",
                "RECEIPT.json",
                "SPARSE_POOL_MANIFEST.json",
                "STAGE_MANIFEST.json",
            }
            self.assertEqual({p.name for p in pub.iterdir()}, expected)
            self.assertEqual(stat.S_IMODE((pub / "PRIVATE_SEED_AND_NONCE.json").stat().st_mode), 0o600)
            private = json.loads((pub / "PRIVATE_SEED_AND_NONCE.json").read_text("utf-8"))
            required = {
                "algorithm_version","rng_model","input_manifest_sha256","twin_artifact_sha256",
                "policy_artifact_sha256","sparse_pool_sha256","circuit_breaker_status",
                "effective_route_mode","rule_regime_state","physical_slip_plan","seed_sha256","tickets"
            }
            self.assertTrue(required.issubset(private["commitment_body"]))
            self.assertEqual(out["receipt"]["promo_eligibility"], "PLANNED_INELIGIBLE")
            led = open_production_ledger(ledger, GENESIS)
            self.assertTrue(led.verify_chain()[0])

        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            with self.assertRaises(GateBlockedError) as cm:
                run_v5(
                    2,
                    remaining_sixth_promo_rmb=-1.0,
                    ledger_path=td / "ledger.jsonl",
                    output_root=td / "out",
                    now_utc=_now("2026-11-25T00:00:00Z"),
                )
            self.assertEqual(cm.exception.code, "INVALID_PROMO_REMAINING_RMB")
            # All three model locks must fail closed into the same transactional V4 route.
            gate_cases = [
                (
                    {"phase3_oos_gate":"FAIL","cusum_drift_gate":"NOT_RUN","policy_gate":"NOT_RUN",
                     "fallback_reason":"V5_OOS_GATE_LOCKED"},
                    None,
                    "V5_OOS_GATE_LOCKED",
                ),
                (
                    {"phase3_oos_gate":"PASS","cusum_drift_gate":"V5_CIRCUIT_BREAKER_TRIPPED","policy_gate":"NOT_RUN",
                     "circuit_breaker":{"status":"V5_CIRCUIT_BREAKER_TRIPPED"},
                     "fallback_reason":"CIRCUIT_BREAKER_DOWNGRADE:TEST"},
                    None,
                    "CIRCUIT_BREAKER_DOWNGRADE:TEST",
                ),
                (
                    {"phase3_oos_gate":"PASS","cusum_drift_gate":"OK","policy_gate":"NOT_RUN",
                     "fallback_reason":None},
                    RuntimeError("BROKEN_POLICY_ARTIFACT"),
                    "V5_POLICY_GATE_LOCKED",
                ),
            ]
            routed = []
            for idx,(lock_info,policy_exc,expected_reason) in enumerate(gate_cases):
                with tempfile.TemporaryDirectory() as gtd_s:
                    gtd=Path(gtd_s)
                    fake_phase=(None,None,[],lock_info)
                    with patch("ssq_v5.production_local_v5._phase3_and_cusum", return_value=fake_phase):
                        if policy_exc is None:
                            ctx=patch("ssq_v5.production_local_v5._verify_policy_result")
                            mocked=ctx.start()
                            mocked.return_value={"policy_gate_artifact":{"policy_gate_status":"PASS","integrity_summary_sha256":"f"*64}}
                        else:
                            ctx=patch("ssq_v5.production_local_v5._verify_policy_result", side_effect=policy_exc)
                            ctx.start()
                        try:
                            gout=run_v5(
                                2,
                                remaining_sixth_promo_rmb=560_000_000.0,
                                ledger_path=gtd/"ledger.jsonl",
                                output_root=gtd/"out",
                                now_utc=_now("2026-11-25T00:00:00Z"),
                                seed=bytes([90+idx])*32,
                            )
                        finally:
                            ctx.stop()
                    self.assertEqual(gout["effective_route_mode"], "UNIFORM_MARGINAL_BALANCED_COVERAGE")
                    self.assertIn(expected_reason, str(gout["fallback_reason"]))
                    routed.append(gout["fallback_reason"])

            print("EDGE2_PROMO_FAILCLOSED", json.dumps({
                "effective_route_mode": out["effective_route_mode"],
                "fallback_reason": out["fallback_reason"],
                "negative_error": cm.exception.code,
                "gate_fallbacks": routed,
            }, sort_keys=True))

    def test_03_cross_year_suspension_and_expired_time_gates(self) -> None:
        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            sched = _frozen_2027_schedule(td)
            cross = next_issue_and_draw(
                "2026152",
                "2026-12-31",
                schedule_path=sched,
                now_utc=_now("2027-01-02T00:00:00Z"),
            )
            self.assertEqual(cross["target_issue"], "2027001")
            self.assertEqual(cross["target_draw_date_bjt"], "2027-01-03")

        with self.assertRaises(GateBlockedError) as cm_s:
            validate_local_draw_date("2026-02-15", SCHEDULE)
        self.assertEqual(cm_s.exception.code, "DRAW_DATE_SUSPENDED")

        with self.assertRaises(GateBlockedError) as cm_t:
            next_issue_and_draw(
                "2026135",
                "2026-11-24",
                schedule_path=SCHEDULE,
                now_utc=_now("2026-11-26T13:15:00Z"),
            )
        self.assertEqual(cm_t.exception.code, "DRAW_ALREADY_STARTED_OR_PASSED")
        print("EDGE3_SCHEDULE", json.dumps({
            "cross_year": cross,
            "suspension_error": cm_s.exception.code,
            "expired_error": cm_t.exception.code,
        }, sort_keys=True))

    def test_04_duplicate_and_crash_recovery_without_redraw(self) -> None:
        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            ledger = td / "ledger.jsonl"
            output = td / "out"
            first = run_v4_local(
                2,
                ledger_path=ledger,
                output_root=output,
                now_utc=_now("2026-11-25T00:00:00Z"),
                seed=bytes.fromhex("71" * 32),
            )
            with self.assertRaises(GateBlockedError) as cm:
                run_v4_local(
                    2,
                    ledger_path=ledger,
                    output_root=output,
                    now_utc=_now("2026-11-25T00:01:00Z"),
                    seed=bytes.fromhex("72" * 32),
                )
            self.assertEqual(cm.exception.code, "REFUSE_DUPLICATE_ISSUE")

        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            ledger_path = td / "ledger.jsonl"
            output = td / "out"
            def boom() -> None:
                raise RuntimeError("INJECTED_AFTER_LEDGER_COMMIT")
            with self.assertRaisesRegex(RuntimeError, "INJECTED_AFTER_LEDGER_COMMIT"):
                run_v4_local(
                    2,
                    ledger_path=ledger_path,
                    output_root=output,
                    now_utc=_now("2026-11-25T00:00:00Z"),
                    seed=bytes.fromhex("73" * 32),
                    _inject_failure_after_ledger_commit=boom,
                )
            ledger_obj = open_production_ledger(ledger_path, GENESIS)
            rec = ledger_obj.find_issue_record("2026136")
            self.assertIsNotNone(rec)
            recovery = recover_staged_publish("2026136", output, ledger_obj)
            self.assertEqual(recovery["recovery_action"], "COMPLETED_ATOMIC_PUBLISH_NO_REDRAW")
            final = Path(recovery["final_dir"])
            private = json.loads((final / "PRIVATE_SEED_AND_NONCE.json").read_text("utf-8"))
            import hashlib
            from v4.local_production_shell import canonical_json
            commitment = hashlib.sha256(canonical_json(private["commitment_body"])).hexdigest()
            self.assertEqual(commitment, rec["commitment_sha256"])
            print("EDGE4_RECOVERY", json.dumps({
                "duplicate_error": cm.exception.code,
                "recovery_action": recovery["recovery_action"],
                "commitment_sha256": commitment,
            }, sort_keys=True))

    def test_05_2_8_16_tickets_blue_uniqueness_coverage_and_pockets(self) -> None:
        rows = []
        for n, byte in ((2, "81"), (8, "82"), (16, "83")):
            with tempfile.TemporaryDirectory() as td_s:
                td = Path(td_s)
                out = run_v5(
                    n,
                    remaining_sixth_promo_rmb=560_000_000.0,
                    ledger_path=td / "ledger.jsonl",
                    output_root=td / "out",
                    now_utc=_now("2026-11-25T00:00:00Z"),
                    seed=bytes.fromhex(byte * 32),
                )
                blues = [int(t["blue"]) for t in out["tickets"]]
                self.assertEqual(len(set(blues)), n)
                pockets = out["metrics"]["pocket_distribution"]
                self.assertEqual(sum(pockets.values()), n)
                self.assertEqual(len(pockets), n)
                self.assertEqual(out["metrics"]["profit_guarantee"], "NONE")
                if n == 16:
                    self.assertEqual(out["metrics"]["prize_coverage"], 1.0)
                if n == 16:
                    actual = []
                    for slip in out["receipt"]["physical_slip_plan"]:
                        actual.append({
                            "slip_id": slip["slip_id"],
                            "printed_amount_rmb": slip["amount_rmb"],
                            "printed_bet_type": slip["bet_type"],
                            "terminal_receipt_sha256": "a" * 64,
                        })
                    confirmed = confirm_published_purchase(out["published_dir"], actual)
                    self.assertEqual(confirmed["promo_eligibility"], "RECEIPT_CONFIRMED_PARTIAL_ELIGIBLE")
                    self.assertEqual(out["receipt"]["promo_eligibility"], "PLANNED_PARTIAL_ELIGIBLE")
                rows.append({
                    "tickets": n,
                    "blues": blues,
                    "prize_coverage": out["metrics"]["prize_coverage"],
                    "profit_guarantee": out["metrics"]["profit_guarantee"],
                    "pockets": len(pockets),
                })
        print("EDGE5_MULTI_TICKET", json.dumps(rows, sort_keys=True))


if __name__ == "__main__":
    unittest.main()

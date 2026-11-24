"""
test_v4_regression.py
P1-B10: 42 项 V4-RC2 完整回归测试套件
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from registry_and_parsers_v4 import (
    OFFICIAL_SOURCE_REGISTRY,
    GateBlockedError,
    bind_schedule_suspension,
    load_promo_evidence,
    load_schedule_manifest_evidence,
    load_suspension_evidence,
    verify_2_of_n_official_quorum,
)
from rules_and_ev_v4 import (
    build_draw_ev_context,
    confirm_physical_slips_post_purchase,
    evaluate_2026_rule_state_machine,
    validate_physical_slip_plan,
    verify_schedule_and_continuity,
)
from runner_v4 import AppendOnlyLedger, recover_staged_publish, run_v4_production
from sampler_v4 import (
    RNG_MODEL_DECLARATION,
    DomainSeparatedDRBG,
    exact_red_4plus_union_count,
    generate_v4_tickets,
)

V3_HEAD = "0b44cd1e63caec4b9a95994f9655ecdd0673df47b1d5975047b8d5d247904a4f"


def _write_json(path: Path, obj: Dict[str, Any]) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class V4TestFixtureMixin:
    def _build_env(
        self,
        root: Path,
        *,
        prev_issue: str = "2026152",
        prev_date: str = "2026-12-31",
        target_issue: str = "2027001",
        draw_at_utc: str = "2027-01-03T13:15:00Z",
        pool_after: float = 1_245_000_000.0,
        special_prev: bool = False,
        ticket_count: int = 2,
        slip_plan: List[Dict[str, Any]] | None = None,
        suspended: List[str] | None = None,
        include_promo: bool = True,
        promo_min_slip: int = 20,
        promo_end: str = "2027020",
    ) -> Dict[str, Any]:
        draw_doc = {
            "latest_draw": {
                "issue": prev_issue,
                "draw_date_bjt": prev_date,
                "reds": [2, 8, 14, 21, 28, 32],
                "blue": 6,
                "pool_after_rmb": pool_after,
                "sales_rmb": 380_000_000.0,
            }
        }
        sh_path, sc_path = root / "sh.json", root / "sc.json"
        sh_sha = _write_json(sh_path, draw_doc)
        sc_sha = _write_json(sc_path, draw_doc)

        sched_path = root / "sched.json"
        sched_sha = _write_json(
            sched_path,
            {
                "valid_draw_weekdays": [1, 3, 6],
                "draw_time_bjt": "21:15:00",
            },
        )

        coverage_year = int(target_issue[:4])
        default_suspension = [f"{coverage_year:04d}-02-14"]
        suspension_dates = sorted(set(default_suspension + (suspended or [])))
        suspension_path = root / "suspension.json"
        suspension_sha = _write_json(
            suspension_path,
            {
                "coverage_year": coverage_year,
                "suspended_dates_bjt": suspension_dates,
            },
        )

        promo_entry = None
        if include_promo:
            promo_path = root / "promo.json"
            promo_sha = _write_json(
                promo_path,
                {
                    "active": True,
                    "start_issue": "2026128",
                    "end_issue": promo_end,
                    "min_single_slip_rmb": promo_min_slip,
                    "allowed_bet_types": ["SINGLE", "DUPLEX"],
                    "second_prize_special_active": True,
                    "sixth_prize_double_active": True,
                    "remaining_sixth_promo_rmb": 559_400_000.0,
                },
            )
            promo_entry = {
                "source_id": "SC_FLCP",
                "url": "https://www.scflcp.com.cn/promo2026.json",
                "path": str(promo_path),
                "sha256": promo_sha,
                "fetched_at_utc": "2027-01-03T11:00:00Z",
                "parser_version": "sc_promo_html_or_json_v2",
            }

        plan = slip_plan or [
            {
                "slip_id": "SLIP_A",
                "bet_type": "SINGLE",
                "ticket_indices": list(range(ticket_count)),
                "amount_rmb": 2 * ticket_count,
            }
        ]
        return {
            "mode": "UNIFORM_MARGINAL_BALANCED_COVERAGE",
            "apply_popularity_filter": False,
            "target_issue": target_issue,
            "draw_at_utc": draw_at_utc,
            "ticket_count": ticket_count,
            "budget_rmb": 2 * ticket_count,
            "special_rule_active_previous": special_prev,
            "physical_slip_plan": plan,
            "schedule_manifest": {
                "source_id": "SH_FLCP",
                "url": "https://www.swlc.net.cn/schedule.json",
                "path": str(sched_path),
                "sha256": sched_sha,
                "fetched_at_utc": "2027-01-03T11:00:00Z",
                "parser_version": "sh_sched_html_or_manifest_v2",
            },
            "suspension_evidence": {
                "source_id": "GD_FLCP",
                "url": "https://www.gdfc.org.cn/suspension.json",
                "path": str(suspension_path),
                "sha256": suspension_sha,
                "fetched_at_utc": "2027-01-03T11:00:02Z",
                "parser_version": "gd_suspension_html_or_json_v1",
            },
            "promo_evidence": promo_entry,
            "official_sources": [
                {
                    "source_id": "SH_FLCP",
                    "url": "https://www.swlc.net.cn/latest.json",
                    "path": str(sh_path),
                    "sha256": sh_sha,
                    "fetched_at_utc": "2027-01-03T11:00:00Z",
                },
                {
                    "source_id": "SC_FLCP",
                    "url": "https://www.scflcp.com.cn/latest.json",
                    "path": str(sc_path),
                    "sha256": sc_sha,
                    "fetched_at_utc": "2027-01-03T11:00:05Z",
                },
            ],
        }


class TestV4Regression42(unittest.TestCase, V4TestFixtureMixin):
    # --- P1-B01: 原始证据与解析强绑定 (Tests 01-04) ---
    def test_01_b01_rejects_caller_parsed_latest_draw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            p["official_sources"][0]["parsed_latest_draw"] = {"issue": "2026152"}
            with self.assertRaises(GateBlockedError) as c:
                verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(c.exception.code, "CALLER_PARSED_PAYLOAD_FORBIDDEN")

    def test_02_b01_tampered_raw_file_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            Path(p["official_sources"][0]["path"]).write_bytes(b'{"tampered":true}')
            with self.assertRaises(GateBlockedError) as c:
                verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(c.exception.code, "QUORUM_CONSENSUS_FAILED")

    def test_03_b01_malformed_reds_in_raw_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            bad = {"latest_draw": {"issue": "2026152", "draw_date_bjt": "2026-12-31", "reds": [1, 1, 2, 3, 4, 5], "blue": 6, "pool_after_rmb": 1e8, "sales_rmb": 1e8}}
            sha = _write_json(Path(p["official_sources"][0]["path"]), bad)
            p["official_sources"][0]["sha256"] = sha
            with self.assertRaises(GateBlockedError):
                verify_2_of_n_official_quorum(p["official_sources"])

    def test_04_b01_malformed_blue_in_raw_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            bad = {"latest_draw": {"issue": "2026152", "draw_date_bjt": "2026-12-31", "reds": [1, 2, 3, 4, 5, 6], "blue": 17, "pool_after_rmb": 1e8, "sales_rmb": 1e8}}
            sha = _write_json(Path(p["official_sources"][0]["path"]), bad)
            p["official_sources"][0]["sha256"] = sha
            with self.assertRaises(GateBlockedError):
                verify_2_of_n_official_quorum(p["official_sources"])

    # --- P1-B02: 真独立 2-of-N 注册表仲裁 (Tests 05-09) ---
    def test_05_b02_unregistered_source_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            p["official_sources"][0]["source_id"] = "FAKE_LOTTERY_SITE"
            with self.assertRaises(GateBlockedError) as c:
                verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(c.exception.code, "UNREGISTERED_OFFICIAL_SOURCE")

    def test_06_b02_wrong_domain_or_http_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            p["official_sources"][0]["url"] = "http://www.swlc.net.cn/latest.json"
            with self.assertRaises(GateBlockedError) as c:
                verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(c.exception.code, "QUORUM_CONSENSUS_FAILED")

    def test_07_b02_duplicate_source_id_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            p["official_sources"][1]["source_id"] = "SH_FLCP"
            with self.assertRaises(GateBlockedError) as c:
                verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(c.exception.code, "DUPLICATE_SOURCE_ID")

    def test_08_b02_2_of_3_tolerates_one_corrupt_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root)
            cwl_path = root / "cwl.json"
            cwl_path.write_bytes(b"broken")
            p["official_sources"].append({
                "source_id": "CWL_CENTRAL",
                "url": "https://www.cwl.gov.cn/latest.json",
                "path": str(cwl_path),
                "sha256": "0" * 64,
                "fetched_at_utc": "2027-01-03T11:00:08Z",
            })
            res = verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(len(set(res.agreed_authorities)), 2)
            self.assertIn("CWL_CENTRAL", res.rejected_sources)

    def test_09_b02_disagreement_between_two_sources_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            diff = {"latest_draw": {"issue": "2026152", "draw_date_bjt": "2026-12-31", "reds": [1, 2, 3, 4, 5, 6], "blue": 7, "pool_after_rmb": 1e9, "sales_rmb": 3e8}}
            p["official_sources"][1]["sha256"] = _write_json(Path(p["official_sources"][1]["path"]), diff)
            with self.assertRaises(GateBlockedError) as c:
                verify_2_of_n_official_quorum(p["official_sources"])
            self.assertEqual(c.exception.code, "QUORUM_CONSENSUS_FAILED")

    # --- P1-B03: 2026 滞回规则状态机 (Tests 10-15) ---
    def test_10_b03_normal_under_100m(self) -> None:
        s = evaluate_2026_rule_state_machine(80_000_000.0, False)
        self.assertEqual(s.rule_regime, "NORMAL_POOL_UNDER_100M")
        self.assertFalse(s.special_rule_active)

    def test_11_b03_normal_100m_to_1500m(self) -> None:
        s = evaluate_2026_rule_state_machine(1_499_999_999.0, False)
        self.assertEqual(s.rule_regime, "NORMAL_POOL_100M_TO_1500M")
        self.assertFalse(s.fuyun_prize_active)

    def test_12_b03_enter_special_at_1500m(self) -> None:
        s = evaluate_2026_rule_state_machine(1_500_000_000.0, False)
        self.assertEqual(s.transition, "ENTER_SPECIAL")
        self.assertTrue(s.special_rule_active)
        self.assertTrue(s.fuyun_prize_active)

    def test_13_b03_hysteresis_keep_special_when_pool_drops_below_1500m(self) -> None:
        s = evaluate_2026_rule_state_machine(300_000_000.0, True)
        self.assertEqual(s.transition, "KEEP_SPECIAL")
        self.assertTrue(s.special_rule_active)
        self.assertTrue(s.fuyun_prize_active)

    def test_14_b03_hysteresis_exit_special_only_below_300m(self) -> None:
        s = evaluate_2026_rule_state_machine(299_999_999.0, True)
        self.assertEqual(s.transition, "EXIT_SPECIAL")
        self.assertFalse(s.special_rule_active)
        self.assertEqual(s.rule_regime, "NORMAL_POOL_100M_TO_1500M")

    def test_15_b03_exit_special_direct_to_under_100m(self) -> None:
        s = evaluate_2026_rule_state_machine(50_000_000.0, True)
        self.assertEqual(s.transition, "EXIT_SPECIAL")
        self.assertEqual(s.rule_regime, "NORMAL_POOL_UNDER_100M")

    # --- P1-B04: 删除 DRAW_EV_SCORE 只输出 DRAW_EV_CONTEXT (Tests 16-17) ---
    def test_16_b04_no_heuristic_ev_score_in_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp), ticket_count=10)
            promo = load_promo_evidence(p["promo_evidence"], "2027001")
            reg = evaluate_2026_rule_state_machine(1_600_000_000.0, False)
            slips = validate_physical_slip_plan(p["physical_slip_plan"], 10, 20)
            ctx = build_draw_ev_context(1_600_000_000.0, reg, promo, slips)
            self.assertNotIn("draw_ev_score", ctx)
            self.assertEqual(ctx["promo_eligibility"], "PLANNED_ELIGIBLE")

    def test_17_b04_required_context_keys_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp), include_promo=False)
            promo = load_promo_evidence(None, "2027001")
            reg = evaluate_2026_rule_state_machine(500_000_000.0, False)
            slips = validate_physical_slip_plan(p["physical_slip_plan"], 2, 4)
            ctx = build_draw_ev_context(500_000_000.0, reg, promo, slips)
            for k in ("rule_regime", "special_rule_active", "pool_rmb", "promo_active", "promo_eligibility", "first_prize_rule", "second_prize_rule", "fixed_prize_bonus_rule"):
                self.assertIn(k, ctx)

    # --- P1-B05: physical_slip_plan 与出票后确认 (Tests 18-23) ---
    def test_18_b05_split_20rmb_into_two_10rmb_slips_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = [
                {"slip_id": "A", "bet_type": "SINGLE", "ticket_indices": [0, 1, 2, 3, 4], "amount_rmb": 10},
                {"slip_id": "B", "bet_type": "SINGLE", "ticket_indices": [5, 6, 7, 8, 9], "amount_rmb": 10},
            ]
            p = self._build_env(Path(tmp), ticket_count=10, slip_plan=plan)
            promo = load_promo_evidence(p["promo_evidence"], "2027001")
            slips = validate_physical_slip_plan(plan, 10, 20)
            ctx = build_draw_ev_context(5e8, evaluate_2026_rule_state_machine(5e8, False), promo, slips)
            self.assertEqual(ctx["promo_eligibility"], "PLANNED_INELIGIBLE")

    def test_19_b05_partial_eligible_slips_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = [
                {"slip_id": "A", "bet_type": "SINGLE", "ticket_indices": list(range(10)), "amount_rmb": 20},
                {"slip_id": "B", "bet_type": "SINGLE", "ticket_indices": [10, 11], "amount_rmb": 4},
            ]
            p = self._build_env(Path(tmp), ticket_count=12, slip_plan=plan)
            promo = load_promo_evidence(p["promo_evidence"], "2027001")
            slips = validate_physical_slip_plan(plan, 12, 24)
            ctx = build_draw_ev_context(5e8, evaluate_2026_rule_state_machine(5e8, False), promo, slips)
            self.assertEqual(ctx["promo_eligibility"], "PLANNED_PARTIAL_ELIGIBLE")
            self.assertEqual(ctx["planned_eligible_slip_ids"], ["A"])

    def test_20_b05_overlapping_slip_indices_rejected(self) -> None:
        plan = [
            {"slip_id": "A", "bet_type": "SINGLE", "ticket_indices": [0, 1], "amount_rmb": 4},
            {"slip_id": "B", "bet_type": "SINGLE", "ticket_indices": [1, 2], "amount_rmb": 4},
        ]
        with self.assertRaises(GateBlockedError) as c:
            validate_physical_slip_plan(plan, 3, 6)
        self.assertEqual(c.exception.code, "OVERLAPPING_SLIP_INDICES")

    def test_21_b05_incomplete_partition_rejected(self) -> None:
        plan = [{"slip_id": "A", "bet_type": "SINGLE", "ticket_indices": [0, 1], "amount_rmb": 4}]
        with self.assertRaises(GateBlockedError) as c:
            validate_physical_slip_plan(plan, 3, 6)
        self.assertEqual(c.exception.code, "SLIP_PLAN_INCOMPLETE_PARTITION")

    def test_22_b05_post_purchase_upgrade_to_receipt_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp), ticket_count=10)
            promo = load_promo_evidence(p["promo_evidence"], "2027001")
            slips = validate_physical_slip_plan(p["physical_slip_plan"], 10, 20)
            res = confirm_physical_slips_post_purchase(
                slips,
                [{"slip_id": "SLIP_A", "printed_amount_rmb": 20, "printed_bet_type": "SINGLE", "terminal_receipt_sha256": "a" * 64}],
                promo,
            )
            self.assertEqual(res["promo_eligibility"], "RECEIPT_CONFIRMED_ELIGIBLE")

    def test_23_b05_post_purchase_mismatch_downgrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp), ticket_count=10)
            promo = load_promo_evidence(p["promo_evidence"], "2027001")
            slips = validate_physical_slip_plan(p["physical_slip_plan"], 10, 20)
            res = confirm_physical_slips_post_purchase(
                slips,
                [{"slip_id": "SLIP_A", "printed_amount_rmb": 10, "printed_bet_type": "SINGLE", "terminal_receipt_sha256": "a" * 64}],
                promo,
            )
            self.assertEqual(res["promo_eligibility"], "RECEIPT_MISMATCH_INELIGIBLE")

    # --- P1-B06: 派奖公告证据链 (Tests 24-27) ---
    def test_24_b06_caller_injected_active_flag_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            p["promo_evidence"]["active"] = True
            with self.assertRaises(GateBlockedError) as c:
                load_promo_evidence(p["promo_evidence"], "2027001")
            self.assertEqual(c.exception.code, "CALLER_PARSED_PAYLOAD_FORBIDDEN")

    def test_25_b06_promo_parser_version_mismatch_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            p["promo_evidence"]["parser_version"] = "wrong_v99"
            with self.assertRaises(GateBlockedError) as c:
                load_promo_evidence(p["promo_evidence"], "2027001")
            self.assertEqual(c.exception.code, "PROMO_PARSER_VERSION_MISMATCH")

    def test_26_b06_promo_out_of_issue_window_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp), promo_end="2026152")
            ev = load_promo_evidence(p["promo_evidence"], "2027001")
            self.assertFalse(ev.active_for_target_issue)

    def test_27_b06_promo_raw_sha_tamper_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            Path(p["promo_evidence"]["path"]).write_bytes(b"{}")
            with self.assertRaises(GateBlockedError) as c:
                load_promo_evidence(p["promo_evidence"], "2027001")
            self.assertEqual(c.exception.code, "EVIDENCE_SHA256_MISMATCH")

    # --- P1-B07 & CSPRNG 措辞: 精确覆盖率与有界构造 (Tests 28-32) ---
    def test_28_b07_exact_coverage_n5_matches_closed_form(self) -> None:
        out = generate_v4_tickets(5, DomainSeparatedDRBG(b"\x01" * 32))
        self.assertEqual(out.exact_red_4plus_union_count, 5 * 5428)
        self.assertEqual(out.prize_coverage_note, "EXACT_ENUMERATED_BATCH_COVERAGE")

    def test_29_b07_exact_coverage_n8_and_n15_exact_tag(self) -> None:
        for n in (8, 15):
            out = generate_v4_tickets(n, DomainSeparatedDRBG(bytes([n]) * 32))
            self.assertEqual(out.prize_coverage_note, "EXACT_ENUMERATED_BATCH_COVERAGE")
            self.assertLessEqual(out.exact_red_4plus_union_count, n * 5428)
            self.assertGreater(out.exact_red_4plus_union_count, 5428)

    def test_30_b07_n16_exact_full_blue_coverage(self) -> None:
        out = generate_v4_tickets(16, DomainSeparatedDRBG(b"\x10" * 32))
        self.assertEqual(out.prize_coverage, 1.0)
        self.assertEqual(len({t["blue"] for t in out.tickets}), 16)

    def test_31_b07_large_n64_bounded_termination_and_spread(self) -> None:
        out = generate_v4_tickets(64, DomainSeparatedDRBG(b"\x40" * 32), max_local_search_steps=3000)
        self.assertEqual(len(out.tickets), 64)
        self.assertLessEqual(out.coverage_diagnostics["red_count_spread"], 1)
        blue_counts = [sum(1 for t in out.tickets if t["blue"] == b) for b in range(1, 17)]
        self.assertEqual(max(blue_counts) - min(blue_counts), 0)

    def test_32_rng_model_declaration_exact_wording(self) -> None:
        self.assertEqual(
            RNG_MODEL_DECLARATION,
            "CRYPTOGRAPHICALLY_UNIFORM_MARGINAL_UNDER_CSPRNG_ASSUMPTION",
        )

    # --- 赛程外部化与跨年/休市门禁 (Tests 33-36) ---
    def test_33_schedule_manifest_custom_time_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            sched = bind_schedule_suspension(
                load_schedule_manifest_evidence(p["schedule_manifest"]),
                load_suspension_evidence(p["suspension_evidence"]),
            )
            with self.assertRaises(GateBlockedError) as c:
                verify_schedule_and_continuity(
                    "2027001", "2027-01-03T13:30:00Z", {"issue": "2026152", "draw_date_bjt": "2026-12-31"}, sched,
                    now_utc=datetime(2027, 1, 3, 10, 0, tzinfo=timezone.utc),
                )
            self.assertEqual(c.exception.code, "TARGET_DRAW_SCHEDULE_MISMATCH")

    def test_34_suspension_date_blocked_and_skipped_continuity_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp), suspended=["2027-01-03"])
            sched = bind_schedule_suspension(
                load_schedule_manifest_evidence(p["schedule_manifest"]),
                load_suspension_evidence(p["suspension_evidence"]),
            )
            with self.assertRaises(GateBlockedError) as c:
                verify_schedule_and_continuity(
                    "2027001", "2027-01-03T13:15:00Z", {"issue": "2026152", "draw_date_bjt": "2026-12-31"}, sched,
                    now_utc=datetime(2027, 1, 3, 10, 0, tzinfo=timezone.utc),
                )
            self.assertEqual(c.exception.code, "DRAW_DATE_SUSPENDED")
            # 跳过 1月3日休市，1月5日周二应正常放行
            dt = verify_schedule_and_continuity(
                "2027001", "2027-01-05T13:15:00Z", {"issue": "2026152", "draw_date_bjt": "2026-12-31"}, sched,
                now_utc=datetime(2027, 1, 5, 10, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(dt.day, 5)

    def test_35_post_draw_generation_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            sched = bind_schedule_suspension(
                load_schedule_manifest_evidence(p["schedule_manifest"]),
                load_suspension_evidence(p["suspension_evidence"]),
            )
            with self.assertRaises(GateBlockedError) as c:
                verify_schedule_and_continuity(
                    "2027001", "2027-01-03T13:15:00Z", {"issue": "2026152", "draw_date_bjt": "2026-12-31"}, sched,
                    now_utc=datetime(2027, 1, 3, 13, 15, tzinfo=timezone.utc),
                )
            self.assertEqual(c.exception.code, "DRAW_ALREADY_STARTED_OR_PAST")

    def test_36_unaccounted_gap_draw_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = self._build_env(Path(tmp))
            sched = bind_schedule_suspension(
                load_schedule_manifest_evidence(p["schedule_manifest"]),
                load_suspension_evidence(p["suspension_evidence"]),
            )
            with self.assertRaises(GateBlockedError) as c:
                verify_schedule_and_continuity(
                    "2027001", "2027-01-05T13:15:00Z", {"issue": "2026152", "draw_date_bjt": "2026-12-31"}, sched,
                    now_utc=datetime(2027, 1, 5, 10, 0, tzinfo=timezone.utc),
                )
            self.assertEqual(c.exception.code, "PREV_ISSUE_CHAIN_BROKEN")

    # --- P1-B08 & P1-B09: 全量Commitment、四阶段事务与崩溃恢复 (Tests 37-42) ---
    def test_37_b08_commitment_binds_all_required_audit_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root, ticket_count=4)
            ledger = AppendOnlyLedger(root / "ledger.jsonl", V3_HEAD)
            res = run_v4_production(p, root / "pub", ledger, now_utc=datetime(2027, 1, 3, 11, 30, tzinfo=timezone.utc))
            priv = json.loads((Path(res["published_dir"]) / "PRIVATE_SEED_AND_NONCE.json").read_text("utf-8"))
            cb = priv["commitment_body"]
            for field in (
                "algorithm_version", "rng_model", "input_manifest_sha256",
                "official_evidence_hashes", "schedule_manifest_sha256",
                "suspension_evidence_sha256", "promo_evidence_sha256", "seed_sha256", "rule_regime_state",
                "physical_slip_plan", "tickets", "nonce_hex",
            ):
                self.assertIn(field, cb)

    def test_38_b09_atomic_publish_and_0600_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root)
            ledger = AppendOnlyLedger(root / "ledger.jsonl", V3_HEAD)
            res = run_v4_production(p, root / "pub", ledger, now_utc=datetime(2027, 1, 3, 11, 30, tzinfo=timezone.utc))
            pub_dir = Path(res["published_dir"])
            self.assertFalse((root / "pub" / ".staging_2027001").exists())
            self.assertEqual(os.stat(pub_dir / "PRIVATE_SEED_AND_NONCE.json").st_mode & 0o777, 0o600)
            self.assertTrue(ledger.verify_chain()[0])

    def test_39_b09_crash_after_ledger_commit_recovers_without_redraw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root, ticket_count=6)
            ledger = AppendOnlyLedger(root / "ledger.jsonl", V3_HEAD)
            now = datetime(2027, 1, 3, 11, 30, tzinfo=timezone.utc)

            def _boom() -> None:
                raise OSError("Simulated power loss before atomic publish")

            with self.assertRaises(OSError):
                run_v4_production(p, root / "pub", ledger, now_utc=now, _inject_failure_after_ledger_commit=_boom)

            # 崩溃后账本已有记录，严禁重抽
            with self.assertRaises(GateBlockedError) as c:
                run_v4_production(p, root / "pub", ledger, now_utc=now)
            self.assertEqual(c.exception.code, "REFUSE_DUPLICATE_ISSUE")

            # 执行故障恢复：无重抽完成原子发布，且承诺哈希与账本一致
            rec_before = ledger.find_issue_record("2027001")
            recov = recover_staged_publish("2027001", root / "pub", ledger)
            self.assertEqual(recov["recovery_action"], "COMPLETED_ATOMIC_PUBLISH_NO_REDRAW")
            tkt = json.loads((Path(recov["final_dir"]) / "TICKETS.json").read_text("utf-8"))
            self.assertEqual(tkt["commitment_sha256"], rec_before["commitment_sha256"])

    def test_40_b09_tampered_staging_fails_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root)
            ledger = AppendOnlyLedger(root / "ledger.jsonl", V3_HEAD)
            with self.assertRaises(OSError):
                run_v4_production(
                    p, root / "pub", ledger,
                    now_utc=datetime(2027, 1, 3, 11, 30, tzinfo=timezone.utc),
                    _inject_failure_after_ledger_commit=lambda: (_ for _ in ()).throw(OSError("crash")),
                )
            (root / "pub" / ".staging_2027001" / "TICKETS.json").write_text("{\"hacked\":true}", encoding="utf-8")
            with self.assertRaises(GateBlockedError) as c:
                recover_staged_publish("2027001", root / "pub", ledger)
            self.assertEqual(c.exception.code, "STAGED_FILE_SHA_MISMATCH")

    def test_41_popularity_filter_hard_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root)
            p["apply_popularity_filter"] = True
            with self.assertRaises(GateBlockedError) as c:
                run_v4_production(p, root / "pub", AppendOnlyLedger(root / "l.jsonl", V3_HEAD))
            self.assertEqual(c.exception.code, "POPULARITY_FILTER_BLOCKED")

    def test_42_ledger_tamper_breaks_verify_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._build_env(root)
            ledger = AppendOnlyLedger(root / "ledger.jsonl", V3_HEAD)
            run_v4_production(p, root / "pub", ledger, now_utc=datetime(2027, 1, 3, 11, 30, tzinfo=timezone.utc))
            raw = (root / "ledger.jsonl").read_text("utf-8").replace("2027001", "2027999")
            (root / "ledger.jsonl").write_text(raw, encoding="utf-8")
            self.assertFalse(ledger.verify_chain()[0])


if __name__ == "__main__":
    unittest.main()

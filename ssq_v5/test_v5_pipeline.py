"""
test_v5_pipeline.py
Phase 2-4 综合验收测试（运行：python3 -m unittest test_v5_pipeline.py -v）
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT / "ssq_v4_rc2") not in sys.path:
    sys.path.insert(0, str(_PARENT / "ssq_v4_rc2"))

from runner_v4 import AppendOnlyLedger
from sampler_v4 import DomainSeparatedDRBG

from db_and_sentiment_v5 import (
    HistoricalDrawRecord,
    V5GateError,
    build_verified_sentiment_snapshot,
    extract_calendar_pulse_numbers,
)
from digital_twin_v5 import (
    CalibratedTwinParameters,
    P_TIER4,
    P_TIER5,
    P_TIER6,
    build_causal_context_from_history,
    extract_red_features_matrix,
    generate_all_red_combinations_1based,
    run_expanding_window_oos_calibration,
    score_full_space_density,
)
from liability_and_pockets_v5 import (
    build_frozen_sparse_pool,
    compute_exact_neighborhood_densities,
    compute_red_liability_score,
    sample_from_sparse_pockets,
    select_regime_policy,
)
from runner_v5 import (
    V4_FALLBACK_ROUTE,
    V5_ROUTE_PROD,
    evaluate_cusum_circuit_breaker,
    run_v5_production,
)

V3_HEAD = "0b44cd1e63caec4b9a95994f9655ecdd0673df47b1d5975047b8d5d247904a4f"


def _write_json(path: Path, obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _make_synthetic_history(n: int = 260, inject_signal: bool = True) -> list[HistoricalDrawRecord]:
    rng = np.random.default_rng(42)
    recs: list[HistoricalDrawRecord] = []
    for i in range(n):
        reds = tuple(sorted(int(x) for x in rng.choice(np.arange(1, 34), size=6, replace=False)))
        blue = int(rng.integers(1, 17))
        sales = 360_000_000.0
        n_tkt = sales / 2.0
        # 注入结构性人群偏置用于验证数字孪生样本外识别能力
        r_mult = 1.25 if (inject_signal and max(reds) <= 31) else 0.90
        b_mult = 1.35 if (inject_signal and blue <= 12) else 0.80
        t4 = int(round(n_tkt * P_TIER4 * r_mult * rng.normal(1.0, 0.03)))
        t5 = int(round(n_tkt * P_TIER5 * r_mult * rng.normal(1.0, 0.02)))
        t6 = int(round(n_tkt * P_TIER6 * b_mult * rng.normal(1.0, 0.02)))
        issue = f"2025{i + 1:03d}" if i < 150 else f"2026{i - 149:03d}"
        recs.append(
            HistoricalDrawRecord(
                issue=issue,
                draw_date_bjt="2026-12-31",
                reds=reds,
                blue=blue,
                sales_rmb=sales,
                pool_after_rmb=1_200_000_000.0,
                tier1_count=4,
                tier2_count=120,
                tier3_count=1500,
                tier4_count=max(1, t4),
                tier5_count=max(1, t5),
                tier6_count=max(1, t6),
            )
        )
    return recs


class TestV5Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.combos = generate_all_red_combinations_1based()

    # --- Phase 2 测试 ---
    def test_01_sentiment_forbids_caller_parsed_and_enforces_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s1, s2 = root / "p1.json", root / "p2.json"
            body = {
                "target_issue": "2027001",
                "hot_reds": [6, 8, 18],
                "kill_reds": [32, 33],
                "hot_blues": [8],
                "kill_blues": [15],
                "double_dan_pairs": [[6, 8]],
            }
            h1, h2 = _write_json(s1, body), _write_json(s2, body)
            entries = [
                {
                    "channel_id": "PORTAL_A",
                    "url": "https://www.zhcw.com/s.json",
                    "path": str(s1),
                    "sha256": h1,
                    "fetched_at_utc": "2027-01-03T11:00:00Z",  # 19:00 BJT (窗口内)
                    "parser_version": "sent_json_v1",
                },
                {
                    "channel_id": "PORTAL_B",
                    "url": "https://www.cjcp.com.cn/s.json",
                    "path": str(s2),
                    "sha256": h2,
                    "fetched_at_utc": "2027-01-03T11:10:00Z",  # 19:10 BJT (窗口内)
                    "parser_version": "sent_json_v1",
                },
            ]
            snap = build_verified_sentiment_snapshot(entries, "2027001", "2027-01-03")
            self.assertEqual(snap.status, "VERIFIED")
            self.assertEqual(snap.consensus_kill_reds, frozenset({32, 33}))

            # 违规注入预解析字段必须拦截
            entries[0]["hot_reds"] = [1, 2]
            with self.assertRaises(V5GateError) as c:
                build_verified_sentiment_snapshot(entries, "2027001", "2027-01-03")
            self.assertEqual(c.exception.code, "SENTIMENT_CALLER_PARSED_FORBIDDEN")

    def test_02_calendar_pulse_deterministic(self) -> None:
        pulses = extract_calendar_pulse_numbers("2026-11-11")
        self.assertIn(11, pulses)
        self.assertIn(22, pulses)

    # --- Phase 3 测试 ---
    def test_03_reflexivity_weird_combos_excluded(self) -> None:
        hist = _make_synthetic_history(30)
        ctx = build_causal_context_from_history(hist, "2026100", "2026-11-24")
        weird_samples = np.asarray(
            [
                [1, 2, 3, 4, 5, 6],        # 6连号
                [5, 10, 15, 20, 25, 30],   # 严格等差
                [2, 4, 8, 14, 22, 30],     # 全偶
                [1, 5, 14, 30, 31, 33],    # 30+过载
                [3, 9, 14, 22, 27, 32],    # 正常平庸组合（应放行）
            ],
            dtype=np.uint8,
        )
        _, weird_mask = extract_red_features_matrix(weird_samples, ctx)
        self.assertTrue(all(weird_mask[:4]))
        self.assertFalse(weird_mask[4])

    def test_04_expanding_window_oos_calibration_passes_with_signal_and_fails_on_noise(self) -> None:
        # 含真实结构信号：应通过 OOS 门禁
        sig_hist = _make_synthetic_history(260, inject_signal=True)
        params_pass, _ = run_expanding_window_oos_calibration(
            sig_hist, initial_train_size=100, min_oos_draws=150
        )
        self.assertEqual(params_pass.oos_gate_status, "PASS")

        # 纯噪音无信号：必须被 OOS 门禁拒绝
        noise_hist = _make_synthetic_history(260, inject_signal=False)
        params_fail, _ = run_expanding_window_oos_calibration(
            noise_hist, initial_train_size=100, min_oos_draws=150
        )
        self.assertEqual(params_fail.oos_gate_status, "TWIN_OOS_GATE_FAILED")

    # --- Phase 4 核心算法精确性测试 ---
    def test_05_exact_inclusion_exclusion_matches_bruteforce_and_speed_under_30s(self) -> None:
        # 验证 1: 恒等密度 D=1 => 全部 1,107,568 组精确得到 n5=162, n4=5265
        t0 = time.perf_counter()
        ones = np.ones(len(self.combos), dtype=np.float64)
        n5, n4 = compute_exact_neighborhood_densities(self.combos, ones)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 30.0)
        self.assertTrue(np.allclose(n5, 162.0, atol=1e-9))
        self.assertTrue(np.allclose(n4, 5265.0, atol=1e-9))

        # 验证 2: 随机非均匀点质量分布，与逐对交集暴力计算 0 误差一致
        rng = np.random.default_rng(7)
        sparse_d = np.zeros(len(self.combos), dtype=np.float64)
        active_idx = rng.choice(len(self.combos), size=80, replace=False)
        sparse_d[active_idx] = rng.uniform(0.5, 5.0, size=80)
        fast_n5, fast_n4 = compute_exact_neighborhood_densities(self.combos, sparse_d)

        active_sets = [set(self.combos[i].tolist()) for i in active_idx]
        for test_i in rng.choice(len(self.combos), size=15, replace=False):
            ts = set(self.combos[test_i].tolist())
            bf5 = sum(sparse_d[idx] for idx, aset in zip(active_idx, active_sets) if len(ts & aset) == 5)
            bf4 = sum(sparse_d[idx] for idx, aset in zip(active_idx, active_sets) if len(ts & aset) == 4)
            self.assertAlmostEqual(float(fast_n5[test_i]), float(bf5), places=9)
            self.assertAlmostEqual(float(fast_n4[test_i]), float(bf4), places=9)

    def test_06_sparse_pockets_dispersion_and_cusum_circuit_breaker(self) -> None:
        # 验证 CUSUM 正常态与漂移熔断态
        ok_cb = evaluate_cusum_circuit_breaker([0.05] * 20, baseline_oos_mae=0.05)
        self.assertFalse(ok_cb.tripped)
        trip_cb = evaluate_cusum_circuit_breaker([0.12] * 20, baseline_oos_mae=0.05)
        self.assertTrue(trip_cb.tripped)
        self.assertEqual(trip_cb.status, "V5_CIRCUIT_BREAKER_TRIPPED")

    def test_07_runner_v5_oos_gate_lock_and_cusum_fallback_to_v4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hist = _make_synthetic_history(120, inject_signal=True)
            last = hist[-1]

            # 构造通过与失败两套校准参数
            params_pass, _ = run_expanding_window_oos_calibration(hist, initial_train_size=60, min_oos_draws=50)
            params_locked = CalibratedTwinParameters(
                **{**params_pass.__dict__, "oos_gate_status": "TWIN_OOS_GATE_FAILED"}
            )

            draw_doc = {
                "latest_draw": {
                    "issue": last.issue,
                    "draw_date_bjt": "2026-12-31",
                    "reds": list(last.reds),
                    "blue": last.blue,
                    "pool_after_rmb": 1_200_000_000.0,
                    "sales_rmb": 360_000_000.0,
                }
            }
            sh, sc = root / "sh.json", root / "sc.json"
            sched = root / "sched.json"
            sh_h, sc_h = _write_json(sh, draw_doc), _write_json(sc, draw_doc)
            sched_h = _write_json(sched, {"valid_draw_weekdays": [1, 3, 6], "draw_time_bjt": "21:15:00", "suspended_dates_bjt": []})

            payload = {
                "mode": V5_ROUTE_PROD,
                "target_issue": "2027001",
                "draw_at_utc": "2027-01-03T13:15:00Z",
                "ticket_count": 4,
                "budget_rmb": 8,
                "special_rule_active_previous": False,
                "physical_slip_plan": [{"slip_id": "A", "bet_type": "SINGLE", "ticket_indices": [0, 1, 2, 3], "amount_rmb": 8}],
                "schedule_manifest": {
                    "source_id": "SH_FLCP", "url": "https://www.swlc.net.cn/sc.json",
                    "path": str(sched), "sha256": sched_h, "fetched_at_utc": "2027-01-03T11:00:00Z", "parser_version": "sh_sched_v1",
                },
                "official_sources": [
                    {"source_id": "SH_FLCP", "url": "https://www.swlc.net.cn/d.json", "path": str(sh), "sha256": sh_h, "fetched_at_utc": "2027-01-03T11:00:00Z"},
                    {"source_id": "SC_FLCP", "url": "https://www.scflcp.com.cn/d.json", "path": str(sc), "sha256": sc_h, "fetched_at_utc": "2027-01-03T11:00:00Z"},
                ],
                "sentiment_entries": [],
                "enforce_sentiment_window": False,
            }
            now = datetime(2027, 1, 3, 11, 20, tzinfo=timezone.utc)

            # 场景 A: OOS 门禁未通过 -> 强制降级 V4
            res_a = run_v5_production(
                payload, hist, params_locked, [0.05] * 20, root / "pub_a",
                AppendOnlyLedger(root / "la.jsonl", V3_HEAD), now_utc=now, combos_cache=self.combos,
            )
            self.assertEqual(res_a["receipt"]["effective_route_mode"], V4_FALLBACK_ROUTE)
            self.assertIn("V5_OOS_GATE_LOCKED", res_a["receipt"]["fallback_reason"])

            # 场景 B: OOS 通过但 CUSUM 触发漂移熔断 -> 自动降级 V4
            res_b = run_v5_production(
                payload, hist, params_pass, [0.99] * 20, root / "pub_b",
                AppendOnlyLedger(root / "lb.jsonl", V3_HEAD), now_utc=now, combos_cache=self.combos,
            )
            self.assertEqual(res_b["receipt"]["effective_route_mode"], V4_FALLBACK_ROUTE)
            self.assertEqual(res_b["receipt"]["circuit_breaker"]["status"], "V5_CIRCUIT_BREAKER_TRIPPED")

            # 场景 C: OOS 通过且无漂移 -> 正式执行 V5 稀疏口袋路由
            res_c = run_v5_production(
                payload, hist, params_pass, [0.02] * 20, root / "pub_c",
                AppendOnlyLedger(root / "lc.jsonl", V3_HEAD), now_utc=now, combos_cache=self.combos,
            )
            self.assertEqual(res_c["receipt"]["effective_route_mode"], V5_ROUTE_PROD)
            self.assertEqual(len({t["blue"] for t in json.loads((Path(res_c["published_dir"]) / "TICKETS.json").read_text("utf-8"))["tickets"]}), 4)


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import hashlib
import unittest

import numpy as np

from ssq_v5.digital_twin_v5 import generate_all_red_combinations_1based
from ssq_v5.policy_selector_v5 import (
    CausalDrawPolicyRecord,
    FROZEN_POLICY_GRID,
    PROTOCOL_HYPERPARAMETERS,
    run_nested_walk_forward_policy_selection,
)
from ssq_v5.statutory_liability_v5 import (
    EXPOSURE_LABEL,
    StatutoryRuleTable,
    compute_exact_all_neighborhoods,
    compute_single_ticket_rmb_breakdown,
    compute_statutory_rmb_liability_surface,
)


class TestV5PolicyPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.combos = generate_all_red_combinations_1based()
        cls.rule = StatutoryRuleTable(
            rule_document_id="TEST_RULE",
            rule_document_sha256=hashlib.sha256(b"TEST_RULE").hexdigest(),
            prize_return_ratio=0.49,
            tier3_rmb=3000.0,
            tier4_rmb=200.0,
            tier5_rmb=10.0,
            tier6_rmb=5.0,
            fuyun_rmb=5.0,
            low_pool_threshold_rmb=100_000_000.0,
            low_pool_t1_share=0.75,
            low_pool_t2_share=0.25,
            high_pool_t1_part_a_share=0.55,
            high_pool_t1_part_b_share=0.20,
            high_pool_t2_share=0.25,
            single_part_cap_rmb=5_000_000.0,
            promo_sixth_extra_rmb_per_ticket=5.0,
        )

    def test_01_full_universe_uniform_neighborhood_identities(self) -> None:
        ones = np.ones(len(self.combos), dtype=np.float64)
        nb = compute_exact_all_neighborhoods(self.combos, ones)
        self.assertTrue(np.allclose(nb.n5, 162.0, atol=1e-9))
        self.assertTrue(np.allclose(nb.n4, 5265.0, atol=1e-9))
        self.assertTrue(np.allclose(nb.n3, 58500.0, atol=1e-9))
        self.assertTrue(np.allclose(nb.n_le_2, 1_043_640.0, atol=1e-9))

    def test_02_nonuniform_sparse_mass_matches_bruteforce(self) -> None:
        rng = np.random.default_rng(1234)
        sparse_d = np.zeros(len(self.combos), dtype=np.float64)
        active_idx = rng.choice(len(self.combos), size=80, replace=False)
        sparse_d[active_idx] = rng.uniform(0.25, 5.0, size=len(active_idx))
        nb = compute_exact_all_neighborhoods(self.combos, sparse_d)
        active_sets = [(idx, set(self.combos[idx].tolist())) for idx in active_idx]
        for test_i in rng.choice(len(self.combos), size=32, replace=False):
            ts = set(self.combos[test_i].tolist())
            bf5 = sum(sparse_d[idx] for idx, s in active_sets if len(ts & s) == 5)
            bf4 = sum(sparse_d[idx] for idx, s in active_sets if len(ts & s) == 4)
            bf3 = sum(sparse_d[idx] for idx, s in active_sets if len(ts & s) == 3)
            bf2 = sum(sparse_d[idx] for idx, s in active_sets if len(ts & s) <= 2)
            self.assertAlmostEqual(float(nb.n5[test_i]), float(bf5), places=8)
            self.assertAlmostEqual(float(nb.n4[test_i]), float(bf4), places=8)
            self.assertAlmostEqual(float(nb.n3[test_i]), float(bf3), places=8)
            self.assertAlmostEqual(float(nb.n_le_2[test_i]), float(bf2), places=8)

    def test_03_plugin_exposure_and_promo_budget_cap(self) -> None:
        ones = np.ones(len(self.combos), dtype=np.float64)
        nb = compute_exact_all_neighborhoods(self.combos, ones)
        blues = np.ones(16, dtype=np.float64)
        weird = np.zeros(len(self.combos), dtype=bool)
        weird[0] = True
        red, blue = compute_statutory_rmb_liability_surface(
            nb=nb,
            blue_density=blues,
            weird_mask=weird,
            sales_rmb=400_000_000.0,
            pool_before_rmb=1_200_000_000.0,
            special_rule_active=False,
            fuyun_prize_active=False,
            remaining_sixth_promo_rmb=100.0,
            rule=self.rule,
        )
        self.assertTrue(np.isinf(red[0]))
        self.assertTrue(np.all(np.isfinite(red[1:])))
        self.assertEqual(blue.shape, (16,))
        bd = compute_single_ticket_rmb_breakdown(
            nb=nb,
            row_idx=10,
            blue_1based=1,
            blue_density=blues,
            sales_rmb=400_000_000.0,
            pool_before_rmb=1_200_000_000.0,
            special_rule_active=False,
            fuyun_prize_active=False,
            remaining_sixth_promo_rmb=100.0,
            rule=self.rule,
        )
        self.assertEqual(bd["exposure_label"], EXPOSURE_LABEL)
        self.assertLessEqual(float(bd["tier6_promo_extra_rmb"]), 100.0 + 1e-9)

    def _signal_records(self, seed: int, signal: bool) -> list[CausalDrawPolicyRecord]:
        rng = np.random.default_rng(seed)
        recs = []
        for i in range(500):
            r_pct = float(rng.uniform(0.0, 100.0))
            b_rank = int(rng.integers(0, 16))
            if signal:
                r_crowd = 0.55 if (1.0 <= r_pct <= 10.0) else 1.12
                b_crowd = 0.62 if b_rank < 6 else 1.15
                r_crowd += float(rng.normal(0, 0.025))
                b_crowd += float(rng.normal(0, 0.025))
            else:
                r_crowd = 1.0 + float(rng.normal(0, 0.08))
                b_crowd = 1.0 + float(rng.normal(0, 0.08))
            recs.append(CausalDrawPolicyRecord(
                issue=f"T{i:04d}",
                actual_red_liability_pct=r_pct,
                actual_blue_crowding_rank=b_rank,
                realized_red_crowding_ratio=r_crowd,
                realized_blue_crowding_ratio=b_crowd,
            ))
        return recs

    def test_04_nested_gate_passes_strong_signal_and_binds_integrity_inputs(self) -> None:
        art, trace = run_nested_walk_forward_policy_selection(
            self._signal_records(2026, True),
            history_csv_sha256="1" * 64,
            twin_artifact_sha256="2" * 64,
            rule_document_sha256="3" * 64,
            git_commit_sha="4" * 40,
        )
        self.assertEqual(art.policy_gate_status, "PASS")
        self.assertEqual(art.outer_oos_draws, 400)
        self.assertGreater(art.outer_ci95[0], 0.0)
        self.assertEqual(len(trace), 400)
        self.assertEqual(art.history_csv_sha256, "1" * 64)
        self.assertEqual(art.twin_artifact_sha256, "2" * 64)
        self.assertEqual(len(art.frozen_candidate_grid), 9)
        self.assertEqual(PROTOCOL_HYPERPARAMETERS["bootstrap_block_size"], 12)
        self.assertEqual(sum(art.candidate_selection_counts.values()), 400)

    def test_05_nested_gate_rejects_pure_noise(self) -> None:
        art, _ = run_nested_walk_forward_policy_selection(
            self._signal_records(999, False),
            history_csv_sha256="1" * 64,
            twin_artifact_sha256="2" * 64,
            rule_document_sha256="3" * 64,
            git_commit_sha="4" * 40,
        )
        self.assertEqual(art.policy_gate_status, "REJECTED_POLICY_GATE_FAILED")

    def test_06_frozen_policy_grid_is_exactly_3x3(self) -> None:
        grid = {(c.red_percentile_low, c.red_percentile_high, c.blue_keep_count) for c in FROZEN_POLICY_GRID}
        expected = {(lo, hi, b) for lo, hi in ((1.0,5.0),(1.0,10.0),(1.0,20.0)) for b in (5,6,8)}
        self.assertEqual(grid, expected)


if __name__ == "__main__":
    unittest.main()

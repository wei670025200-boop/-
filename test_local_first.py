from __future__ import annotations

import unittest
from pathlib import Path

from local_history import DEFAULT_HISTORY_PATH, full_tier_history, latest_completed, load_ssq_history
from v4.offline_local_v4 import run_v4_local


class TestLocalFirst(unittest.TestCase):
    def test_history_shape(self):
        rows = load_ssq_history(DEFAULT_HISTORY_PATH)
        self.assertEqual(len(rows), 768)
        self.assertEqual(rows[0].issue, "2021121")
        self.assertEqual(rows[-1].issue, "2026135")
        full = full_tier_history(DEFAULT_HISTORY_PATH)
        self.assertEqual(len(full), 600)
        self.assertEqual(full[0].issue, "2022139")
        self.assertEqual(full[-1].issue, "2026135")

    def test_v4_offline_deterministic_seed(self):
        seed = bytes.fromhex("11" * 32)
        a = run_v4_local(2, seed=seed)
        b = run_v4_local(2, seed=seed)
        self.assertEqual(a["tickets"], b["tickets"])
        self.assertEqual(a["latest_completed_issue"], "2026135")


if __name__ == "__main__":
    unittest.main()
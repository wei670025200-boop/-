from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from ssq_v5.db_and_sentiment_v5 import (
    HistoricalDrawRecord,
    V5GateError,
    build_verified_sentiment_snapshot,
    extract_calendar_pulse_numbers,
)
from ssq_v5.digital_twin_v5 import (
    P_TIER4,
    P_TIER5,
    P_TIER6,
    build_causal_context_from_history,
    extract_red_features_matrix,
    run_expanding_window_oos_calibration,
)
from ssq_v5.phase4_real_policy_gate_v5 import evaluate_cusum_circuit_breaker
from ssq_v5.runner_v5 import (
    PHASE4_STATUS,
    V5_POLICY_GATE_STATUS,
    run_v5_local_production,
    validate_v5_production_artifacts,
)


def _write_json(path: Path, obj: dict) -> str:
    raw=json.dumps(obj,sort_keys=True,ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _synthetic_history(n: int=260, signal: bool=True) -> list[HistoricalDrawRecord]:
    rng=np.random.default_rng(42)
    out=[]
    for i in range(n):
        reds=tuple(sorted(int(x) for x in rng.choice(np.arange(1,34),size=6,replace=False)))
        blue=int(rng.integers(1,17))
        sales=360_000_000.0
        nt=sales/2.0
        rm=1.25 if (signal and max(reds)<=31) else (0.90 if signal else 1.0)
        bm=1.35 if (signal and blue<=12) else (0.80 if signal else 1.0)
        t4=max(1,int(round(nt*P_TIER4*rm*rng.normal(1.0,0.03))))
        t5=max(1,int(round(nt*P_TIER5*rm*rng.normal(1.0,0.02))))
        t6=max(1,int(round(nt*P_TIER6*bm*rng.normal(1.0,0.02))))
        out.append(HistoricalDrawRecord(
            issue=f"S{i:04d}",draw_date_bjt="2026-12-31",reds=reds,blue=blue,
            sales_rmb=sales,pool_after_rmb=1_200_000_000.0,
            tier1_count=4,tier2_count=120,tier3_count=1500,
            tier4_count=t4,tier5_count=t5,tier6_count=t6,
        ))
    return out


class TestV5LocalFirstRegression(unittest.TestCase):
    def test_01_local_sentiment_parser_contract_still_rejects_caller_parsed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            body={"target_issue":"2027001","hot_reds":[6,8,18],"kill_reds":[32,33],
                  "hot_blues":[8],"kill_blues":[15],"double_dan_pairs":[[6,8]]}
            p1,p2=root/"a.json",root/"b.json"
            h1,h2=_write_json(p1,body),_write_json(p2,body)
            entries=[
                {"channel_id":"PORTAL_A","url":"https://www.zhcw.com/s.json","path":str(p1),"sha256":h1,
                 "fetched_at_utc":"2027-01-03T11:00:00Z","parser_version":"sent_json_v1"},
                {"channel_id":"PORTAL_B","url":"https://www.cjcp.com.cn/s.json","path":str(p2),"sha256":h2,
                 "fetched_at_utc":"2027-01-03T11:10:00Z","parser_version":"sent_json_v1"},
            ]
            snap=build_verified_sentiment_snapshot(entries,"2027001","2027-01-03")
            self.assertEqual(snap.status,"VERIFIED")
            entries[0]["hot_reds"]=[1,2]
            with self.assertRaises(V5GateError):
                build_verified_sentiment_snapshot(entries,"2027001","2027-01-03")

    def test_02_calendar_pulse_deterministic(self):
        p=extract_calendar_pulse_numbers("2026-11-11")
        self.assertIn(11,p)
        self.assertIn(22,p)

    def test_03_reflexivity_exclusion(self):
        hist=_synthetic_history(30,True)
        ctx=build_causal_context_from_history(hist,"X","2026-11-24")
        samples=np.asarray([[1,2,3,4,5,6],[5,10,15,20,25,30],[2,4,8,14,22,30],[1,5,14,30,31,33],[3,9,14,22,27,32]],dtype=np.uint8)
        _,mask=extract_red_features_matrix(samples,ctx)
        self.assertTrue(all(mask[:4]))
        self.assertFalse(mask[4])

    def test_04_phase3_signal_pass_noise_fail(self):
        sig=_synthetic_history(260,True)
        p,_=run_expanding_window_oos_calibration(sig,initial_train_size=100,min_oos_draws=150)
        self.assertEqual(p.oos_gate_status,"PASS")
        noise=_synthetic_history(260,False)
        p2,_=run_expanding_window_oos_calibration(noise,initial_train_size=100,min_oos_draws=150)
        self.assertEqual(p2.oos_gate_status,"TWIN_OOS_GATE_FAILED")

    def test_05_cusum_normal_and_drift(self):
        ok=evaluate_cusum_circuit_breaker([0.05]*20,baseline_oos_mae=0.05)
        bad=evaluate_cusum_circuit_breaker([0.12]*20,baseline_oos_mae=0.05)
        self.assertFalse(ok.tripped)
        self.assertTrue(bad.tripped)
        self.assertEqual(bad.status,"V5_CIRCUIT_BREAKER_TRIPPED")

    def test_06_real_gate_artifact_and_three_locks(self):
        x=json.loads(Path("results/PHASE4_POLICY_GATE_SUMMARY.json").read_text("utf-8"))
        self.assertEqual(x["policy_gate_status"],"PASS")
        self.assertEqual(x["outer_oos_draws"],400)
        self.assertGreater(x["outer_ci95"][0],0.0)
        self.assertEqual(x["selected_policy"]["policy_id"],"P_R01_005_B06")
        self.assertEqual(x["three_locks"],{"cusum_drift_gate":"OK","phase3_oos_gate":"PASS","policy_gate":"PASS"})
        self.assertTrue(x["production_eligible"])

    def test_07_real_e2e_2_and_8_ticket_invariants(self):
        x=json.loads(Path("results/PHASE4_POLICY_GATE_SUMMARY.json").read_text("utf-8"))
        for n in ("2","8"):
            e=x[f"e2e_{n}"]
            self.assertEqual(e["ticket_count"],int(n))
            self.assertTrue(e["blue_unique"])
            self.assertEqual(len(e["tickets"]),int(n))
            self.assertEqual(len(e["pocket_distribution"]),int(n))
            self.assertEqual(e["route"],"V5_PRODUCTION_ELIGIBLE")
            for t in e["tickets"]:
                self.assertEqual(t["plugin_statutory_rmb_breakdown"]["exposure_label"],"PLUG_IN_STATUTORY_RMB_EXPOSURE")

    def test_08_unlocked_runner_verifies_artifacts_and_fails_closed_without_promo_balance(self):
        self.assertEqual(PHASE4_STATUS,"PASS_PRODUCTION_ELIGIBLE")
        self.assertEqual(V5_POLICY_GATE_STATUS,"PASS")
        art=validate_v5_production_artifacts()
        self.assertEqual(art["policy_gate_status"],"PASS")
        self.assertEqual(art["selected_policy"]["policy_id"],"P_R01_005_B06")
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            out=run_v5_local_production(
                2,
                remaining_sixth_promo_rmb=None,
                ledger_path=root/"ledger.jsonl",
                output_root=root/"out",
                now_utc=datetime.fromisoformat("2026-11-25T00:00:00+00:00"),
                seed=bytes.fromhex("22"*32),
            )
            self.assertEqual(out["effective_route_mode"],"UNIFORM_MARGINAL_BALANCED_COVERAGE")
            self.assertEqual(out["fallback_reason"],"LOCAL_PROMO_REMAINING_RMB_REQUIRED")

    def test_09_append_new_draw_does_not_invalidate_frozen_training_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"ssq_history.csv"
            original=Path("data/ssq_history.csv").read_bytes()
            self.assertTrue(original.endswith(b"\n"))
            p.write_bytes(
                original
                + b"2026136,2026-11-26,3,9,14,18,25,31,6,410000000,1130000000,8,166,1800,90000,1600000,12000000,1\n"
            )
            art=validate_v5_production_artifacts(p)
            self.assertEqual(art["trained_through_issue"],"2026135")
            self.assertNotEqual(art["current_history_csv_sha256"],art["training_history_prefix_sha256"])
            # Full production continuation additionally requires the previous
            # issue's committed ledger state; that is covered by the dedicated
            # production-shell E2E test. This regression only proves append-safe
            # training-prefix artifact verification.


if __name__=="__main__":
    unittest.main()

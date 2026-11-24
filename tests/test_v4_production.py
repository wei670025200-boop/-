import math
import unittest
from datetime import datetime, timezone, timedelta

from v4.runner_v4 import (
    BLUE_COUNT,
    MAX_TICKETS_PER_RUN,
    PromotionRule,
    V4Error,
    construct_abstract_red_blocks,
    evaluate_promotion_eligibility,
    generate_balanced_batch,
    validate_production_context,
)

FIXED_SEED = bytes.fromhex("42"*32)

class V4ProductionTests(unittest.TestCase):
    def test_n1_to_5_red_zero_overlap(self):
        for n in range(1,6):
            rec=generate_balanced_batch(n,seed=FIXED_SEED)
            sets=[set(t["reds"]) for t in rec["tickets"]]
            for i in range(n):
                for j in range(i+1,n):
                    self.assertFalse(sets[i] & sets[j])

    def test_blue_no_repeat_up_to_16(self):
        for n in (1,2,5,8,15,16):
            rec=generate_balanced_batch(n,seed=FIXED_SEED)
            blues=[t["blue"] for t in rec["tickets"]]
            self.assertEqual(len(blues),len(set(blues)))

    def test_blue_round_balance_over_16(self):
        for n in (17,31,32,33,100):
            rec=generate_balanced_batch(n,seed=FIXED_SEED)
            counts=list(rec["blue_usage"].values())
            self.assertLessEqual(max(counts)-min(counts),1)

    def test_distinct_complete_tickets(self):
        for n in (2,5,16,50,200):
            rec=generate_balanced_batch(n,seed=FIXED_SEED)
            keys={(tuple(t["reds"]),t["blue"]) for t in rec["tickets"]}
            self.assertEqual(len(keys),n)

    def test_abstract_constructor_is_bounded_and_unique(self):
        blocks=construct_abstract_red_blocks(500)
        self.assertEqual(len(blocks),500)
        self.assertEqual(len(set(blocks)),500)

    def test_runtime_cap(self):
        with self.assertRaises(V4Error):
            generate_balanced_batch(MAX_TICKETS_PER_RUN+1,seed=FIXED_SEED)

    def test_reproducible_given_seed(self):
        a=generate_balanced_batch(25,seed=FIXED_SEED,request_time_utc="2026-11-24T00:00:00+00:00")
        b=generate_balanced_batch(25,seed=FIXED_SEED,request_time_utc="2026-11-24T00:00:00+00:00")
        self.assertEqual(a["tickets"],b["tickets"])
        self.assertEqual(a["output_sha256"],b["output_sha256"])

    def test_cross_year_no_arithmetic_previous_issue(self):
        now=datetime(2026,12,31,tzinfo=timezone.utc)
        context={
          "official_sources":[
            {"source_verified":True,"latest_completed_issue":"2026151","latest_completed_draw_date":"2026-12-31","latest_completed_reds":[1,2,3,4,5,6],"latest_completed_blue":7},
            {"source_verified":True,"latest_completed_issue":"2026151","latest_completed_draw_date":"2026-12-31","latest_completed_reds":[1,2,3,4,5,6],"latest_completed_blue":7},
          ],
          "target_schedule":{"source_verified":True,"target_issue":"2027001","draw_at_utc":"2027-01-03T13:15:00+00:00"},
          "market_status":{"source_verified":True,"sales_open":True,"suspension_status":"NO_SUSPENSION_CONFIRMED"},
        }
        out=validate_production_context(context,now)
        self.assertEqual(out["latest_completed_issue"],"2026151")
        self.assertEqual(out["target_issue"],"2027001")
        self.assertTrue(out["cross_year_safe"])

    def test_suspension_unknown_fails_closed(self):
        now=datetime(2027,1,1,tzinfo=timezone.utc)
        context={
          "official_sources":[
            {"source_verified":True,"latest_completed_issue":"2026151","latest_completed_draw_date":"2026-12-31","latest_completed_reds":[1,2,3,4,5,6],"latest_completed_blue":7},
            {"source_verified":True,"latest_completed_issue":"2026151","latest_completed_draw_date":"2026-12-31","latest_completed_reds":[1,2,3,4,5,6],"latest_completed_blue":7},
          ],
          "target_schedule":{"source_verified":True,"target_issue":"2027001","draw_at_utc":"2027-01-03T13:15:00+00:00"},
          "market_status":{"source_verified":True,"sales_open":True,"suspension_status":"UNKNOWN"},
        }
        with self.assertRaises(V4Error):
            validate_production_context(context,now)

    def test_promotion_unknown_does_not_create_ev_uplift(self):
        rule=PromotionRule(active=True,source_verified=False,min_physical_ticket_amount_rmb=20)
        out=evaluate_promotion_eligibility(10,[10],rule)
        self.assertEqual(out["status"],"UNKNOWN_BLOCK_EV_UPLIFT")
        self.assertEqual(out["eligible_ticket_indices"],[])

    def test_promotion_threshold_applies_per_physical_slip(self):
        rule=PromotionRule(
            active=True,source_verified=True,source_url="https://official.example/promo",
            source_sha256="a"*64,min_physical_ticket_amount_rmb=20,
            eligible_bet_types=("single",),eligible_prize_tiers=("sixth",),
        )
        a=evaluate_promotion_eligibility(10,[10],rule)
        b=evaluate_promotion_eligibility(10,[5,5],rule)
        self.assertEqual(a["eligible_count"],10)
        self.assertEqual(b["eligible_count"],0)

if __name__=="__main__":
    unittest.main()

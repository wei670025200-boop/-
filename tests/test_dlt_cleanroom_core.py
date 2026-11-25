import unittest
from itertools import combinations
from math import log
from dlt_v5 import prereg_core as c, statutory_liability as s, PHASE4_STATUS, FULL_SYSTEM_READY

class CleanroomCore(unittest.TestCase):
    def test_import(self): self.assertEqual((PHASE4_STATUS,FULL_SYSTEM_READY),('UNLOCKED',True))
    def test_counts(self): self.assertEqual(c.theory_counts(),c.COUNTS)
    def test_space(self): self.assertEqual(c.SPACE,21425712)
    def test_win_count(self): self.assertEqual(sum(c.COUNTS),1429197)
    def test_targets(self): self.assertEqual(c.targets(2*c.SPACE,dict(zip(['g4','g5','g6','g7'],c.COUNTS[3:]))),(0.,0.))
    def test_zero_counts(self): self.assertTrue(all(x<0 for x in c.targets(2*c.SPACE,dict.fromkeys(['g4','g5','g6','g7'],0))))
    def test_bad_sales(self):
        for v in (0,-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError): c.targets(v,{})
    def test_bad_count(self):
        for v in (-1,1.5,float('nan'),True):
            with self.assertRaises(ValueError): c.targets(2,dict.fromkeys(['g4','g5','g6','g7'],v))
    def test_front_features(self):
        self.assertEqual(c.front_features([6,14,18,25,30],previous=[5,14,19,25,31],calendar_pulse_numbers=[6,25],omit_chase_numbers=[18,30]),(1,0.,.4,.2,.4,0,.4,.4,.6,.4,0.,0.))
    def test_front_box_positive(self): self.assertEqual(c.front_features([6,14,19,25,30],previous=[1,2,3,4,5],calendar_pulse_numbers=[],omit_chase_numbers=[])[5],1)
    def test_front_month_excess(self): self.assertEqual(c.front_features([1,2,3,4,5],previous=[1,2,3,4,5],calendar_pulse_numbers=[],omit_chase_numbers=[])[1],1.)
    def test_front_same_tail(self): self.assertEqual(c.front_features([1,11,21,31,32],previous=[1,2,3,4,5],calendar_pulse_numbers=[],omit_chase_numbers=[])[11],.6)
    def test_front_adj(self): self.assertEqual(c.front_features([1,2,3,4,5],previous=[1,2,3,4,5],calendar_pulse_numbers=[],omit_chase_numbers=[])[10],1.)
    def test_mask_adj(self): self.assertIn('ADJ_PAIRS_GE3',c.front_exclusion_reasons([1,2,3,4,30]))
    def test_mask_adj_split(self): self.assertIn('ADJ_PAIRS_GE3',c.front_exclusion_reasons([1,2,3,10,11]))
    def test_mask_ap(self): self.assertIn('ARITHMETIC_PROGRESSION',c.front_exclusion_reasons([3,9,15,21,27]))
    def test_mask_parity(self): self.assertIn('SAME_PARITY',c.front_exclusion_reasons([1,5,13,25,33]))
    def test_mask_half_low(self): self.assertIn('EXTREME_HALF',c.front_exclusion_reasons([1,4,7,11,17]))
    def test_mask_half_high(self): self.assertIn('EXTREME_HALF',c.front_exclusion_reasons([18,20,23,27,31]))
    def test_mask_overload(self): self.assertIn('HIGH_32_35_OVERLOAD',c.front_exclusion_reasons([1,13,32,34,35]))
    def test_no_mask(self): self.assertEqual(c.front_exclusion_reasons([6,14,18,25,30]),())
    def test_back(self): self.assertEqual(c.back_features([6,7],previous=[4,6],omit_chase_numbers=[7]),(1,1,0,1.,0,0.,.5,.5,1))
    def test_back_edges(self): self.assertEqual(c.back_features([1,12],previous=[1,2],omit_chase_numbers=[])[5],1.)
    def test_back_all_pairs(self): self.assertEqual(len([c.back_features(p,previous=[1,2],omit_chase_numbers=[]) for p in combinations(range(1,13),2)]),66)
    def test_invalid_balls(self):
        with self.assertRaises(ValueError): c.front_exclusion_reasons([1,1,2,3,4])
    def test_grid(self): self.assertEqual(len(set(c.POLICY_GRID)),9)
    def test_expand(self): self.assertEqual([c.effective_back_k(18,n) for n in (2,18,19,66,70)],[18,18,19,66,66])
    def test_dimensions(self): self.assertEqual((len(c.FRONT_NAMES),len(c.BACK_NAMES)),(12,9))
    def test_promo_zero(self):
        o={'front':[1,2,3,4,5],'back':[1,2]}; r=s.plug_in_statutory_rmb_exposure([o],o,0,1000,0,0,predicted_promo_extra_rmb=500)
        self.assertEqual((r['base_total_after_caps_rmb'],r['promo_extra_after_cap_rmb'],r['total_after_caps_rmb']),(1000,0,1000))
    def test_promo_partial(self):
        o={'front':[1,2,3,4,5],'back':[1,2]}; r=s.plug_in_statutory_rmb_exposure([o],o,0,1000,0,100,predicted_promo_extra_rmb=500)
        self.assertEqual(r['total_after_caps_rmb'],1100)
    def test_promo_uncapped(self):
        o={'front':[1,2,3,4,5],'back':[1,2]}; r=s.plug_in_statutory_rmb_exposure([o],o,0,1000,0,600,predicted_promo_extra_rmb=500)
        self.assertEqual(r['total_after_caps_rmb'],1500)
    def test_promo_missing_balance(self):
        with self.assertRaisesRegex(ValueError,'PROMO_BALANCE_REQUIRED'): s.plug_in_statutory_rmb_exposure([],{'front':[],'back':[]},0,0,0,predicted_promo_extra_rmb=1)
    def test_promo_negative(self):
        with self.assertRaises(ValueError): s.plug_in_statutory_rmb_exposure([],{'front':[],'back':[]},0,0,0,0,predicted_promo_extra_rmb=-1)

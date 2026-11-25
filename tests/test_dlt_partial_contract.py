"""Independent partial recovery checks. Not the original 42 tests."""
import unittest
from dlt_v5 import history_contract as h, statutory_liability as s

def prizes(n):
    return [(c+'等奖',i+1) for i,c in enumerate('一二三四五六七八九'[:n])]

class PartialContract(unittest.TestCase):
    def test_22_fields(self): self.assertEqual(len(h.HISTORY_FIELDS),22)
    def test_legacy(self):
        r=h.normalize_prize_rows('26013','2026-01-31',prizes(9))
        self.assertEqual([r['g'+str(i)] for i in range(3,8)],['7','5','13','8','9'])
    def test_new(self):
        r=h.normalize_prize_rows('26014','2026-02-02',prizes(7))
        self.assertEqual([r['g'+str(i)] for i in range(3,8)],['3','4','5','6','7'])
    def test_conflict_old_after_cutoff(self):
        with self.assertRaisesRegex(ValueError,'RULE_EPOCH_CONFLICT'): h.normalize_prize_rows('26014','2026-02-02',prizes(9))
    def test_conflict_new_before_cutoff(self):
        with self.assertRaisesRegex(ValueError,'RULE_EPOCH_CONFLICT'): h.normalize_prize_rows('26013','2026-01-31',prizes(7))
    def test_conflict_date_issue(self):
        with self.assertRaisesRegex(ValueError,'RULE_EPOCH_CONFLICT'): h.normalize_prize_rows('26013','2026-02-02',prizes(7))
    def test_additional_aliases(self):
        for a in ['(追加)','（追加）','追加']:
            r=h.normalize_prize_rows('26014','2026-02-02',prizes(7)+[('一等奖'+a,3),('二等奖'+a,0)])
            self.assertEqual((r['g1_add'],r['g2_add']),('3','0'))
    def test_promo(self):
        for value in [0,4]:
            r=h.normalize_prize_rows('26014','2026-02-02',prizes(7)+[('一等奖派奖',value)])
            self.assertEqual((r['g1'],r['promo_active']),('1',str(int(value>0))))
    def test_duplicate(self):
        with self.assertRaisesRegex(ValueError,'DUPLICATE_PRIZE_LABEL'): h.normalize_prize_rows('26014','2026-02-02',prizes(7)+[('一等奖',2)])
    def test_bad_counts(self):
        for v in [-1,'1.5','NaN','Infinity']:
            with self.assertRaisesRegex(ValueError,'INVALID_STAKE_COUNT'): h.normalize_prize_rows('26014','2026-02-02',[('一等奖',v)])
    def test_missing_is_not_zero(self):
        r=h.normalize_prize_rows('26014','2026-02-02',prizes(7));self.assertEqual(r['g1_add'],'')
    def test_legacy_zero(self):
        r=h.normalize_legacy_9tier_row({'issue':'26013','date':'2026-01-31','t1':0,'t8':0,'t9':0,'promo_active':0})
        self.assertEqual((r['g1'],r['g6'],r['g7'],r['promo_active']),('0','0','0','0'))
    def test_threshold(self):
        self.assertEqual(list(s.fixed_awards(799999999).values()),[5000,300,150,15,5]);self.assertEqual(list(s.fixed_awards(800000000).values()),[6666,380,200,18,7]);self.assertEqual(s.fixed_awards(799999999)['g3'],5000)
    def test_negative_promo(self):
        with self.assertRaisesRegex(ValueError,'INVALID_RMB'): s.plug_in_statutory_rmb_exposure([],{'front':[],'back':[]},0,0,0,-1)
    def test_invalid_rmb(self):
        for v in [-1,float('nan'),float('inf')]:
            with self.assertRaisesRegex(ValueError,'INVALID_RMB'): s.fixed_awards(v)
    def test_caps_and_additional(self):
        o={'front':[1,2,3,4,5],'back':[1,2]}
        t=[{**o,'is_additional_bet':True},{'front':o['front'],'back':[1,3],'is_additional_bet':True}]
        r=s.plug_in_statutory_rmb_exposure(t,o,0,100000000,100000000)
        self.assertEqual((r['g1_after_aggregate_cap_rmb'],r['g2_after_aggregate_cap_rmb']),(100000000,100000000));self.assertEqual(r['ticket_rows'][0]['raw_statutory_rmb'],180000000)
        self.assertEqual(s.plug_in_statutory_rmb_exposure(t,o,0,100000000,100000000,10)['total_after_caps_rmb'],200000000)

if __name__=='__main__': unittest.main(verbosity=2)

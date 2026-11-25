import unittest
from itertools import combinations
from dlt_v5 import balanced_coverage as b
from dlt_v5.prereg_core import SPACE, COUNTS
SEED=bytes(range(32))
class BalancedCoverage(unittest.TestCase):
    def test_deterministic(self): self.assertEqual(b.balanced_tickets(8,SEED),b.balanced_tickets(8,SEED))
    def test_domain(self): self.assertNotEqual(b.DomainDRBG(SEED,b'A').permutation(35),b.DomainDRBG(SEED,b'B').permutation(35))
    def test_bad_seed(self):
        with self.assertRaises(ValueError): b.balanced_tickets(1,b'bad')
    def test_invalid_n(self):
        for n in (0,-1,True,1.5):
            with self.assertRaises(ValueError): b.balanced_tickets(n,SEED)
    def test_front_disjoint(self):
        for n in range(1,8): self.assertEqual(len(set(x for f,_ in b.balanced_tickets(n,SEED) for x in f)),5*n)
    def test_back_disjoint(self):
        for n in range(1,7): self.assertEqual(len(set(x for _,p in b.balanced_tickets(n,SEED) for x in p)),2*n)
    def test_back_cycle(self): self.assertEqual(set(b.back_cycle()),set(combinations(range(12),2)))
    def test_back_rounds(self):
        for i in range(0,66,6): self.assertEqual(len(set(x for p in b.back_cycle()[i:i+6] for x in p)),12)
    def test_balanced(self):
        for n in (8,13,66,67,100):
            for f in b.frequencies(b.balanced_tickets(n,SEED)): self.assertLessEqual(max(f)-min(f),1)
    def test_no_back_repeats(self):
        for n in (7,8,65,66): self.assertEqual(len(set(p for _,p in b.balanced_tickets(n,SEED))),n)
    def test_round_reset(self): self.assertEqual(len(set(p for _,p in b.balanced_tickets(132,SEED)[66:])),66)
    def test_single_exact(self): self.assertEqual(b.exact_batch_coverage(b.balanced_tickets(1,SEED))['winning_outcomes'],sum(COUNTS))
    def test_duplicate_exact(self):
        t=b.balanced_tickets(1,SEED);self.assertEqual(b.exact_batch_coverage(t+t)['winning_outcomes'],sum(COUNTS))
    def test_union_bounds(self):
        r=b.exact_batch_coverage(b.balanced_tickets(2,SEED));self.assertTrue(sum(COUNTS)<r['winning_outcomes']<=2*sum(COUNTS))
    def test_full_coverage(self):
        for n in (66,67):
            r=b.exact_batch_coverage(b.balanced_tickets(n,SEED));self.assertEqual((r['winning_outcomes'],r['prize_coverage'],r['profit_guarantee']),(SPACE,1.0,'NONE'))
    def test_exact_nonfull(self): self.assertLess(b.exact_batch_coverage(b.balanced_tickets(2,SEED))['prize_coverage'],1)
    def test_empty_batch(self):
        with self.assertRaises(ValueError): b.exact_batch_coverage([])

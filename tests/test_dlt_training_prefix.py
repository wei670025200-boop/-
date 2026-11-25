import unittest
import json
from pathlib import Path
from dlt_v5 import training_prefix as p, history_contract as h, statutory_liability as s

HEADER=(','.join(h.HISTORY_FIELDS)+'\n').encode()
def row(issue,day,sales='10000'):
    return (','.join([issue,day,'1','2','3','4','5','1','2',sales,'100000','1','1','2','0','3','4','5','6','7','7TIER_2026','0'])+'\n').encode()
A=row('26014','2026-02-02'); B=row('26015','2026-02-04'); C=row('26016','2026-02-07')
class PrefixTests(unittest.TestCase):
    def test_append(self): self.assertEqual(p.prefix_receipt(HEADER+A+B,'26015'),p.prefix_receipt(HEADER+A+B+C,'26015'))
    def test_verify_append(self): self.assertEqual(p.verify_prefix(HEADER+A+B+C,p.prefix_receipt(HEADER+A+B,'26015'))['prefix_rows'],2)
    def test_tamper_prefix(self):
        with self.assertRaisesRegex(ValueError,'TRAINING_PREFIX_MISMATCH'): p.verify_prefix(HEADER+row('26014','2026-02-02','999')+B,p.prefix_receipt(HEADER+A+B,'26015'))
    def test_suffix_not_hashed(self): self.assertEqual(p.prefix_receipt(HEADER+A+B+C,'26015'),p.prefix_receipt(HEADER+A+B+row('26016','2026-02-07','999'),'26015'))
    def test_missing_cutoff(self):
        with self.assertRaisesRegex(ValueError,'TRAINED_THROUGH_ISSUE_MISSING'): p.prefix_receipt(HEADER+A,'26015')
    def test_duplicate(self):
        with self.assertRaisesRegex(ValueError,'HISTORY_ORDER'): p.prefix_receipt(HEADER+A+A,'26014')
    def test_out_of_order(self):
        with self.assertRaisesRegex(ValueError,'HISTORY_ORDER'): p.prefix_receipt(HEADER+B+A,'26014')
    def test_bad_header(self):
        with self.assertRaisesRegex(ValueError,'HISTORY_HEADER_MISMATCH'): p.prefix_receipt(b'issue\n'+A,'26014')
    def test_unterminated(self):
        with self.assertRaisesRegex(ValueError,'INVALID_CSV_LINE'): p.prefix_receipt(HEADER+A.rstrip(b'\n'),'26014')
    def test_conflict(self):
        with self.assertRaisesRegex(ValueError,'RULE_EPOCH_CONFLICT'): p.prefix_receipt(HEADER+A.replace(b'7TIER_2026',b'9TIER_2019'),'26014')
    def test_wrong_receipt(self):
        with self.assertRaisesRegex(ValueError,'INVALID_PREFIX_RECEIPT'): p.verify_prefix(HEADER+A,{})
    def test_rule_json(self):
        rule=json.loads((Path(__file__).resolve().parents[1]/'data/dlt_statutory_rule.json').read_text())
        self.assertEqual(rule['fixed_below_threshold_rmb'],s.fixed_awards(799999999))
        self.assertEqual(rule['fixed_at_or_above_threshold_rmb'],s.fixed_awards(800000000))
        self.assertEqual((rule['promo_cap_scope'],rule['profit_guarantee']),('PROMO_EXTRA_ONLY','NONE'))

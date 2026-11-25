import unittest
from datetime import date
import numpy as np
from dlt_v5 import frozen_protocol as f, cusum_monitor as c, policy_selector as p
from test_dlt_digital_twin import fixture
from dlt_v5.digital_twin import oos_arrays

class FinalProtocol(unittest.TestCase):
    def test_holiday_before(self): self.assertEqual(f.calendar_pulse(date(2026,2,11)),{2,11,14})
    def test_holiday_after(self): self.assertEqual(f.calendar_pulse(date(2026,2,17)),{2,17,14})
    def test_holiday_outside(self): self.assertEqual(f.calendar_pulse(date(2026,2,18)),{2,18})
    def test_year_boundary(self): self.assertEqual(f.calendar_pulse(date(2026,12,30)),{12,30,1})
    def test_christmas(self): self.assertEqual(f.calendar_pulse(date(2026,12,24)),{12,24,25})
    def test_double11(self): self.assertEqual(f.calendar_pulse(date(2026,11,10)),{10,11})
    def test_signal(self): self.assertEqual(f.official_oos_gate(oos_arrays(*fixture(True)))['oos_gate'],'PASS')
    def test_noise(self): self.assertEqual(f.official_oos_gate(oos_arrays(*fixture(False)))['oos_gate'],'FAIL')
    def test_combined_rule(self):
        rng=np.random.default_rng(123);a=rng.normal(size=240);base=np.zeros(240)
        x={'actual_front':a,'actual_back':a,'baseline_front':base,'baseline_back':base,'prediction_front':a*.8,'prediction_back':a*.01}
        self.assertEqual(f.official_oos_gate(x)['oos_gate'],'PASS')
        x['prediction_back']=-a*.01;self.assertEqual(f.official_oos_gate(x)['oos_gate'],'FAIL')
    def test_spearman_not_gate(self):
        a=np.linspace(-1,1,240);x={'actual_front':a,'actual_back':a+2,'baseline_front':np.ones(240)*5,'baseline_back':np.ones(240)*5,'prediction_front':a,'prediction_back':np.ones(240)*2}
        self.assertEqual(f.official_oos_gate(x)['oos_gate'],'PASS')
    def test_cusum_pass(self): self.assertEqual(c.evaluate([1]*20,c.reference([0,2],'26014'))['drift_gate'],'PASS')
    def test_cusum_trip(self): self.assertEqual(c.evaluate([1]*19+[6.5],c.reference([0,2],'26014'))['drift_gate'],'TRIPPED')
    def test_sigma_floor(self): self.assertEqual(c.reference([1,1],'26014')['sigma0'],1e-6)
    def test_cusum_short(self):
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_DRIFT_ROWS'): c.evaluate([1]*19,c.reference([0,2],'26014'))
    def test_cusum_uses_trajectory(self): self.assertEqual(c.evaluate([10]+[1]*19,c.reference([0,2],'26014'))['drift_gate'],'TRIPPED')
    def test_cusum_prior(self): self.assertEqual(c.reference(c.errors([2,4],[0,0],[4,2],[0,0]),'26014')['mu0'],3)
    def test_integrity(self):
        d={k:f.sha256(k.encode()) for k in f.DIGEST_KEYS};r=f.make_integrity(d,'a'*40);self.assertTrue(f.verify_integrity(r,d));self.assertFalse(r['digital_signature'])
        r2=f.make_integrity(d,'b'*40);self.assertEqual(r['integrity_summary_sha256'],r2['integrity_summary_sha256'])
    def test_integrity_tamper(self):
        d={k:f.sha256(k.encode()) for k in f.DIGEST_KEYS};r=f.make_integrity(d,'a'*40);d[f.DIGEST_KEYS[0]]='0'*64
        with self.assertRaisesRegex(ValueError,'INTEGRITY_MISMATCH'): f.verify_integrity(r,d)
    def test_integrity_missing(self):
        with self.assertRaises(ValueError): f.make_integrity({},'a'*40)
    def test_policy_pass(self):
        n=400;f_rank=np.tile([.03,.5,.5,.5],100);b_rank=np.tile([1,12,60,65],100)
        a=np.where(f_rank<.2,-2.,2.);b=np.where(b_rank<=24,-2.,2.)
        r=p.nested_select(a,b,f_rank,b_rank,2);self.assertEqual(r['policy_gate'],'PASS');self.assertGreaterEqual(r['valid_resample_fraction'],.95)
    def test_policy_no_hits(self):
        n=220;r=p.nested_select(np.ones(n),np.ones(n),np.full(n,.9),np.full(n,66),2);self.assertEqual(r['policy_gate'],'FAIL')
    def test_policy_no_signal(self):
        n=400;f_rank=np.tile([.03,.5,.5,.5],100);b_rank=np.tile([1,12,60,65],100)
        r=p.nested_select(np.ones(n),np.ones(n),f_rank,b_rank,2);self.assertEqual(r['policy_gate'],'FAIL')
    def test_policy_no_current_leakage(self):
        n=220;fr=np.tile([.03,.5,.5,.5],55);br=np.tile([1,12,60,65],55);a=np.where(fr<.2,-2.,2.);b=np.where(br<=24,-2.,2.)
        first=p.nested_select(a,b,fr,br,2)['trace'][0];a[100:]+=99;b[100:]-=99
        self.assertEqual(first,p.nested_select(a,b,fr,br,2)['trace'][0])
    def test_policy_mask(self):
        n=220;r=p.nested_select(np.ones(n),np.ones(n),np.full(n,.03),np.ones(n),2,np.zeros(n,dtype=bool));self.assertEqual(r['front_hits'],0)

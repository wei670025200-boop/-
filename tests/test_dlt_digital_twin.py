import unittest
import numpy as np
from dlt_v5 import digital_twin as d
from dlt_v5.prereg_core import PARAMS

def fixture(signal):
    r=np.random.default_rng(20261125); n=260
    xf=r.uniform(size=(n,12)); xb=r.uniform(size=(n,9)); pairs=[tuple(sorted(r.choice(np.arange(1,13),2,replace=False).tolist())) for _ in range(n)]
    yf=r.normal(0,.05 if signal else 1,n); yb=r.normal(0,.05 if signal else 1,n)
    if signal: yf+=xf@np.linspace(1,3,12); yb+=xb@np.linspace(1,3,9)
    return xf,yf,xb,yb,pairs
class DigitalTwin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signal=fixture(True); cls.noise=fixture(False)
        cls.signal_arrays=d.oos_arrays(*cls.signal); cls.noise_arrays=d.oos_arrays(*cls.noise)
    def test_ridge_shape(self): self.assertEqual(len(d.fit_ridge(self.signal[0],self.signal[1],15).weights),12)
    def test_bad_alpha(self):
        with self.assertRaises(ValueError): d.fit_ridge([[1],[2]],[1,2],0)
    def test_bad_training(self):
        with self.assertRaises(ValueError): d.fit_ridge([[float('nan')]],[1],15)
    def test_winsor(self):
        y=np.arange(101); m=d.fit_ridge(np.zeros((101,1)),y,15); self.assertEqual((m.winsor_low,m.winsor_high),(2.,98.))
    def test_eb_dimensions(self): self.assertEqual(len(d.fit_back(self.signal[2],self.signal[3],self.signal[4]).ball_bias),12)
    def test_eb_unseen_zero(self):
        m=d.fit_back(np.zeros((10,9)),np.arange(10),[(1,2)]*10);self.assertEqual(m.ball_bias[2:],(0.,)*10)
    def test_oos_length(self): self.assertEqual(len(self.signal_arrays['prediction_front']),60)
    def test_no_future_leakage(self):
        xf,yf,xb,yb,p=self.signal; yf=yf.copy(); yb=yb.copy(); yf[200:]+=1000;yb[200:]+=1000
        a=d.oos_arrays(xf,yf,xb,yb,p)
        self.assertEqual(a['prediction_front'][0],self.signal_arrays['prediction_front'][0]);self.assertEqual(a['prediction_back'][0],self.signal_arrays['prediction_back'][0])
    def test_signal_gate(self): self.assertEqual(d.oos_diagnostics(self.signal_arrays)['provisional_oos_gate'],'PASS')
    def test_noise_gate(self): self.assertEqual(d.oos_diagnostics(self.noise_arrays)['provisional_oos_gate'],'FAIL')
    def test_mbb_reproducible(self): self.assertEqual(d.oos_diagnostics(self.signal_arrays),d.oos_diagnostics(self.signal_arrays))
    def test_mbb_settings(self):
        r=d.oos_diagnostics(self.signal_arrays)['front'];self.assertEqual((r['B'],r['block_length'],r['seed']),(5000,12,2026112501))
    def test_constant_spearman(self):
        a=dict(self.noise_arrays);a['prediction_back']=[0.]*60; self.assertIsNone(d.oos_diagnostics(a)['back_spearman']['rho'])
    def test_degenerate_baseline(self):
        with self.assertRaisesRegex(ValueError,'DEGENERATE_BASELINE_MSE'): d.mse_diagnostic([1]*20,[1]*20,[1]*20,1)
    def test_omission_ties(self): self.assertEqual(d.omission_set([(3,4)],5,12),{1,2})
    def test_omission_prior(self): self.assertIn(3,d.omission_set([(1,2)]*15,5,12,20))
    def test_rankings(self):
        m=d.fit_back(self.signal[2],self.signal[3],self.signal[4]); r=d.latest_back_rankings(m,self.signal[4]);self.assertEqual(tuple(map(len,r.values())),(12,10,10));self.assertLessEqual(r['low_top10'][0]['score'],r['high_top10'][0]['score'])
    def test_requires_holiday_mapping(self):
        with self.assertRaisesRegex(ValueError,'HOLIDAY_PULSE_SPEC_REQUIRED'): d.feature_history([],None)

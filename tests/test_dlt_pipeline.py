import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from dlt_v5.prequential_rank import front_universe,dynamic_front_features,front_percentile,back_rank
from dlt_v5.prereg_core import front_features,front_exclusion_reasons
from dlt_v5.digital_twin import Ridge,BackModel
from dlt_v5.frozen_protocol import sha256,canonical_json
from dlt_v5.runner_v5 import evaluate_validators,execute_verified,GATES
from dlt_v5.research_pipeline import REFERENCE_OOS_ROWS,REFERENCE_TICKET_COUNT
from dlt_v5.engine_v4 import DLTV4Engine,CrashInjection,DuplicateIssue,FALLBACK
from dlt_v5.local_schedule import LocalSchedule
from run_step2_real_oos_once import verify_claim,consume_once
from cryptography.exceptions import InvalidSignature
ROOT=Path(__file__).resolve().parents[1]
class Pipeline(unittest.TestCase):
    def test_front_exhaustive_count(self):
        a,_,m=front_universe();self.assertEqual((len(a),int((~m).sum())),(324632,292887))
    def test_vector_scalar_parity(self):
        a,s,m=front_universe();idx=np.random.default_rng(1).choice(len(a),1000,replace=False);p=[1,6,13,20,31];pulse=[2,14,26];omit=[5,32];d=dynamic_front_features(a[idx],p,pulse,omit)
        for j,i in enumerate(idx):
            f=tuple(map(int,a[i]));v=s[i].copy();v[6:10]=d[j];np.testing.assert_allclose(v,front_features(f,previous=p,calendar_pulse_numbers=pulse,omit_chase_numbers=omit));self.assertEqual(bool(m[i]),bool(front_exclusion_reasons(f)))
    def test_front_rank_tie(self):
        a,s,m=front_universe();f=tuple(map(int,a[np.flatnonzero(~m)[0]]));r,ok,n=front_percentile(Ridge(0,(0,)*12,0,0),f,[1,2,3,4,5],date(2026,2,2),{1});self.assertTrue(ok);self.assertEqual(r,1/n)
    def test_front_mask(self): self.assertEqual(front_percentile(Ridge(0,(0,)*12,0,0),(1,2,3,4,5),[1,2,3,4,5],date(2026,2,2),{1})[:2],(1.,False))
    def test_back_tie(self): self.assertEqual(back_rank(BackModel(Ridge(0,(0,)*9,0,0),(0,)*12),(1,2),[1,2],{1}),1)
    def test_reference_domain(self):self.assertEqual((REFERENCE_OOS_ROWS,REFERENCE_TICKET_COUNT),(100,18))
    def test_gate_errors_independent(self):
        def bad():raise ValueError('broken')
        for failed in GATES:
            v={g:bad if g==failed else lambda:'PASS' for g in GATES};m=Mock();m.execute.return_value={};r=execute_verified(m,'26014','2026-02-02',2,v,dry_run=True);self.assertEqual(r['gate_decision']['reason'],failed);self.assertEqual(r['gate_detail'][failed]['status'],'FAIL');m.execute.assert_called_once_with('26014','2026-02-02',2,additional=False,route=FALLBACK,dry_run=True)
    def test_missing_validators(self):self.assertTrue(all(v=='FAIL' for _,v in evaluate_validators({})[0]))
    def test_claim(self):
        raw=b'ledger';spec=b'frozen';env={'GITHUB_RUN_ID':'42','GITHUB_RUN_ATTEMPT':'1','DLT_SOURCE_COMMIT_SHA':'a'*40};claim={'schema':'DLT_REAL_OOS_ONCE_CLAIM_V1','run_id':'42','run_attempt':'1','csv_sha256':sha256(raw),'prereg_spec_sha256':sha256(spec),'source_commit_sha':'a'*40};self.assertEqual(verify_claim(claim,raw,spec,env),claim)
        with self.assertRaisesRegex(ValueError,'OOS_ONCE_CLAIM_INVALID'):verify_claim(claim,raw+b'1',spec,env)
        with self.assertRaisesRegex(ValueError,'OOS_ONCE_CLAIM_INVALID'):verify_claim(claim,raw,spec,{**env,'GITHUB_RUN_ATTEMPT':'2'})
    def test_consume_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'marker';consume_once(p,{'test':'synthetic'});self.assertEqual(p.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):consume_once(p,{'test':'synthetic'})
    def test_stage_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=DLTV4Engine(tmp,LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json'))
            with self.assertRaises(CrashInjection):e.execute('26014','2026-02-02',2,crash_after='STAGE')
            (Path(tmp)/'pending/26014/seed.bin').write_bytes(b'x'*32)
            with self.assertRaises(InvalidSignature):e.recover_staged_publish()
    def test_dry_run_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=DLTV4Engine(tmp,LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json'));e.execute('26014','2026-02-02',2)
            with self.assertRaises(DuplicateIssue):e.execute('26014','2026-02-02',2,dry_run=True)
    def test_actual_engine_four_fallbacks(self):
        for failed in GATES:
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'engine';e=DLTV4Engine(root,LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json'));v={g:(lambda g=g:'FAIL' if g==failed else 'PASS') for g in GATES}
                r=execute_verified(e,'26014','2026-02-02',2,v,dry_run=True)
                self.assertEqual(r['route'],FALLBACK);self.assertEqual(r['gate_decision']['reason'],failed);self.assertEqual(r['artifacts']['canonical_commitment.json']['payload']['route'],FALLBACK);self.assertFalse(root.exists())

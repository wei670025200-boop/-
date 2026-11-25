import tempfile
import unittest
from pathlib import Path

from dlt_v5.engine_v4 import DLTV4Engine,FALLBACK,V5_ROUTE
from dlt_v5.history_validation import validate_history
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.prereg_core import front_exclusion_reasons
from dlt_v5.production_v5 import EXPECTED_INTEGRITY,load_production_artifact,load_runtime,production_validators
from dlt_v5.runner_v5 import GATES,execute_verified

ROOT=Path(__file__).resolve().parents[1]
SCHEDULE=LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json')

class ProductionV5(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifact=load_production_artifact(ROOT)
        cls.raw=(ROOT/'data/dlt_history.csv').read_bytes()

    def test_frozen_production_credential(self):
        self.assertEqual(self.artifact['phase4_status'],'UNLOCKED')
        self.assertEqual(self.artifact['candidate'],{'candidate':0,'front_band':[.01,.05],'back_k':18})
        self.assertEqual(self.artifact['integrity']['integrity_summary_sha256'],EXPECTED_INTEGRITY)
        self.assertFalse(self.artifact['integrity']['digital_signature'])

    def test_append_does_not_invalidate_training_prefix(self):
        suffix=b'26135,2026-11-25,1,2,3,4,5,1,2,100000000,100000000,1,0,2,0,3,4,5,100000,1000000,7TIER_2026,0\n'
        validators=production_validators(ROOT,SCHEDULE,self.artifact,self.raw+suffix,5)
        self.assertEqual(validators['DATA_GATE'](),'PASS')

    def test_all_gates_green_real_v5_dry_run(self):
        artifact,factory,validators=load_runtime(ROOT,SCHEDULE,5)
        with tempfile.TemporaryDirectory() as tmp:
            r=execute_verified(DLTV4Engine(Path(tmp)/'engine',SCHEDULE,factory),'26135','2026-11-25',5,validators,dry_run=True)
        self.assertEqual((r['selected_strategy'],r['gate_decision']['reason']),(V5_ROUTE,'ALL_GATES_PASS'))
        payload=r['artifacts']['canonical_commitment.json']['payload'];self.assertEqual(payload['route'],V5_ROUTE)
        self.assertEqual(len(payload['tickets']),5);self.assertFalse(any(front_exclusion_reasons(tuple(x['front'])) for x in payload['tickets']))

    def test_scope_exceeded_real_v4_fallback(self):
        _,factory,validators=load_runtime(ROOT,SCHEDULE,20)
        with tempfile.TemporaryDirectory() as tmp:
            r=execute_verified(DLTV4Engine(Path(tmp)/'engine',SCHEDULE,factory),'26135','2026-11-25',20,validators,dry_run=True)
        self.assertEqual((r['selected_strategy'],r['gate_decision']['reason']),(FALLBACK,'POLICY_SCOPE_EXCEEDED_FALLBACK_V4'))
        self.assertEqual(r['artifacts']['canonical_commitment.json']['payload']['route'],FALLBACK)

    def test_each_single_gate_failure_falls_back(self):
        _,factory,validators=load_runtime(ROOT,SCHEDULE,2)
        for failed in GATES:
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as tmp:
                current=dict(validators);current[failed]=lambda:'FAIL'
                r=execute_verified(DLTV4Engine(Path(tmp)/'engine',SCHEDULE,factory),'26135','2026-11-25',2,current,dry_run=True)
                self.assertEqual((r['selected_strategy'],r['gate_decision']['reason']),(FALLBACK,failed))

if __name__=='__main__':unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from dlt_v5.engine_v4 import DLTV4Engine, ARTIFACTS, CrashInjection, DuplicateIssue,FALLBACK
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.runner_v5 import GATES,execute_locked,route_gates
from dlt_v5.frozen_protocol import sha256
ROOT=Path(__file__).resolve().parents[1]
S=LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json')
class Transaction(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'engine';self.engine=DLTV4Engine(self.root,S)
    def tearDown(self):self.tmp.cleanup()
    def issue(self,**kw):return self.engine.execute('26014','2026-02-02',2,**kw)
    def test_publish(self):
        r=self.issue();p=Path(r['path']);self.assertEqual(set(x.name for x in p.iterdir()),set(ARTIFACTS));self.assertEqual(json.loads((p/ARTIFACTS[1]).read_bytes())['total_rmb'],4)
    def test_additional(self):
        r=self.issue(additional=True);self.assertEqual(json.loads((Path(r['path'])/ARTIFACTS[1]).read_bytes())['total_rmb'],6)
    def test_duplicate(self):
        self.issue()
        with self.assertRaisesRegex(DuplicateIssue,'REFUSE_DUPLICATE_ISSUE'):self.issue()
    def test_key_and_seed_mode(self):
        self.issue();self.assertEqual((self.root/'private/ed25519.key').stat().st_mode&0o777,0o600);self.assertEqual((self.root/'private/26014.seed').stat().st_mode&0o777,0o600)
    def test_ledger_next(self):
        a=self.issue();b=self.engine.execute('26015','2026-02-04',2);self.assertEqual(b['entry']['prev_sha256'],a['entry']['entry_sha256'])
    def test_ledger_gap(self):
        self.issue()
        with self.assertRaisesRegex(ValueError,'ISSUE_NOT_NEXT'):self.engine.execute('26016','2026-02-07',2)
    def test_tampered_published(self):
        self.issue();(self.root/'published/26014/physical_slip_plan.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'ARTIFACT_HASH_MISMATCH'):self.engine.execute('26015','2026-02-04',2)
    def test_tampered_ledger(self):
        self.issue();p=self.root/'ledger.jsonl';p.write_bytes(p.read_bytes().replace(b'26014',b'26015'))
        with self.assertRaisesRegex(ValueError,'LEDGER_CHAIN_INVALID'):self.engine.execute('26015','2026-02-04',2)
    def test_dry_run(self):
        r=self.issue(dry_run=True);self.assertEqual(r['status'],'DRY_RUN');self.assertFalse(self.root.exists());self.assertEqual(set(r['artifacts']),set(ARTIFACTS))
    def test_unknown_year(self):
        with self.assertRaisesRegex(ValueError,'SCHEDULE_YEAR_UNREGISTERED'):self.engine.execute('27001','2027-01-02',2)
    def test_recovery(self):
        for phase in ('STAGE','HASH VERIFY','LEDGER COMMIT','ATOMIC PUBLISH'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as tmp:
                e=DLTV4Engine(tmp,S)
                with self.assertRaises(CrashInjection):e.execute('26014','2026-02-02',2,crash_after=phase)
                seed=(Path(tmp)/'pending/26014/seed.bin').read_bytes()
                r=e.recover_staged_publish();p=Path(r['path']);c=json.loads((p/ARTIFACTS[0]).read_bytes());self.assertEqual(c['payload']['seed_sha256'],sha256(seed));self.assertEqual(len((Path(tmp)/'ledger.jsonl').read_text().splitlines()),1);self.assertIsNone(e.recover_staged_publish())
    def test_independent_lock_failures(self):
        for failed in GATES:
            m=Mock();m.execute.return_value={'status':'DRY_RUN'};g=[(x,'FAIL' if x==failed else 'PASS') for x in GATES];r=execute_locked(m,'26014','2026-02-02',2,g,dry_run=True)
            self.assertEqual(r['route'],FALLBACK);self.assertEqual(r['gate_decision']['reason'],failed);m.execute.assert_called_once_with('26014','2026-02-02',2,additional=False,route=FALLBACK,dry_run=True)
    def test_malformed_gate(self):
        for gates in (None,[],[('DATA_GATE','PASS')]*4):self.assertFalse(route_gates(gates)['valid'])
    def test_locked_all_pass(self):
        m=Mock();m.execute.return_value={};r=execute_locked(m,'26014','2026-02-02',2,[(x,'PASS') for x in GATES]);self.assertEqual(r['route'],FALLBACK);self.assertEqual(r['gate_decision']['reason'],'PHASE4_LOCKED')

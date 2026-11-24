import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from registry_and_parsers_v4 import (
    GateBlockedError,
    _parse_shanghai_draw_html,
    _parse_sichuan_draw_html,
    _parse_suspension_raw,
    load_schedule_manifest_evidence,
    verify_2_of_n_official_quorum,
)
from runner_v4 import AppendOnlyLedger, run_v4_production
from test_v4_regression import V3_HEAD, V4TestFixtureMixin

EVID_ROOT = Path('/tmp/ssq_evidence')

class TestV4RealOfficialIntegration(unittest.TestCase):
    def test_real_shanghai_raw_current(self):
        d = _parse_shanghai_draw_html((EVID_ROOT/'shanghai_ssq_previous.body').read_bytes())
        self.assertEqual(d, {
            'issue':'2026134','draw_date_bjt':'2026-11-22',
            'reds':[4,5,10,17,21,32],'blue':8,
            'pool_after_rmb':1154982023.0,'sales_rmb':435713586.0,
        })

    def test_real_sichuan_raw_current(self):
        d = _parse_sichuan_draw_html((EVID_ROOT/'sichuan_ssq_current.body').read_bytes())
        self.assertEqual(d, {
            'issue':'2026134','draw_date_bjt':'2026-11-22',
            'reds':[4,5,10,17,21,32],'blue':8,
            'pool_after_rmb':1154982023.0,'sales_rmb':435713586.0,
        })

    def test_real_shanghai_sichuan_2of2_quorum(self):
        sh=EVID_ROOT/'shanghai_ssq_previous.body'; sc=EVID_ROOT/'sichuan_ssq_current.body'
        out=verify_2_of_n_official_quorum([
            {'source_id':'SH_FLCP','url':'https://www.swlc.net.cn/lottery/ssq.html?limit=100&view=previous','path':str(sh),'sha256':hashlib.sha256(sh.read_bytes()).hexdigest(),'fetched_at_utc':'2026-11-24T11:19:38Z'},
            {'source_id':'SC_FLCP','url':'https://www.scflcp.com.cn/ssq','path':str(sc),'sha256':hashlib.sha256(sc.read_bytes()).hexdigest(),'fetched_at_utc':'2026-11-24T11:19:41Z'},
        ])
        self.assertEqual(set(out.agreed_source_ids), {'SH_FLCP','SC_FLCP'})
        self.assertEqual(out.latest_draw['issue'], '2026134')


    def test_only_real_validated_sources_enabled_for_production_quorum(self):
        from registry_and_parsers_v4 import OFFICIAL_SOURCE_REGISTRY
        self.assertTrue(OFFICIAL_SOURCE_REGISTRY['SH_FLCP'].production_enabled)
        self.assertTrue(OFFICIAL_SOURCE_REGISTRY['SC_FLCP'].production_enabled)
        self.assertFalse(OFFICIAL_SOURCE_REGISTRY['CWL_CENTRAL'].production_enabled)
        self.assertFalse(OFFICIAL_SOURCE_REGISTRY['GD_FLCP'].production_enabled)

    def test_real_shanghai_schedule_parser(self):
        sh=EVID_ROOT/'shanghai_ssq_previous.body'
        ev=load_schedule_manifest_evidence({
            'source_id':'SH_FLCP','url':'https://www.swlc.net.cn/lottery/ssq.html?limit=100&view=previous','path':str(sh),'sha256':hashlib.sha256(sh.read_bytes()).hexdigest(),'fetched_at_utc':'2026-11-24T11:19:38Z','parser_version':'sh_sched_html_or_manifest_v2',
        })
        self.assertEqual(ev.valid_draw_weekdays, frozenset({1,3,6}))
        self.assertEqual(ev.draw_time_bjt, (21,15,0))


    def test_official_annual_suspension_notice_parser_shape(self):
        raw = (
            '<html><body><h1>关于2026年福利彩票市场休市安排的公告</h1>'
            '<p>一、休市时间。春节10天，休市时间为2026年2月14日0:00至2月23日24:00。'
            '国庆节4天，休市时间为2026年10月1日0:00至10月4日24:00。</p></body></html>'
        ).encode('utf-8')
        start,end,dates=_parse_suspension_raw(raw)
        self.assertEqual((start,end),('2026-01-01','2026-12-31'))
        self.assertEqual(len(dates),14)
        self.assertIn('2026-02-14',dates)
        self.assertIn('2026-02-23',dates)
        self.assertIn('2026-10-01',dates)
        self.assertIn('2026-10-04',dates)

    def test_missing_suspension_evidence_blocks_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=V4TestFixtureMixin()._build_env(root)
            p.pop('suspension_evidence')
            with self.assertRaises(GateBlockedError) as c:
                run_v4_production(p,root/'pub',AppendOnlyLedger(root/'ledger.jsonl',V3_HEAD),now_utc=datetime(2027,1,3,11,30,tzinfo=timezone.utc))
            self.assertEqual(c.exception.code,'SUSPENSION_EVIDENCE_REQUIRED')

    def test_wrong_year_suspension_coverage_blocks_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=V4TestFixtureMixin()._build_env(root)
            sp=Path(p['suspension_evidence']['path'])
            raw=json.dumps({'coverage_year':2026,'suspended_dates_bjt':['2026-02-14']},sort_keys=True).encode('utf-8')
            sp.write_bytes(raw)
            p['suspension_evidence']['sha256']=hashlib.sha256(raw).hexdigest()
            with self.assertRaises(GateBlockedError) as c:
                run_v4_production(p,root/'pub',AppendOnlyLedger(root/'ledger.jsonl',V3_HEAD),now_utc=datetime(2027,1,3,11,30,tzinfo=timezone.utc))
            self.assertEqual(c.exception.code,'SUSPENSION_COVERAGE_MISSING')

    def test_ledger_state_inherited_and_caller_conflict_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=V4TestFixtureMixin()._build_env(root)
            ledger=AppendOnlyLedger(root/'ledger.jsonl',V3_HEAD)
            ledger.commit_record(
                target_issue='2026152', commitment_sha256='a'*64,
                stage_manifest_sha256='b'*64,
                meta={'special_rule_active':True,'rule_regime':'SPECIAL_RULE_2026_ACTIVE'},
            )
            p.pop('special_rule_active_previous',None)
            res=run_v4_production(p,root/'pub',ledger,now_utc=datetime(2027,1,3,11,30,tzinfo=timezone.utc))
            self.assertEqual(res['receipt']['rule_state_source'],'PREVIOUS_ISSUE_LEDGER')
            self.assertTrue(res['receipt']['draw_ev_context']['special_rule_active_previous'])

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=V4TestFixtureMixin()._build_env(root)
            ledger=AppendOnlyLedger(root/'ledger.jsonl',V3_HEAD)
            ledger.commit_record(
                target_issue='2026152', commitment_sha256='a'*64,
                stage_manifest_sha256='b'*64,
                meta={'special_rule_active':True,'rule_regime':'SPECIAL_RULE_2026_ACTIVE'},
            )
            p['special_rule_active_previous']=False
            with self.assertRaises(GateBlockedError) as c:
                run_v4_production(p,root/'pub',ledger,now_utc=datetime(2027,1,3,11,30,tzinfo=timezone.utc))
            self.assertEqual(c.exception.code,'CALLER_RULE_STATE_CONFLICT')

if __name__=='__main__':
    unittest.main()

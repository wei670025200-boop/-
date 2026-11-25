import unittest
from pathlib import Path
from dlt_v5.local_schedule import LocalSchedule
from dlt_v5.history_validation import validate_history
from dlt_v5.history_contract import HISTORY_FIELDS
ROOT=Path(__file__).resolve().parents[1]
S=LocalSchedule.load(ROOT/'data/dlt_local_schedule_manifest.json')
HEADER=','.join(HISTORY_FIELDS)+'\n'
SCHEDULE=[r for y in range(2021,2027) for r in S.year(y)]
def fixture(n=780):
    records=[]
    for issue,day in SCHEDULE[-n:]:
        epoch='7TIER_2026' if day>='2026-02-02' else '9TIER_2019'
        records.append(','.join([issue,day,'1','2','3','4','5','1','2','1000','100','1','1','2','1','3','4','5','6','7',epoch,'0']))
    return (HEADER+'\n'.join(records)+'\n').encode()
class HistoryGate(unittest.TestCase):
    def test_schedule_cutoff(self): self.assertEqual(S.check('26014','2026-02-02'),('26014','2026-02-02'))
    def test_holiday(self): self.assertEqual(S.next('26018','2026-02-11'),('26019','2026-02-25'))
    def test_year_transition(self): self.assertEqual(S.next(*S.year(2025)[-1]),S.year(2026)[0])
    def test_unknown_year(self):
        with self.assertRaisesRegex(ValueError,'SCHEDULE_YEAR_UNREGISTERED'): S.next(*S.year(2026)[-1])
    def test_wrong_day(self):
        with self.assertRaisesRegex(ValueError,'ISSUE_DATE_SCHEDULE_CONFLICT'): S.check('26014','2026-02-03')
    def test_780(self): self.assertEqual(validate_history(fixture(),S)[1]['buffer_780'],'PASS')
    def test_650(self): self.assertEqual(validate_history(fixture(650),S)[1]['buffer_780'],'BELOW_TARGET')
    def test_649(self):
        with self.assertRaisesRegex(ValueError,'DATA_GATE_INSUFFICIENT_650'): validate_history(fixture(649),S)
    def test_gap(self):
        lines=fixture().splitlines(keepends=True); del lines[10]
        with self.assertRaisesRegex(ValueError,'HISTORY_NOT_CONTIGUOUS'): validate_history(b''.join(lines),S)
    def test_duplicate(self):
        lines=fixture().splitlines(keepends=True); lines.insert(10,lines[9])
        with self.assertRaisesRegex(ValueError,'HISTORY_NOT_CONTIGUOUS'): validate_history(b''.join(lines),S)
    def test_all_add_zero(self):
        lines=[line.split(b',') for line in fixture().splitlines(keepends=True)];
        for line in lines[1:]: line[12]=b'0'
        with self.assertRaisesRegex(ValueError,'ADDITIONAL_NONZERO_AUDIT_FAILED'): validate_history(b''.join(b','.join(l) for l in lines),S)
    def test_g1_add_audit(self): self.assertEqual(validate_history(fixture(),S)[1]['g1_add_nonzero_rows'],780)
    def test_bad_sales(self):
        lines=fixture().splitlines(keepends=True); row=lines[1].split(b',');row[9]=b'0';lines[1]=b','.join(row)
        with self.assertRaisesRegex(ValueError,'INVALID_RMB'): validate_history(b''.join(lines),S)
    def test_bad_balls(self):
        lines=fixture().splitlines(keepends=True); row=lines[1].split(b',');row[2]=b'2';lines[1]=b','.join(row)
        with self.assertRaisesRegex(ValueError,'INVALID_BALLS'): validate_history(b''.join(lines),S)
    def test_missing_add(self):
        lines=fixture().splitlines(keepends=True); row=lines[1].split(b',');row[12]=b'';lines[1]=b','.join(row)
        with self.assertRaisesRegex(ValueError,'INCOMPLETE_HISTORY_ROW'): validate_history(b''.join(lines),S)
    def test_source_boundary(self): self.assertEqual(validate_history(fixture(),S)[1]['source_authenticity'],'NOT_ESTABLISHED_BY_SCHEMA_VALIDATION')

import tempfile
import unittest
from pathlib import Path

from scripts.ssq_ppas12_evidence import (
    audit_overlaps,
    parse_hebei_details,
    parse_jiangsu_list_pages,
    parse_prize_rows,
    reconcile,
)


BASE_ROWS = """
<table>
<tr><td>一等奖</td><td>7</td><td>7721874</td><td>中6红+蓝</td></tr>
<tr><td>二等奖</td><td>205</td><td>116177</td><td>中6红</td></tr>
<tr><td>三等奖</td><td>1769</td><td>3000</td><td>中5红+蓝</td></tr>
<tr><td>四等奖</td><td>98135</td><td>200</td><td>中5红</td></tr>
<tr><td>五等奖</td><td>1755290</td><td>10</td><td>中4红</td></tr>
<tr><td>六等奖</td><td>10704926</td><td>5</td><td>中蓝</td></tr>
</table>
"""


class PPAS12EvidenceTests(unittest.TestCase):
    def test_prize_parser_reads_sixth_and_special_rows(self):
        rows = parse_prize_rows(BASE_ROWS + "<table><tr><td>六等奖翻番奖</td><td>9000000</td><td>5</td></tr></table>")
        sixth = next(row for row in rows if row["label"] == "六等奖")
        bonus = next(row for row in rows if row["label"] == "六等奖翻番奖")
        self.assertEqual(sixth["national_count"], 10704926)
        self.assertEqual(bonus["national_count"], 9000000)

    def test_jiangsu_embedded_official_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            escaped = ("<html><body>第2021121期 本期全国投注总额39036万元" + BASE_ROWS + "</body></html>")
            import html
            page = (
                '<a target="_blank" href="' + html.escape(escaped, quote=True) + '">中国福利彩票"双色球"第2021121期开奖公告</a>'
                '<span class="articleDate" >2021-10-24</span>'
            )
            (root / "page_1.html").write_text(page, encoding="utf-8")
            records = parse_jiangsu_list_pages(root)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["sixth_prize_count"], 10704926)
            self.assertEqual(records[0]["sales_rmb"], 390360000)
            self.assertEqual(records[0]["sales_rmb_resolution"], 10000)

    def test_hebei_and_jiangsu_overlap_reconciles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            detail = root / "details"
            detail.mkdir()
            (root / "index.json").write_text(
                '{"data":{"rows":[{"issueNo":"2023048","link":"/kjgg/ssq/x.html"}]}}', encoding="utf-8"
            )
            hebei_table = BASE_ROWS.replace(
                "<tr><td>六等奖</td><td>10704926</td><td>5</td><td>中蓝</td></tr>",
                "<tr><td>六等奖</td><td>中蓝</td><td>10704926注</td><td>400000注</td><td>5元</td></tr>",
            )
            (detail / "2023048.html").write_text(
                "<h1>第2023048期开奖公告</h1><p>开奖日期：2023-05-04</p>" + hebei_table,
                encoding="utf-8",
            )
            records = parse_hebei_details(root / "index.json", detail)
            clone = dict(records[0])
            clone["source_id"] = "JIANGSU_FC_OFFICIAL_EMBEDDED_ANNOUNCEMENT"
            canonical, report = reconcile([records[0], clone])
            self.assertEqual(canonical[0]["sixth_prize_count"], 10704926)
            self.assertEqual(report["duplicate_issue_count"], 1)
            self.assertEqual(report["conflict_issue_count"], 0)

    def test_sales_display_precision_is_not_a_source_conflict(self):
        base = {
            "issue": "2023048",
            "date": "2023-05-04",
            "sales_rmb": 362109840,
            "sales_rmb_resolution": 1,
            "sixth_prize_count": 100,
            "sixth_prize_rmb": 5,
            "special_accounting_observation": "NO_SPECIAL_ACCOUNTING_ROW_OBSERVED_IN_COMPLETE_PRIZE_TABLE",
            "special_rows": [],
            "source_id": "HEBEI_FC_OFFICIAL_DETAIL",
            "source_url": "https://example.invalid/exact",
            "raw_sha256": "a" * 64,
        }
        rounded = dict(
            base,
            sales_rmb=362110000,
            sales_rmb_resolution=10000,
            source_id="JIANGSU_FC_OFFICIAL_EMBEDDED_ANNOUNCEMENT",
            source_url="https://example.invalid/rounded",
            raw_sha256="b" * 64,
        )
        canonical, report = reconcile([base, rounded])
        self.assertEqual(report["conflict_issue_count"], 0)
        self.assertEqual(canonical[0]["sales_rmb"], 362109840)
        self.assertEqual(canonical[0]["sales_rmb_resolution"], 1)

    def test_audit_blocks_when_fewer_than_sixty_overlap_issues(self):
        record = {
            "issue": "2023048",
            "sixth_prize_count": 100,
            "sixth_prize_rmb": 5,
            "source_id": "A",
        }
        other = dict(record, source_id="B")
        audit = audit_overlaps([record, other], [])
        self.assertEqual(audit["actual_sample_size"], 1)
        self.assertFalse(audit["fixed_manifest_scope_valid"])
        self.assertEqual(audit["status"], "BLOCKED_OR_FAILED")


if __name__ == "__main__":
    unittest.main()



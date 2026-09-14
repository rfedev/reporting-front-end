"""Unit tests for the FileScanner service."""

import tempfile
import unittest
from pathlib import Path
from reporting_app.core.file_scanner import FileScanner


class TestFileScanner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Setup mock report structure
        # Root contains a report folder
        self.report_dir = self.root / "rep_alpha"
        self.report_dir.mkdir()

        # Query in code subfolder
        code_dir = self.report_dir / "code"
        code_dir.mkdir()
        (code_dir / "query_01.sql").write_text(
            "SELECT * FROM `in_table` WHERE d = '{repDate}';", encoding="utf-8"
        )

        # Query in root folder
        (self.report_dir / "query_02.sql").write_text(
            "CREATE TABLE `out_table` AS SELECT 1;", encoding="utf-8"
        )

        # Process flow JSON
        (self.report_dir / "Flow-01.json").write_text(
            '{"flow_name": "Flow-01", "query_names": ["query_01"], "parameter_defaults": {"repDate": "2026-01-01"}}',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scan_reports(self):
        scanner = FileScanner([{"alias": "Primary", "path": str(self.root)}])
        reports = scanner.scan_all_reports()
        self.assertEqual(len(reports), 1)

        rep = reports[0]
        self.assertEqual(rep.name, "rep_alpha")
        self.assertEqual(rep.directory_alias, "Primary")
        self.assertEqual(len(rep.queries), 2)
        self.assertEqual(len(rep.process_flows), 1)

        q1 = rep.get_query("query_01")
        self.assertIsNotNone(q1)
        self.assertEqual(q1.parameter_names, ["repDate"])
        self.assertEqual(q1.input_tables, ["in_table"])

        flow = rep.get_process_flow("Flow-01")
        self.assertIsNotNone(flow)
        self.assertEqual(flow.query_names, ["query_01"])
        self.assertEqual(flow.parameter_defaults.get("repDate"), "2026-01-01")

    def test_scan_reports_with_queries_subfolder(self):
        # Create a report using the new standard ./reports/<report-name>/queries structure
        rep_beta = self.root / "rep_beta"
        queries_dir = rep_beta / "queries"
        queries_dir.mkdir(parents=True)

        (queries_dir / "beta_01.sql").write_text(
            "SELECT * FROM `dataset.beta` WHERE flag = {flag};", encoding="utf-8"
        )
        (queries_dir / "Flow-Beta.json").write_text(
            '{"flow_name": "Flow-Beta", "query_names": ["beta_01"], "parameter_defaults": {"flag": "1"}}',
            encoding="utf-8",
        )

        scanner = FileScanner([{"alias": "Primary", "path": str(self.root)}])
        reports = {r.name: r for r in scanner.scan_all_reports()}
        self.assertIn("rep_beta", reports)

        beta = reports["rep_beta"]
        self.assertEqual(len(beta.queries), 1)
        self.assertIsNotNone(beta.get_query("beta_01"))
        self.assertEqual(len(beta.process_flows), 1)
        self.assertIsNotNone(beta.get_process_flow("Flow-Beta"))

    def test_scan_multiple_directories(self):
        with tempfile.TemporaryDirectory() as second_dir:
            root2 = Path(second_dir)
            rep2 = root2 / "rep_gamma"
            (rep2 / "queries").mkdir(parents=True)
            (rep2 / "queries" / "gamma.sql").write_text("SELECT 1;", encoding="utf-8")

            scanner = FileScanner([
                {"alias": "Primary", "path": str(self.root)},
                {"alias": "Secondary", "path": str(root2)},
            ])
            reports = scanner.scan_all_reports()
            self.assertEqual(len(reports), 2)
            names_and_aliases = [(r.name, r.directory_alias) for r in reports]
            self.assertIn(("rep_alpha", "Primary"), names_and_aliases)
            self.assertIn(("rep_gamma", "Secondary"), names_and_aliases)


if __name__ == "__main__":
    unittest.main()


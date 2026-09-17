"""Unit tests for the application and process flow controllers."""

import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication

from reporting_app.controllers.app_controller import AppController
from reporting_app.controllers.flow_controller import ProcessFlowController
from reporting_app.core.models import ProcessFlowInfo, QueryInfo, QueryParameter, Report
from reporting_app.persistence.database import DatabaseManager


class TestControllers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "test.db"
        self.db_manager = DatabaseManager(self.db_path)

        # Setup mock report
        self.report_dir = self.root / "report_01"
        self.report_dir.mkdir()
        (self.report_dir / "q1.sql").write_text(
            "SELECT 1 FROM `t1` WHERE x = '{p1}';", encoding="utf-8"
        )
        (self.report_dir / "q2.sql").write_text(
            "SELECT 2 FROM `t2` WHERE x = '{p1}' AND y = '{p2}';", encoding="utf-8"
        )
        (self.report_dir / "flow1.json").write_text(
            '{"flow_name": "flow1", "query_names": ["q1"], "parameter_defaults": {}}',
            encoding="utf-8",
        )

        self.controller = AppController(db_manager=self.db_manager)
        self.controller.set_working_directories([{"alias": "Primary", "path": str(self.root)}])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_app_controller_navigation(self):
        self.assertIn("report_01", self.controller.reports_by_name)
        self.controller.select_report("report_01")
        self.assertEqual(self.controller.active_report.name, "report_01")

        # By default, the available process flow ("flow1") is selected (All Queries removed per Req 2)
        self.assertEqual(self.controller.active_flow_name, "flow1")

        # Queries in active report are visible
        queries = [q.name for q in self.controller.active_report.queries]
        self.assertEqual(set(queries), {"q1", "q2"})

    def test_default_process_flow_created_if_none(self):
        # Create empty report with no flows
        empty_rep_dir = self.root / "empty_report"
        empty_rep_dir.mkdir()
        self.controller.scan()
        self.controller.select_report("empty_report")
        self.assertEqual(self.controller.active_flow_name, "Process Flow 01")
        flow_names = [f.name for f in self.controller.active_report.process_flows]
        self.assertIn("Process Flow 01", flow_names)
        self.assertNotIn("All Queries", flow_names)

    def test_add_remove_reports_and_flows(self):
        # Add report
        new_rep = self.controller.add_report("new_report_xyz")
        self.assertIsNotNone(new_rep)
        self.assertEqual(self.controller.active_report.name, "new_report_xyz")
        self.assertIn("new_report_xyz", self.controller.reports_by_name)

        # Add process flow to report
        new_flow = self.controller.add_process_flow("My Flow")
        self.assertIsNotNone(new_flow)
        self.assertEqual(self.controller.active_flow_name, "My Flow")

        # Remove process flow
        success = self.controller.remove_process_flow("My Flow")
        self.assertTrue(success)

        # Remove report
        success = self.controller.remove_report("new_report_xyz")
        self.assertTrue(success)
        self.assertNotIn("new_report_xyz", self.controller.reports_by_name)

    def test_dropdown_persistence(self):
        self.controller.select_report("report_01")
        self.controller.select_process_flow("flow1")
        self.controller.select_query("q2")

        # Check repository has stored these
        self.assertEqual(self.controller.repo.get_selected_report(), "report_01")
        self.assertEqual(self.controller.repo.get_selected_flow("report_01"), "flow1")
        self.assertEqual(self.controller.repo.get_selected_query("report_01"), "q2")

    def test_multi_working_directories(self):
        with tempfile.TemporaryDirectory() as second_dir:
            root2 = Path(second_dir)
            rep_sec = root2 / "report_sec"
            (rep_sec / "queries").mkdir(parents=True)
            (rep_sec / "queries" / "sec_q.sql").write_text("SELECT 1;", encoding="utf-8")

            # Duplicate name in root2 to test alias disambiguation
            rep_dup = root2 / "report_01"
            (rep_dup / "queries").mkdir(parents=True)
            (rep_dup / "queries" / "dup_q.sql").write_text("SELECT 2;", encoding="utf-8")

            self.controller.set_working_directories([
                {"alias": "Primary", "path": str(self.root)},
                {"alias": "Secondary", "path": str(root2)},
            ])

            # Distinct name appears plain
            self.assertIn("report_sec", self.controller.reports_by_key)
            # Duplicate name appears with alias
            self.assertIn("report_01 [Primary]", self.controller.reports_by_key)
            self.assertIn("report_01 [Secondary]", self.controller.reports_by_key)

            # Test creating report in secondary directory
            new_rep = self.controller.add_report("created_in_sec", directory_alias="Secondary")
            self.assertIsNotNone(new_rep)
            self.assertTrue(new_rep.folder_path.is_relative_to(root2))

    def test_execution_log_persistence_and_formatting(self):
        repo = self.controller.repo
        self.assertIsNotNone(repo)

        # 1. Record a query execution log
        log_data = {
            "run_id": "run-abc-123",
            "flow_start_time": "2026-09-17T08:30:00.000000+00:00",
            "node_start_time": "2026-09-17T08:30:01.000000+00:00",
            "node_end_time": "2026-09-17T08:30:04.500000+00:00",
            "duration_seconds": 3.5,
            "report_name": "report_01",
            "flow_name": "flow1",
            "node_type": "query",
            "node_name": "q1",
            "status": "SUCCESS",
            "submitted_query": "SELECT 1 FROM `t1`;",
            "output_rows": 1250,
            "total_bytes_processed": 1024 * 1024 * 50,  # 50 MB
            "total_bytes_billed": 1024 * 1024 * 60,
            "slot_millis": 2400,
            "cache_hit": False,
            "export_details_json": [{"filename": "q1.csv", "row_count": 1250}],
        }
        repo.record_execution_log(log_data)

        # 2. Verify retrieval by date
        dates = repo.get_distinct_log_dates(report_name="report_01")
        self.assertIn("2026-09-17", dates)

        # 3. Verify retrieval of run sessions
        sessions = repo.get_run_sessions(date_str="2026-09-17", report_name="report_01")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["run_id"], "run-abc-123")
        self.assertEqual(sessions[0]["status"], "SUCCESS")
        self.assertEqual(len(sessions[0]["nodes"]), 1)

        # 4. Verify markdown formatting
        from reporting_app.presentation.flow_editor.schema_dialog import (
            format_single_node_log,
            format_run_session_logs,
            format_bytes,
            format_duration,
        )
        self.assertEqual(format_bytes(1024 * 1024 * 50), "50.00 MB")
        self.assertEqual(format_duration(3.5), "3.50s")

        node_entry = sessions[0]["nodes"][0]
        md_node = format_single_node_log(node_entry)
        self.assertIn("## ✅ Query: `q1`", md_node)
        self.assertIn("50.00 MB", md_node)
        self.assertIn("1,250 rows", md_node)
        self.assertIn("SELECT 1 FROM `t1`;", md_node)

        md_session = format_run_session_logs(sessions[0]["nodes"])
        self.assertIn("Summary Overview", md_session)
        self.assertIn("✅ Success", md_session)

        # 5. Verify LogViewerDialog instantiates without NameError
        from reporting_app.presentation.settings_dialog import LogViewerDialog
        dlg = LogViewerDialog(
            repo=repo,
            report_name="report_01",
            flow_name="flow1",
            filter_node="q1",
        )
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.node_combo.currentText(), "q1")
        self.assertGreater(dlg.run_tree.topLevelItemCount(), 0)
        dlg.close()

    def test_on_file_changed_signals(self):
        self.controller.select_report("report_01")
        emitted_reports = []
        self.controller.active_report_changed.connect(lambda r: emitted_reports.append(r))

        # Test changing a query file triggers active_report_changed
        q1_file = self.report_dir / "q1.sql"
        q1_file.write_text("SELECT 100 FROM `t1`;", encoding="utf-8")
        self.controller._on_file_changed(q1_file)

        self.assertEqual(len(emitted_reports), 1)
        self.assertEqual(emitted_reports[0].name, "report_01")


if __name__ == "__main__":
    unittest.main()

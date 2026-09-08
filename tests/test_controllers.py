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
        self.controller.set_working_directory(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_app_controller_navigation(self):
        self.assertIn("report_01", self.controller.reports_by_name)
        self.controller.select_report("report_01")
        self.assertEqual(self.controller.active_report.name, "report_01")

        # By default, "All Queries" is selected
        self.assertEqual(self.controller.active_flow_name, AppController.ALL_QUERIES_OPTION)

        # When "All Queries" is active, both q1 and q2 are visible
        queries = [q.name for q in self.controller.active_report.queries]
        self.assertEqual(set(queries), {"q1", "q2"})

        # When selecting "flow1", only q1 is shown (since flow1 only has q1)
        self.controller.select_process_flow("flow1")
        self.assertEqual(self.controller.active_flow_name, "flow1")

    def test_flow_controller_parameters(self):
        rep = self.controller.reports_by_name["report_01"]
        flow_ctrl = ProcessFlowController(report=rep, flow_name="flow1")

        # Test unique parameter consolidation across queries
        unique_params = flow_ctrl.get_unique_parameters(["q1", "q2"])
        # q1 has {p1}, q2 has {p1, p2}. Unique list should contain p1 and p2 exactly once
        self.assertEqual(unique_params, ["p1", "p2"])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the persistence layer."""

import tempfile
import unittest
from pathlib import Path
from reporting_app.persistence.database import DatabaseManager
from reporting_app.persistence.flow_storage import FlowStorage
from reporting_app.persistence.repository import Repository


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db_manager = DatabaseManager(self.db_path)
        self.repo = Repository(self.db_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_settings_crud(self):
        self.repo.set_setting("theme", "dark")
        self.assertEqual(self.repo.get_setting("theme"), "dark")
        self.assertEqual(self.repo.get_setting("nonexistent", "fallback"), "fallback")

        self.repo.set_auto_scan(False)
        self.assertFalse(self.repo.get_auto_scan())

    def test_parameter_defaults(self):
        self.repo.set_parameter_default("query", "policy-01", "repDate", "2026-09-08")
        self.repo.set_parameter_default("query", "policy-01", "status", "ACTIVE")

        val = self.repo.get_parameter_default("query", "policy-01", "repDate")
        self.assertEqual(val, "2026-09-08")

        defaults = self.repo.get_all_defaults_for_scope("query", "policy-01")
        self.assertEqual(defaults, {"repDate": "2026-09-08", "status": "ACTIVE"})

    def test_catalog_tables(self):
        self.repo.record_query_tables(
            report_name="l0006",
            query_name="q1",
            input_tables=["raw.policies"],
            output_tables=["stage.active_policies"],
        )
        all_tables = self.repo.get_all_tables()
        self.assertEqual(len(all_tables), 2)
        names = {t["table_name"] for t in all_tables}
        self.assertEqual(names, {"raw.policies", "stage.active_policies"})

    def test_flow_storage(self):
        flow_file = Path(self.temp_dir.name) / "Flow-Test.json"
        FlowStorage.save(
            file_path=flow_file,
            flow_name="Flow-Test",
            report_name="l0006",
            query_names=["q1", "q2"],
            parameter_defaults={"p1": "v1"},
            graph_session={"nodes": {}},
            show_full_table_names=True,
            csv_filenames={"q1": "q1_custom.csv"},
            csv_imports=[{"node_name": "Import csv", "items": [{"csv_path": "/path/a.csv", "has_headers": True, "output_table": "proj.d.t"}]}],
            view_state={"zoom": 0.85, "center": [120.0, 340.0]},
        )

        loaded = FlowStorage.load(flow_file)
        self.assertEqual(loaded["flow_name"], "Flow-Test")
        self.assertEqual(loaded["query_names"], ["q1", "q2"])
        self.assertEqual(loaded["parameter_defaults"], {"p1": "v1"})
        self.assertEqual(loaded["csv_filenames"], {"q1": "q1_custom.csv"})
        self.assertEqual(len(loaded["csv_imports"]), 1)
        self.assertEqual(loaded["view_state"], {"zoom": 0.85, "center": [120.0, 340.0]})

    def test_workbench_dataset(self):
        self.assertEqual(self.repo.get_workbench_dataset(), "")
        self.repo.set_workbench_dataset("iw-gid-prd-01-c683.gid_art_test")
        self.assertEqual(self.repo.get_workbench_dataset(), "iw-gid-prd-01-c683.gid_art_test")

    def test_dropdown_selection_persistence(self):
        self.assertEqual(self.repo.get_selected_report(), "")
        self.assertEqual(self.repo.get_selected_flow("report1"), "")
        self.assertEqual(self.repo.get_selected_query("report1"), "")

        self.repo.set_selected_report("report1")
        self.repo.set_selected_flow("Process Flow 01", "report1")
        self.repo.set_selected_query("query1", "report1")

        self.assertEqual(self.repo.get_selected_report(), "report1")
        self.assertEqual(self.repo.get_selected_flow("report1"), "Process Flow 01")
        self.assertEqual(self.repo.get_selected_query("report1"), "query1")

    def test_working_directories(self):
        # Default should return list with Default alias and cwd
        dirs = self.repo.get_working_directories()
        self.assertIsInstance(dirs, list)
        self.assertTrue(len(dirs) >= 1)
        self.assertEqual(dirs[0]["alias"], "Default")

        # Set multiple working directories
        new_dirs = [
            {"alias": "Main", "path": "/path/to/main"},
            {"alias": "Archive", "path": "/path/to/archive"},
        ]
        self.repo.set_working_directories(new_dirs)
        loaded_dirs = self.repo.get_working_directories()
        self.assertEqual(loaded_dirs, new_dirs)


if __name__ == "__main__":
    unittest.main()

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
        )

        loaded = FlowStorage.load(flow_file)
        self.assertEqual(loaded["flow_name"], "Flow-Test")
        self.assertEqual(loaded["query_names"], ["q1", "q2"])
        self.assertEqual(loaded["parameter_defaults"], {"p1": "v1"})


if __name__ == "__main__":
    unittest.main()

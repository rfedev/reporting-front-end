"""Unit tests for the bigquery_run execution module."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd

from reporting_app.core.bigquery_run import (
    is_csv_export_query,
    run_bigquery_script,
    substitute_parameters,
)


class TestBigQueryRun(unittest.TestCase):

    def test_substitute_parameters(self):
        sql = "SELECT * FROM `mytable` WHERE dt = '{repDate}' AND id = {id};"
        params = {"repDate": "2026-09-08", "id": "12345"}
        res = substitute_parameters(sql, params)
        self.assertEqual(
            res,
            "SELECT * FROM `mytable` WHERE dt = '2026-09-08' AND id = 12345;",
        )

    def test_is_csv_export_query_select(self):
        sql = """
        -- Comment at start
        /* Multi-line
           comment */
        SELECT customer_id, count(1)
        FROM `proj.dataset.table`
        GROUP BY 1;
        """
        self.assertTrue(is_csv_export_query(sql))

    def test_is_csv_export_query_create_table(self):
        sql = """
        CREATE TABLE `proj.dataset.table` AS
        SELECT 1 as x;
        """
        self.assertFalse(is_csv_export_query(sql))

    def test_is_csv_export_query_create_or_replace_table(self):
        sql = """
        -- Header comment
        CREATE OR REPLACE TABLE `proj.dataset.table` AS
        SELECT 1 as x;
        """
        self.assertFalse(is_csv_export_query(sql))

    def test_is_csv_export_query_insert_into(self):
        sql = """
        INSERT INTO `proj.dataset.table` (a, b)
        VALUES (1, 2);
        """
        self.assertFalse(is_csv_export_query(sql))

    def test_run_bigquery_script_select_exports_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            sql_file = temp_path / "test_query.sql"
            sql_file.write_text("SELECT id, name FROM `sample`;", encoding="utf-8")

            outputs_dir = temp_path / "outputs"

            mock_df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
            mock_results = MagicMock()
            mock_results.to_dataframe.return_value = mock_df

            mock_query_job = MagicMock()
            mock_query_job.result.return_value = mock_results

            mock_client = MagicMock()
            mock_client.query.return_value = mock_query_job

            with patch("reporting_app.core.bigquery_run.bigquery.Client", return_value=mock_client):
                result = run_bigquery_script(
                    sql_script_path=sql_file,
                    report_name="test_report",
                    outputs_dir=outputs_dir,
                    parameters={},
                )

            self.assertTrue(result["is_export"])
            self.assertEqual(result["row_count"], 2)
            expected_csv = outputs_dir / "test_query.csv"
            self.assertEqual(result["output_file"], str(expected_csv))
            self.assertTrue(expected_csv.exists())

            # Verify CSV content
            csv_content = expected_csv.read_text(encoding="utf-8")
            self.assertIn("Alice", csv_content)
            self.assertIn("Bob", csv_content)

    def test_run_bigquery_script_create_table_no_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            sql_file = temp_path / "create_table.sql"
            sql_file.write_text("CREATE TABLE `sample` AS SELECT 1;", encoding="utf-8")

            outputs_dir = temp_path / "outputs"

            mock_query_job = MagicMock()
            mock_client = MagicMock()
            mock_client.query.return_value = mock_query_job

            with patch("reporting_app.core.bigquery_run.bigquery.Client", return_value=mock_client):
                result = run_bigquery_script(
                    sql_script_path=sql_file,
                    report_name="test_report",
                    outputs_dir=outputs_dir,
                    parameters={},
                )

            self.assertFalse(result["is_export"])
            self.assertIsNone(result["output_file"])
            # Ensure no CSV was created
            self.assertFalse((outputs_dir / "create_table.csv").exists())

    def test_run_bigquery_script_auto_extracts_project_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            sql_file = temp_path / "from_query.sql"
            sql_file.write_text(
                "SELECT * FROM `my-custom-project.dataset.source_table`;",
                encoding="utf-8",
            )
            outputs_dir = temp_path / "outputs"

            mock_df = pd.DataFrame({"col": [10, 20]})
            mock_results = MagicMock()
            mock_results.to_dataframe.return_value = mock_df

            mock_query_job = MagicMock()
            mock_query_job.result.return_value = mock_results

            mock_client = MagicMock()
            mock_client.query.return_value = mock_query_job

            with patch("reporting_app.core.bigquery_run.bigquery.Client", return_value=mock_client) as mock_client_cls:
                res = run_bigquery_script(
                    sql_script_path=sql_file,
                    report_name="test_report",
                    outputs_dir=outputs_dir,
                )

            # Client should be initialized with project="my-custom-project"
            mock_client_cls.assert_called_with(project="my-custom-project")
            self.assertEqual(res["project_id"], "my-custom-project")
            mock_client.query.assert_called_once()
            _, kwargs = mock_client.query.call_args
            self.assertEqual(kwargs.get("project"), "my-custom-project")

    def test_run_bigquery_script_combined_create_and_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            sql_file = temp_path / "combined.sql"
            sql_file.write_text(
                """
                CREATE OR REPLACE TABLE `my-proj.dataset.tbl` AS SELECT 1;
                SELECT * FROM `my-proj.dataset.tbl`;
                """,
                encoding="utf-8",
            )
            outputs_dir = temp_path / "outputs"

            mock_df = pd.DataFrame({"x": [1]})
            mock_results = MagicMock()
            mock_results.to_dataframe.return_value = mock_df

            mock_query_job = MagicMock()
            mock_query_job.result.return_value = mock_results

            mock_client = MagicMock()
            mock_client.query.return_value = mock_query_job

            with patch("reporting_app.core.bigquery_run.bigquery.Client", return_value=mock_client):
                res = run_bigquery_script(
                    sql_script_path=sql_file,
                    report_name="test_report",
                    outputs_dir=outputs_dir,
                )

            self.assertTrue(res["is_export"])
            self.assertTrue(res["has_output_tables"])
            self.assertEqual(res["output_tables"], ["my-proj.dataset.tbl"])
            self.assertEqual(res["row_count"], 1)

    def test_run_bigquery_import_csv(self):
        from reporting_app.core.bigquery_run import run_bigquery_import_csv

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            csv_file = temp_path / "data.csv"
            csv_file.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")

            mock_client = MagicMock()
            mock_job = MagicMock()
            mock_client.load_table_from_file.return_value = mock_job
            mock_table = MagicMock()
            mock_table.num_rows = 2
            mock_client.get_table.return_value = mock_table

            with patch("reporting_app.core.bigquery_run.bigquery.Client", return_value=mock_client) as mock_cls:
                res = run_bigquery_import_csv(
                    csv_path=csv_file,
                    destination_table="my-proj.dataset.imported_table",
                    has_headers=True,
                )

            mock_cls.assert_called_with(project="my-proj")
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["row_count"], 2)
            self.assertEqual(res["destination_table"], "my-proj.dataset.imported_table")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for BigQuery SQL parsing utilities."""

import unittest
from reporting_app.core.sql_parser import (
    clean_table_name,
    scan_query_parameters,
    scan_query_tables,
    strip_comments,
)


class TestSqlParser(unittest.TestCase):

    def test_strip_comments(self):
        sql = """
        -- This is a single line comment
        SELECT 1; /* This is a
        multi line comment */
        SELECT 2;
        """
        cleaned = strip_comments(sql)
        self.assertNotIn("single line comment", cleaned)
        self.assertNotIn("multi line comment", cleaned)
        self.assertIn("SELECT 1;", cleaned)
        self.assertIn("SELECT 2;", cleaned)

    def test_clean_table_name(self):
        self.assertEqual(clean_table_name("`proj.dataset.table`"), "proj.dataset.table")
        self.assertEqual(clean_table_name("dataset.table"), "dataset.table")
        self.assertEqual(clean_table_name("  `tbl`  "), "tbl")

    def test_scan_query_parameters(self):
        sql = """
        SELECT * FROM `orders`
        WHERE order_date >= '{startDate}'
          AND order_date <= '{endDate}'
          AND status = '{orderStatus}'
          -- AND ignored = '{inComment}'
          AND retry_count >= {retryMin}
          AND region = '{orderStatus}'; -- duplicate should be deduplicated
        """
        params = scan_query_parameters(sql)
        self.assertEqual(params, ["startDate", "endDate", "orderStatus", "retryMin"])

    def test_scan_query_tables(self):
        sql = """
        CREATE OR REPLACE TABLE `analytics.daily_summary` AS
        SELECT
            t1.id,
            t2.val
        FROM `raw.table1` AS t1
        JOIN `raw.table2` AS t2 ON t1.id = t2.id
        LEFT JOIN `raw.table3` AS t3 ON t1.id = t3.id;
        """
        input_tables, output_tables, csv_tables = scan_query_tables(sql)
        self.assertEqual(output_tables, ["analytics.daily_summary"])
        self.assertEqual(input_tables, ["raw.table1", "raw.table2", "raw.table3"])
        self.assertEqual(csv_tables, [])

    def test_insert_into_tables(self):
        sql = """
        INSERT INTO `analytics.events_log`
        SELECT * FROM `stage.incoming_events`;
        """
        input_tables, output_tables, csv_tables = scan_query_tables(sql)
        self.assertEqual(output_tables, ["analytics.events_log"])
        self.assertEqual(input_tables, ["stage.incoming_events"])
        self.assertEqual(csv_tables, [])

    def test_scan_query_csv_output_single_and_multiple_select(self):
        # Single SELECT statement query
        sql_single = """
        -- Final policy counts output
        SELECT out.*
        FROM `analytics_reporting.final_policy_counts_report` out;
        """
        input_tables, output_tables, csv_tables = scan_query_tables(sql_single)
        self.assertEqual(output_tables, [])
        self.assertEqual(csv_tables, ["analytics_reporting.final_policy_counts_report"])

        # Multiple SELECT statements in a single query
        sql_multi = """
        SELECT a, b FROM `project.dataset.report_summary`;
        SELECT c, d FROM `project.dataset.detail_records` WHERE active = true;
        """
        input_tables, output_tables, csv_tables = scan_query_tables(sql_multi)
        self.assertEqual(output_tables, [])
        self.assertEqual(
            csv_tables,
            ["project.dataset.report_summary", "project.dataset.detail_records"],
        )

    def test_extract_project_id_from_sql(self):
        from reporting_app.core.sql_parser import extract_project_id_from_sql

        sql1 = """
        SELECT * FROM `my-gcp-project.my_dataset.my_table` WHERE id = 1;
        """
        self.assertEqual(extract_project_id_from_sql(sql1), "my-gcp-project")

        sql2 = """
        CREATE OR REPLACE TABLE `iw-gid-prd-01-c683.dest_dataset.tbl` AS
        SELECT col1, col2
        FROM `iw-gid-prd-01-c683.src_dataset.source_tbl`
        JOIN `other-proj.dataset.tbl2` ON 1=1;
        """
        self.assertEqual(extract_project_id_from_sql(sql2), "iw-gid-prd-01-c683")

        # Table without 3-part project qualification
        sql3 = "SELECT * FROM `dataset.tbl`;"
        self.assertIsNone(extract_project_id_from_sql(sql3))

    def test_cte_and_create_with_select_csv(self):
        # Verify CTEs are not treated as external inputs
        # And verify CREATE TABLE + SELECT results in both output_tables and csv_tables (Requirement 6 & 12)
        sql = """
        WITH my_cte AS (
            SELECT id FROM `iw-gid-prd-01-c683.raw_dataset.users`
        )
        CREATE OR REPLACE TABLE `iw-gid-prd-01-c683.mart_dataset.active_users` AS
        SELECT u.id
        FROM my_cte AS u;

        SELECT * FROM `iw-gid-prd-01-c683.mart_dataset.active_users`;
        """
        input_tables, output_tables, csv_tables = scan_query_tables(sql)
        self.assertEqual(output_tables, ["iw-gid-prd-01-c683.mart_dataset.active_users"])
        self.assertEqual(csv_tables, ["iw-gid-prd-01-c683.mart_dataset.active_users"])
        # my_cte must NOT be in input_tables; only the real source table
        self.assertEqual(input_tables, ["iw-gid-prd-01-c683.raw_dataset.users"])


if __name__ == "__main__":
    unittest.main()

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


    def test_commented_create_table_with_output_comments(self):
        sql = """-- Query: test
#comment
# output: sel1.csv
SELECT '{repdate}' as reporting_date, 1 as num;

# create or replace table `link-to-cloud.test_dataset.tablename` as (select * from `link-to-cloud.test_dataset.test-import`);

# output: sel2.csv
select * from `link-to-cloud.test_dataset.test-import-202608`;

# output: sel3.csv
select * from `link-to-cloud.test_dataset.test-import`;
"""
        from reporting_app.core.sql_parser import find_standalone_select_statements, scan_select_output_tables
        stmts = find_standalone_select_statements(sql)
        self.assertEqual(len(stmts), 3)
        self.assertEqual(stmts[0]["csv_filename"], "sel1.csv")
        self.assertEqual(stmts[1]["csv_filename"], "sel2.csv")
        self.assertEqual(stmts[2]["csv_filename"], "sel3.csv")

        csv_tables = scan_select_output_tables(sql)
        self.assertEqual(csv_tables, ["sel1.csv", "sel2.csv", "sel3.csv"])

    def test_sqlglot_standalone_select_detection(self):
        """sqlglot identifies standalone SELECT and WITH...SELECT while ignoring CREATE/INSERT."""
        from reporting_app.core.sql_parser import find_standalone_select_statements

        sql = """
        -- Statement 1: CREATE TABLE
        CREATE OR REPLACE TABLE `prj.ds.t1` AS
        SELECT * FROM `prj.ds.raw`;

        -- Statement 2: INSERT INTO
        INSERT INTO `prj.ds.t2`
        SELECT * FROM `prj.ds.raw2`;

        -- Statement 3: WITH ... SELECT (should be standalone SELECT)
        -- Query description here
        WITH my_cte AS (
            SELECT id FROM `prj.ds.users`
        )
        SELECT * FROM my_cte;

        -- Statement 4: Normal SELECT
        SELECT count(*) FROM `prj.ds.orders`;
        """
        stmts = find_standalone_select_statements(sql)
        self.assertEqual(len(stmts), 2)
        # Verify first line indices point to the start of the statements
        lines = sql.splitlines(keepends=True)
        self.assertIn("-- Statement 3", lines[stmts[0]["first_line_idx"]])
        self.assertIn("-- Statement 4", lines[stmts[1]["first_line_idx"]])

    def test_sync_query_csv_comments_places_at_first_line(self):
        """sync_query_csv_comments places '# output: table_01.csv' at the very top of the statement."""
        import tempfile
        from pathlib import Path
        from reporting_app.core.sql_parser import sync_query_csv_comments

        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("-- Header comment\n-- More details\nSELECT 1;\n")
            temp_path = Path(f.name)

        try:
            csvs = sync_query_csv_comments(temp_path)
            self.assertEqual(csvs, ["table_01.csv"])
            content = temp_path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# output: table_01.csv\n-- Header comment"))
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_comment_anywhere_in_statement_detected(self):
        """A comment containing .csv anywhere in the statement is recognized."""
        from reporting_app.core.sql_parser import find_standalone_select_statements

        sql = """
        SELECT col1, col2
        /* output: block_output.csv */
        FROM `prj.ds.tbl`;

        SELECT colA
        FROM `prj.ds.tbl2`
        WHERE x = 1; -- inline_output.csv
        """
        stmts = find_standalone_select_statements(sql)
        self.assertEqual(len(stmts), 2)
        self.assertEqual(stmts[0]["csv_filename"], "block_output.csv")
        self.assertEqual(stmts[1]["csv_filename"], "inline_output.csv")


if __name__ == "__main__":
    unittest.main()


"""BigQuery query execution module.

Features:
- Parameter substitution for {param_name} placeholders
- Automatic query classification:
    - CREATE TABLE / INSERT INTO -> executes on BigQuery, no CSV export
    - Pure SELECT -> executes on BigQuery and exports results to ./reports/<report-name>/outputs/<query_name>.csv
- Uses Application Default Credentials (ADC) configured via 'gcloud auth application-default login'
"""

import csv
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import bigquery
import pandas as pd

logger = logging.getLogger(__name__)

# Regex to detect statements that modify/create tables instead of returning rows
_MODIFY_TABLE_RE = re.compile(
    r"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+|TEMPORARY\s+)?TABLE|INSERT\s+INTO|MERGE\s+INTO)\b",
    re.IGNORECASE,
)


def substitute_parameters(sql: str, parameters: Dict[str, str]) -> str:
    """Replace all {param_name} placeholders with their supplied values using string.format()."""
    if not parameters:
        return sql
    # string.format requires all keys present or defaultdict-like behavior
    # Use format_map with a fallback mapping so any non-matching bracket syntax is preserved safely
    class SafeFormatDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return sql.format_map(SafeFormatDict(parameters))


def is_csv_export_query(sql: str) -> bool:
    """Determine whether the query should export results to a CSV file.

    Returns:
        True if the query has at least one standalone SELECT statement (not saving into a table).
        False otherwise.
    """
    from reporting_app.core.sql_parser import scan_select_output_tables

    csv_tables = scan_select_output_tables(sql)
    return len(csv_tables) > 0


def run_bigquery_script(
    sql_script_path: str | Path,
    report_name: str,
    outputs_dir: str | Path,
    parameters: Optional[Dict[str, str]] = None,
    project_id: Optional[str] = None,
    stream_to_csv: bool = False,
    client: Optional[Any] = None,
    output_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a single query script and conditionally export results to CSV.

    Parameters:
        sql_script_path: Path to the .sql file.
        report_name: Name of the report.
        outputs_dir: Path to ./reports/<report-name>/outputs/.
        parameters: Key-value parameter dictionary.
        project_id: Optional GCP project ID override (defaults to first FROM statement table address or ADC).
        stream_to_csv: Stream large result sets directly to CSV.
        client: Optional pre-configured bigquery.Client.
        output_filename: Optional custom CSV filename (e.g. 'custom_name.csv').

    Returns:
        Dictionary with execution details: query_name, is_export, output_file, row_count, status.
    """
    from reporting_app.core.sql_parser import extract_project_id_from_sql, scan_query_tables

    sql_path = Path(sql_script_path)
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    raw_sql = sql_path.read_text(encoding="utf-8", errors="replace")
    substituted_sql = substitute_parameters(raw_sql, parameters or {})

    query_name = sql_path.stem
    input_tables, output_tables, output_csv_tables = scan_query_tables(substituted_sql)
    should_export = len(output_csv_tables) > 0 or is_csv_export_query(substituted_sql)
    has_output_tables = len(output_tables) > 0

    # Requirement 5: automatically pass and use the bigquery projectid based off
    # the first 'from' statement table address of the query being run
    if not project_id:
        project_id = extract_project_id_from_sql(substituted_sql)

    # Fallback to workbench dataset project if configured
    if not project_id:
        try:
            from reporting_app.persistence.repository import SQLiteRepository
            repo = SQLiteRepository()
            wb = repo.get_workbench_dataset()
            if wb and "." in wb:
                project_id = wb.split(".")[0].strip()
        except Exception:
            pass

    out_dir = Path(outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if output_filename:
        csv_name = output_filename if output_filename.endswith(".csv") else f"{output_filename}.csv"
        output_file = out_dir / csv_name
    else:
        output_file = out_dir / f"{query_name}.csv"

    # Initialize BigQuery client using ADC with detected or explicit project_id
    if client is None:
        client = bigquery.Client(project=project_id) if project_id else bigquery.Client()

    logger.info(
        f"Submitting BigQuery job for {query_name} (Project: {project_id}, "
        f"Export to CSV: {should_export}, Create Tables: {has_output_tables})"
    )
    query_job = client.query(substituted_sql, project=project_id)
    results = query_job.result()

    row_count = 0
    exported_path: Optional[str] = None

    if should_export:
        export_results = results
        # If running a multi-statement script job where a previous statement was the SELECT
        if getattr(export_results, "total_rows", None) is None:
            try:
                for child_job in client.list_jobs(parent_job=query_job):
                    if hasattr(child_job, "destination") and child_job.destination:
                        child_res = child_job.result()
                        if getattr(child_res, "total_rows", 0) and child_res.total_rows > 0:
                            export_results = child_res
                            break
            except Exception as e:
                logger.debug(f"Checking multi-query script child jobs: {e}")

        if stream_to_csv:
            with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                header_written = False
                for page in export_results.pages:
                    for row in page:
                        if not header_written:
                            writer.writerow(row.keys())
                            header_written = True
                        writer.writerow(list(row.values()))
                        row_count += 1
        else:
            try:
                df = export_results.to_dataframe(create_bqstorage_client=False)
                df.to_csv(output_file, index=False)
                row_count = len(df)
            except Exception as df_err:
                logger.debug(f"Could not convert to dataframe, falling back to manual CSV writing: {df_err}")
                with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.writer(csvfile)
                    header_written = False
                    for page in export_results.pages:
                        for row in page:
                            if not header_written:
                                writer.writerow(row.keys())
                                header_written = True
                            writer.writerow(list(row.values()))
                            row_count += 1

        exported_path = str(output_file)
        logger.info(f"Exported {row_count} rows to {exported_path}")
    else:
        logger.info(f"Query {query_name} completed table creation/update in BigQuery.")

    return {
        "query_name": query_name,
        "is_export": should_export,
        "has_output_tables": has_output_tables,
        "output_tables": output_tables,
        "output_file": exported_path,
        "row_count": row_count if should_export else None,
        "status": "SUCCESS",
        "project_id": project_id,
    }


def run_bigquery_import_csv(
    csv_path: str | Path,
    destination_table: str,
    has_headers: bool = True,
    project_id: Optional[str] = None,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Import a local CSV file into BigQuery with schema auto-detection.

    Parameters:
        csv_path: Path to the local CSV file.
        destination_table: BigQuery table reference (e.g. 'project-id.dataset_id.table_id').
        has_headers: Whether the CSV file has header row for column names.
        project_id: Optional GCP project ID override (extracted from table name if not given).
        client: Optional pre-configured bigquery.Client.

    Returns:
        Dictionary with status, destination_table, row_count, project_id.
    """
    from reporting_app.core.sql_parser import clean_table_name

    csv_p = Path(csv_path)
    if not csv_p.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_p}")

    clean_dest = clean_table_name(destination_table)
    if not project_id:
        parts = clean_dest.split(".")
        if len(parts) >= 3:
            project_id = parts[0]

    if client is None:
        client = bigquery.Client(project=project_id) if project_id else bigquery.Client()

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        autodetect=True,
        skip_leading_rows=1 if has_headers else 0,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    logger.info(f"Loading CSV {csv_p} into BigQuery table {clean_dest} (Headers: {has_headers})")
    with open(csv_p, "rb") as source_file:
        job = client.load_table_from_file(source_file, clean_dest, job_config=job_config, project=project_id)
        job.result()

    row_count = None
    try:
        table = client.get_table(clean_dest)
        row_count = table.num_rows
    except Exception as e:
        logger.debug(f"Could not fetch table num_rows: {e}")

    return {
        "status": "SUCCESS",
        "destination_table": clean_dest,
        "row_count": row_count,
        "project_id": project_id,
    }
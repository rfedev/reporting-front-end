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
    """Replace all {param_name} placeholders with their supplied values."""
    if not parameters:
        return sql

    def replacer(match):
        param_key = match.group(1).strip()
        if param_key in parameters:
            return str(parameters[param_key])
        return match.group(0)

    return re.sub(r"\{([a-zA-Z0-9_-]+)\}", replacer, sql)


def is_csv_export_query(sql: str) -> bool:
    """Determine whether the query should export its results to a CSV file.

    Returns:
        False if the query creates or modifies a table (e.g. CREATE TABLE, INSERT INTO).
        True if the query is a SELECT statement whose result set should be saved to CSV.
    """
    from reporting_app.core.sql_parser import strip_comments

    cleaned = strip_comments(sql).strip()
    # If it contains a CREATE TABLE or INSERT INTO statement, it saves to BigQuery table directly
    if _MODIFY_TABLE_RE.search(cleaned):
        return False
    return True


def run_bigquery_script(
    sql_script_path: str | Path,
    report_name: str,
    outputs_dir: str | Path,
    parameters: Optional[Dict[str, str]] = None,
    project_id: Optional[str] = None,
    stream_to_csv: bool = False,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute a single query script and conditionally export results to CSV.

    Parameters:
        sql_script_path: Path to the .sql file.
        report_name: Name of the report.
        outputs_dir: Path to ./reports/<report-name>/outputs/.
        parameters: Key-value parameter dictionary.
        project_id: Optional GCP project ID override (defaults to environment ADC project).
        stream_to_csv: Stream large result sets directly to CSV.
        client: Optional pre-configured bigquery.Client.

    Returns:
        Dictionary with execution details: query_name, is_export, output_file, row_count, status.
    """
    sql_path = Path(sql_script_path)
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    raw_sql = sql_path.read_text(encoding="utf-8", errors="replace")
    substituted_sql = substitute_parameters(raw_sql, parameters or {})

    query_name = sql_path.stem
    should_export = is_csv_export_query(substituted_sql)

    out_dir = Path(outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"{query_name}.csv"

    # Initialize BigQuery client using ADC
    if client is None:
        client = bigquery.Client(project=project_id) if project_id else bigquery.Client()

    logger.info(f"Submitting BigQuery job for {query_name} (Export to CSV: {should_export})")
    query_job = client.query(substituted_sql)
    results = query_job.result()

    row_count = 0
    exported_path: Optional[str] = None

    if should_export:
        if stream_to_csv:
            with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                header_written = False
                for page in results.pages:
                    for row in page:
                        if not header_written:
                            writer.writerow(row.keys())
                            header_written = True
                        writer.writerow(list(row.values()))
                        row_count += 1
        else:
            df = results.to_dataframe(create_bqstorage_client=False)
            df.to_csv(output_file, index=False)
            row_count = len(df)

        exported_path = str(output_file)
        logger.info(f"Exported {row_count} rows to {exported_path}")
    else:
        logger.info(f"Query {query_name} completed table creation/update in BigQuery.")

    return {
        "query_name": query_name,
        "is_export": should_export,
        "output_file": exported_path,
        "row_count": row_count if should_export else None,
        "status": "SUCCESS",
    }
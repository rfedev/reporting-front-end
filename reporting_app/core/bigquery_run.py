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
            from reporting_app.persistence.database import DatabaseManager
            from reporting_app.persistence.repository import Repository
            repo = Repository(DatabaseManager())
            wb = repo.get_workbench_dataset().strip()
            if wb and "." in wb:
                project_id = wb.split(".")[0].strip()
        except Exception:
            pass

    out_dir = Path(outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine target output CSV filenames
    target_csv_filenames: List[str] = []
    if output_filename:
        if isinstance(output_filename, (list, tuple)):
            target_csv_filenames = [str(f).strip() for f in output_filename if str(f).strip()]
        elif "," in str(output_filename):
            target_csv_filenames = [s.strip() for s in str(output_filename).split(",") if s.strip()]
        else:
            target_csv_filenames = [str(output_filename).strip()]
    elif output_csv_tables:
        target_csv_filenames = list(output_csv_tables)
    else:
        target_csv_filenames = [f"{query_name}.csv"]

    # Ensure all filenames have .csv extension
    target_csv_filenames = [
        f if f.lower().endswith(".csv") else f"{f}.csv"
        for f in target_csv_filenames
    ]

    # Initialize BigQuery client using ADC with detected or explicit project_id
    if client is None:
        client = bigquery.Client(project=project_id) if project_id else bigquery.Client()

    logger.info(
        f"Submitting BigQuery job for {query_name} (Project: {project_id}, "
        f"Export to CSV: {should_export}, Create Tables: {has_output_tables})"
    )
    query_job = client.query(substituted_sql, project=project_id)
    results = query_job.result()

    total_row_count = 0
    exported_paths: List[str] = []
    export_details: List[Dict[str, Any]] = []

    def export_row_iterator_to_file(row_iter: Any, out_file: Path) -> int:
        written_count = 0
        total_rows_meta = getattr(row_iter, "total_rows", None)
        auto_stream = (isinstance(total_rows_meta, (int, float)) and total_rows_meta > 500_000)
        use_stream = stream_to_csv or auto_stream

        if auto_stream and not stream_to_csv:
            logger.info(
                f"Auto-enabling stream_to_csv for {out_file.name}: "
                f"result set has {int(total_rows_meta):,} rows (> 500,000 threshold)."
            )

        if use_stream:
            with open(out_file, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                header_written = False
                for page in row_iter.pages:
                    for row in page:
                        if not header_written:
                            writer.writerow(row.keys())
                            header_written = True
                        writer.writerow(list(row.values()))
                        written_count += 1
        else:
            try:
                df = row_iter.to_dataframe(create_bqstorage_client=False)
                df.to_csv(out_file, index=False)
                written_count = len(df)
            except Exception as df_err:
                logger.debug(f"Dataframe conversion fallback to manual CSV writing: {df_err}")
                with open(out_file, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.writer(csvfile)
                    header_written = False
                    for page in row_iter.pages:
                        for row in page:
                            if not header_written:
                                writer.writerow(row.keys())
                                header_written = True
                            writer.writerow(list(row.values()))
                            written_count += 1
        return written_count

    if should_export:
        # Check child jobs for multi-statement scripts
        child_select_results: List[Any] = []
        try:
            for child_job in client.list_jobs(parent_job=query_job):
                if hasattr(child_job, "destination") and child_job.destination:
                    child_res = child_job.result()
                    if getattr(child_res, "total_rows", 0) is not None and child_res.total_rows > 0:
                        child_select_results.append(child_res)
        except Exception as e:
            logger.debug(f"Checking multi-query script child jobs: {e}")

        # In case list_jobs returns in reverse chronological order, ensure proper order
        if len(child_select_results) > 1:
            try:
                child_select_results.reverse()
            except Exception:
                pass

        if child_select_results:
            for idx, c_res in enumerate(child_select_results):
                fname = target_csv_filenames[idx] if idx < len(target_csv_filenames) else f"{query_name}_{idx+1}.csv"
                dest_file = out_dir / fname
                cnt = export_row_iterator_to_file(c_res, dest_file)
                total_row_count += cnt
                exported_paths.append(str(dest_file))
                export_details.append({"filename": dest_file.name, "path": str(dest_file), "row_count": cnt})
                logger.info(f"Exported {cnt} rows to {dest_file}")
        else:
            fname = target_csv_filenames[0] if target_csv_filenames else f"{query_name}.csv"
            dest_file = out_dir / fname
            cnt = export_row_iterator_to_file(results, dest_file)
            total_row_count += cnt
            exported_paths.append(str(dest_file))
            export_details.append({"filename": dest_file.name, "path": str(dest_file), "row_count": cnt})
            logger.info(f"Exported {cnt} rows to {dest_file}")
    else:
        # Determine rows created/added to destination or created table(s)
        dml_rows = getattr(query_job, "num_dml_affected_rows", None)
        if isinstance(dml_rows, (int, float)) and dml_rows >= 0:
            total_row_count = int(dml_rows)
        else:
            dest_ref = getattr(query_job, "destination", None)
            if dest_ref:
                try:
                    table_obj = client.get_table(dest_ref)
                    if getattr(table_obj, "num_rows", None) is not None:
                        total_row_count = table_obj.num_rows
                except Exception as e:
                    logger.debug(f"Failed to fetch num_rows from job destination {dest_ref}: {e}")

            if total_row_count == 0 and output_tables:
                for tbl in output_tables:
                    try:
                        table_obj = client.get_table(tbl)
                        if getattr(table_obj, "num_rows", None) is not None:
                            total_row_count += table_obj.num_rows
                    except Exception as e:
                        logger.debug(f"Failed to fetch num_rows from table {tbl}: {e}")

        logger.info(f"Query {query_name} completed table creation/update in BigQuery ({total_row_count} rows).")

    exported_path_str = ", ".join(exported_paths) if exported_paths else None

    # BigQuery Execution Telemetry
    job_started_iso = query_job.started.isoformat() if getattr(query_job, "started", None) else None
    job_ended_iso = query_job.ended.isoformat() if getattr(query_job, "ended", None) else None
    duration_sec = 0.0
    if getattr(query_job, "started", None) and getattr(query_job, "ended", None):
        duration_sec = (query_job.ended - query_job.started).total_seconds()

    return {
        "query_name": query_name,
        "is_export": should_export,
        "has_output_tables": has_output_tables,
        "output_tables": output_tables,
        "output_file": exported_path_str,
        "output_files": exported_paths,
        "export_details": export_details,
        "row_count": total_row_count if (should_export or has_output_tables or total_row_count > 0) else None,
        "status": "SUCCESS",
        "project_id": project_id,
        "submitted_query": substituted_sql,
        "job_started": job_started_iso,
        "job_ended": job_ended_iso,
        "duration_seconds": duration_sec,
        "total_bytes_processed": getattr(query_job, "total_bytes_processed", None),
        "total_bytes_billed": getattr(query_job, "total_bytes_billed", None),
        "slot_millis": getattr(query_job, "slot_millis", None),
        "cache_hit": getattr(query_job, "cache_hit", None),
    }


def run_bigquery_import_file(
    file_path: Optional[str | Path] = None,
    destination_table: str = "",
    has_headers: bool = True,
    sheet_name: Optional[str] = None,
    schema_mode: str = "auto",
    manual_schema: Optional[List[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    client: Optional[Any] = None,
    csv_path: Optional[str | Path] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Import a local CSV or XLSX file into BigQuery with auto or manual schema.

    Parameters:
        file_path: Path to the local CSV or XLSX file.
        destination_table: BigQuery table reference (e.g. 'project-id.dataset_id.table_id').
        has_headers: Whether the source file has a header row for column names.
        sheet_name: Worksheet name if importing an Excel (.xlsx) file.
        schema_mode: 'auto' for BigQuery autodetect, or 'manual' to apply manual_schema.
        manual_schema: List of dicts [{'name': '...', 'type': '...', 'mode': '...'}].
        project_id: Optional GCP project ID override.
        client: Optional pre-configured bigquery.Client.
        csv_path: Optional backwards-compatible alias for file_path.

    Returns:
        Dictionary with status, destination_table, row_count, project_id.
    """
    from reporting_app.core.sql_parser import clean_table_name
    import pandas as pd

    actual_path = file_path or csv_path
    if not actual_path:
        raise ValueError("file_path or csv_path must be provided.")

    file_p = Path(actual_path)
    if not file_p.exists():
        raise FileNotFoundError(f"Import file not found: {file_p}")

    clean_dest = clean_table_name(destination_table)
    parts = clean_dest.split(".")
    if len(parts) == 1 and clean_dest:
        try:
            from reporting_app.persistence.database import DatabaseManager
            from reporting_app.persistence.repository import Repository
            repo = Repository(DatabaseManager())
            wb = repo.get_workbench_dataset().strip()
            if wb:
                clean_dest = f"{wb.rstrip('.')}.{clean_dest}"
                parts = clean_dest.split(".")
        except Exception:
            pass

    if not project_id:
        if len(parts) >= 3:
            project_id = parts[0]
        else:
            try:
                from reporting_app.persistence.database import DatabaseManager
                from reporting_app.persistence.repository import Repository
                repo = Repository(DatabaseManager())
                wb = repo.get_workbench_dataset().strip()
                if wb and "." in wb:
                    project_id = wb.split(".")[0].strip()
            except Exception:
                pass

    if client is None:
        client = bigquery.Client(project=project_id) if project_id else bigquery.Client()

    bq_schema = None
    if schema_mode == "manual" and manual_schema:
        bq_schema = [
            bigquery.SchemaField(
                name=f["name"].strip(),
                field_type=f.get("type", "STRING").upper(),
                mode=f.get("mode", "NULLABLE").upper(),
                description=f.get("description") or None,
            )
            for f in manual_schema
            if f.get("name") and f["name"].strip()
        ]

    ext = file_p.suffix.lower()

    if ext in (".xlsx", ".xls"):
        # Excel file import using pandas and openpyxl
        sheet = sheet_name or 0
        df = pd.read_excel(file_p, sheet_name=sheet, header=0 if has_headers else None, engine="openpyxl")
        if not has_headers:
            if bq_schema and len(bq_schema) == len(df.columns):
                df.columns = [f.name for f in bq_schema]
            else:
                df.columns = [f"col_{i+1}" for i in range(len(df.columns))]
        else:
            df.columns = [str(c).strip() for c in df.columns]

        job_config = bigquery.LoadJobConfig(
            schema=bq_schema if bq_schema else None,
            autodetect=False if bq_schema else True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )

        logger.info(f"Loading Excel sheet '{sheet}' from {file_p} into BigQuery table {clean_dest}")
        job = client.load_table_from_dataframe(df, clean_dest, job_config=job_config, project=project_id)
        job.result()
    else:
        # CSV file import
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            schema=bq_schema if bq_schema else None,
            autodetect=False if bq_schema else True,
            skip_leading_rows=1 if has_headers else 0,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )

        logger.info(f"Loading CSV {file_p} into BigQuery table {clean_dest} (Headers: {has_headers}, Schema: {schema_mode})")
        with open(file_p, "rb") as source_file:
            job = client.load_table_from_file(source_file, clean_dest, job_config=job_config, project=project_id)
            job.result()

    row_count = None
    try:
        table = client.get_table(clean_dest)
        row_count = table.num_rows
    except Exception as e:
        logger.debug(f"Could not fetch table num_rows: {e}")

    job_started_iso = job.started.isoformat() if getattr(job, "started", None) else None
    job_ended_iso = job.ended.isoformat() if getattr(job, "ended", None) else None
    duration_sec = 0.0
    if getattr(job, "started", None) and getattr(job, "ended", None):
        duration_sec = (job.ended - job.started).total_seconds()

    return {
        "status": "SUCCESS",
        "destination_table": clean_dest,
        "row_count": row_count,
        "project_id": project_id,
        "file_path": str(file_p),
        "job_started": job_started_iso,
        "job_ended": job_ended_iso,
        "duration_seconds": duration_sec,
        "total_bytes_processed": getattr(job, "total_bytes_processed", None),
        "total_bytes_billed": getattr(job, "total_bytes_billed", None),
    }


# Backwards-compatible alias
run_bigquery_import_csv = run_bigquery_import_file
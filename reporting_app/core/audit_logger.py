"""Google Cloud BigQuery audit database module.

Appends process flow execution information, runtime parameters, and node-by-node
execution statuses to the `<workbench_dataset>.audit` table in BigQuery.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import threading
from typing import Any, Dict, List, Optional
import warnings

# Suppress noisy Google Cloud SDK ADC warnings and missing default project logs
warnings.filterwarnings("ignore", category=UserWarning, module="google.auth._default")
logging.getLogger("google.auth._default").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def get_authenticated_user_account() -> str:
    """Return the Google Cloud account email or fall back to system user."""
    # 1. Try google.auth.default() credentials
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="google.auth._default")
            import google.auth
            credentials, _ = google.auth.default()
            email = getattr(credentials, "service_account_email", None) or getattr(credentials, "_service_account_email", None)
            if email:
                return str(email)
            account = getattr(credentials, "account", None) or getattr(credentials, "_account", None)
            if account:
                return str(account)
    except Exception:
        pass

    # 2. Try reading Application Default Credentials JSON file
    try:
        adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not adc_path:
            adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        else:
            adc_path = Path(adc_path)
        if adc_path.exists():
            data = json.loads(adc_path.read_text(encoding="utf-8"))
            if "client_email" in data and data["client_email"]:
                return str(data["client_email"])
            if "account" in data and data["account"]:
                return str(data["account"])
    except Exception:
        pass

    # 3. Try reading gcloud active account
    try:
        import subprocess
        proc = subprocess.run(
            ["gcloud", "config", "get-value", "account"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode == 0 and proc.stdout.strip() and "(unset)" not in proc.stdout:
            return proc.stdout.strip()
    except Exception:
        pass

    # 4. Fallback to OS username
    try:
        import getpass
        return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
    except Exception:
        return "unknown"


def get_machine_name() -> str:
    """Return hostname/machine name."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def get_audit_table_schema() -> list:
    """Return BigQuery SchemaField list for the audit table."""
    from google.cloud import bigquery

    return [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED", description="Unique UUID for the flow run"),
        bigquery.SchemaField("report_name", "STRING", mode="REQUIRED", description="Name of the report"),
        bigquery.SchemaField("flow_name", "STRING", mode="REQUIRED", description="Name of the process flow"),
        bigquery.SchemaField("node_name", "STRING", mode="REQUIRED", description="Name of the node / query / import"),
        bigquery.SchemaField("node_type", "STRING", mode="NULLABLE", description="Type of node: query or import_csv"),
        bigquery.SchemaField("step_number", "INTEGER", mode="NULLABLE", description="1-based step index"),
        bigquery.SchemaField("total_steps", "INTEGER", mode="NULLABLE", description="Total number of steps in run"),
        bigquery.SchemaField("node_status", "STRING", mode="REQUIRED", description="SUCCESS, FAILED, or SKIPPED"),
        bigquery.SchemaField("flow_status", "STRING", mode="REQUIRED", description="SUCCESS, FAILED, or CANCELLED"),
        bigquery.SchemaField("node_start_time", "TIMESTAMP", mode="NULLABLE", description="Start timestamp of node"),
        bigquery.SchemaField("node_end_time", "TIMESTAMP", mode="NULLABLE", description="End timestamp of node"),
        bigquery.SchemaField("node_duration_seconds", "FLOAT", mode="NULLABLE", description="Duration of node in seconds"),
        bigquery.SchemaField("flow_start_time", "TIMESTAMP", mode="NULLABLE", description="Start timestamp of overall flow"),
        bigquery.SchemaField("flow_end_time", "TIMESTAMP", mode="NULLABLE", description="End timestamp of overall flow"),
        bigquery.SchemaField("flow_duration_seconds", "FLOAT", mode="NULLABLE", description="Duration of flow in seconds"),
        bigquery.SchemaField("parameters", "STRING", mode="NULLABLE", description="JSON string of runtime parameters"),
        bigquery.SchemaField("user_account", "STRING", mode="NULLABLE", description="GCP account email or system user"),
        bigquery.SchemaField("machine_name", "STRING", mode="NULLABLE", description="Hostname of machine executing flow"),
        bigquery.SchemaField("error_message", "STRING", mode="NULLABLE", description="Error message if failed"),
        bigquery.SchemaField("recorded_at", "TIMESTAMP", mode="REQUIRED", description="Audit record creation timestamp"),
    ]


def ensure_audit_table(client: Any, table_ref: str) -> Any:
    """Idempotently ensure the audit table exists in BigQuery."""
    from google.cloud import bigquery

    schema = get_audit_table_schema()
    table = bigquery.Table(table_ref, schema=schema)
    try:
        return client.create_table(table, exists_ok=True)
    except Exception as e:
        logger.warning(f"Could not automatically create BigQuery audit table {table_ref}: {e}")
        return None


def record_audit_run(
    workbench_dataset: str,
    flow_data: Dict[str, Any],
    step_records: List[Dict[str, Any]],
    client: Optional[Any] = None,
) -> bool:
    """Append audit records for all execution nodes to `<workbench_dataset>.audit`.

    Args:
        workbench_dataset: Project and dataset configured in settings (e.g. 'proj.dataset')
        flow_data: Summary metadata for the flow run:
            - run_id (str)
            - report_name (str)
            - flow_name (str)
            - flow_status (str: SUCCESS, FAILED, CANCELLED)
            - flow_start_time (str ISO)
            - flow_end_time (str ISO)
            - flow_duration_seconds (float)
            - parameters (dict or json str)
            - error_message (str, optional)
        step_records: List of step execution dicts:
            - node_name (str)
            - node_type (str)
            - step_number (int)
            - node_status (str: SUCCESS, FAILED, SKIPPED)
            - node_start_time (str ISO or None)
            - node_end_time (str ISO or None)
            - node_duration_seconds (float)
            - error_message (str, optional)
        client: Optional pre-configured bigquery.Client

    Returns:
        True if inserted successfully, False otherwise.
    """
    cleaned_wb = (workbench_dataset or "").strip()
    if not cleaned_wb or "." not in cleaned_wb:
        logger.debug(f"Audit table skipped: workbench_dataset '{workbench_dataset}' is not in 'project.dataset' format.")
        return False

    parts = cleaned_wb.split(".")
    project_id = parts[0].strip()
    dataset_id = parts[1].strip()
    table_id = f"{project_id}.{dataset_id}.audit"

    if project_id and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = project_id

    try:
        from google.cloud import bigquery

        if client is None:
            client = bigquery.Client(project=project_id)

        ensure_audit_table(client, table_id)

        # Prepare parameters JSON string
        params_val = flow_data.get("parameters", {})
        if isinstance(params_val, (dict, list)):
            params_str = json.dumps(params_val, default=str)
        else:
            params_str = str(params_val or "")

        user_acc = flow_data.get("user_account") or get_authenticated_user_account()
        machine = flow_data.get("machine_name") or get_machine_name()
        recorded_at = datetime.now(timezone.utc).isoformat()

        rows_to_insert = []
        total_steps = len(step_records)

        for step in step_records:
            row = {
                "run_id": flow_data.get("run_id", ""),
                "report_name": flow_data.get("report_name", ""),
                "flow_name": flow_data.get("flow_name", ""),
                "node_name": step.get("node_name", ""),
                "node_type": step.get("node_type", "query"),
                "step_number": step.get("step_number", 1),
                "total_steps": total_steps,
                "node_status": step.get("node_status", "SKIPPED"),
                "flow_status": flow_data.get("flow_status", "UNKNOWN"),
                "node_start_time": step.get("node_start_time"),
                "node_end_time": step.get("node_end_time"),
                "node_duration_seconds": float(step.get("node_duration_seconds", 0.0) or 0.0),
                "flow_start_time": flow_data.get("flow_start_time"),
                "flow_end_time": flow_data.get("flow_end_time"),
                "flow_duration_seconds": float(flow_data.get("flow_duration_seconds", 0.0) or 0.0),
                "parameters": params_str,
                "user_account": user_acc,
                "machine_name": machine,
                "error_message": step.get("error_message") or flow_data.get("error_message") or "",
                "recorded_at": recorded_at,
            }
            rows_to_insert.append(row)

        if not rows_to_insert:
            return True

        # 1. Attempt batch load job (supported on Free Tier, Sandbox, and Paid tiers without streaming restrictions)
        try:
            job_config = bigquery.LoadJobConfig(
                schema=get_audit_table_schema(),
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            )
            load_job = client.load_table_from_json(rows_to_insert, table_id, job_config=job_config)
            if hasattr(load_job, "result"):
                load_job.result(timeout=60)
            logger.info(f"Successfully recorded {len(rows_to_insert)} audit row(s) to BigQuery table '{table_id}'.")
            return True
        except Exception as load_err:
            logger.debug(f"Batch load job for audit logging failed ({load_err}), falling back to streaming insert...")

        # 2. Fallback to streaming insert (tabledata.insertAll)
        errors = client.insert_rows_json(table_id, rows_to_insert)
        if errors:
            logger.warning(f"BigQuery audit insert reported errors for {table_id}: {errors}")
            return False

        logger.info(f"Successfully recorded {len(rows_to_insert)} audit row(s) to BigQuery table '{table_id}'.")
        return True

    except Exception as e:
        logger.warning(f"Failed to record audit run to BigQuery table '{table_id}': {e}")
        return False


def record_audit_run_async(
    workbench_dataset: str,
    flow_data: Dict[str, Any],
    step_records: List[Dict[str, Any]],
) -> threading.Thread:
    """Record audit run in a background daemon thread so UI execution is not delayed."""
    def _run():
        record_audit_run(workbench_dataset, flow_data, step_records)

    th = threading.Thread(target=_run, daemon=True, name="BigQueryAuditLogger")
    th.start()
    return th

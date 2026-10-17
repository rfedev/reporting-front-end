"""Background execution engine for process flows and queries."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
import uuid

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from reporting_app.core.bigquery_run import run_bigquery_import_file, run_bigquery_script
from reporting_app.persistence.repository import Repository
from reporting_app.utils.date_calc import format_filename_with_date

logger = logging.getLogger(__name__)


class FlowWorker(QThread):
    """Background worker thread executing steps in a process flow."""

    flow_started = Signal(list)  # list of step dicts
    step_started = Signal(int, int, str)  # current_step (1-based), total_steps, step_name
    step_completed = Signal(int, int, str, str, float, str)  # step_idx, total, step_name, status, duration_sec, error_msg
    progress_updated = Signal(int, int, str)  # current_step, total_steps, step_name
    log_message = Signal(str)  # human-readable log line
    flow_finished = Signal(bool, str, list, list)  # success, summary_message, results_log, errors

    def __init__(
        self,
        steps: List[Dict[str, Any]],
        report_folder: Path,
        report_name: str,
        flow_name: str,
        param_values: Dict[str, str],
        filename_date: str = "",
        csv_map: Optional[Dict[str, str]] = None,
        outputs_dir: Optional[Path] = None,
        repo: Optional[Repository] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.steps = steps
        self.report_folder = Path(report_folder)
        self.report_name = report_name
        self.flow_name = flow_name or "Process Flow"
        self.param_values = dict(param_values)
        self.filename_date = filename_date
        self.csv_map = dict(csv_map or {})
        self.outputs_dir = Path(outputs_dir) if outputs_dir else (self.report_folder / "outputs")
        self.repo = repo
        self._is_cancelled: bool = False
        self._active_bq_job: Optional[Any] = None
        self._job_lock = threading.Lock()

    def _on_job_created(self, job: Any) -> None:
        """Store reference to the active BigQuery job so it can be cancelled server-side."""
        with self._job_lock:
            self._active_bq_job = job
            should_cancel = self._is_cancelled
        if should_cancel and job is not None and hasattr(job, "cancel"):
            try:
                job.cancel()
            except Exception as e:
                logger.debug(f"Failed to cancel newly created BigQuery job: {e}")

    def _clear_active_job(self) -> None:
        with self._job_lock:
            self._active_bq_job = None

    def cancel(self) -> None:
        """Signal the worker to abort execution and request server-side BigQuery job cancellation."""
        self._is_cancelled = True
        logger.info("Cancellation requested for flow worker.")
        with self._job_lock:
            job = self._active_bq_job

        if job is not None and hasattr(job, "cancel"):
            def _cancel_remote_job(target_job: Any) -> None:
                try:
                    logger.info(f"Sending cancel request to BigQuery server for job {getattr(target_job, 'job_id', '')}...")
                    target_job.cancel()
                except Exception as e:
                    logger.debug(f"Could not cancel remote BigQuery job on server: {e}")

            threading.Thread(target=_cancel_remote_job, args=(job,), daemon=True).start()

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def _emit_skipped_from(self, start_idx_one_based: int, total_steps: int) -> None:
        """Emit SKIPPED status for all remaining unexecuted steps from start_idx_one_based onwards."""
        for rem_idx in range(start_idx_one_based, total_steps + 1):
            rem_step = self.steps[rem_idx - 1]
            rem_name = rem_step.get("name", f"Step {rem_idx}")
            self.step_completed.emit(rem_idx, total_steps, rem_name, "SKIPPED", 0.0, "")

    def run(self) -> None:
        """Execute all steps sequentially in the background thread."""
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        flow_start_time = datetime.now(timezone.utc).isoformat()

        results_log: List[str] = []
        errors: List[str] = []
        total_steps = len(self.steps)
        failed_node_name: Optional[str] = None

        self.flow_started.emit(self.steps)
        self.log_message.emit(f"Starting execution of '{self.flow_name}' ({total_steps} step(s))...")

        for idx, step in enumerate(self.steps, start=1):
            if self._is_cancelled:
                msg = "⚠️ Process flow cancelled by user."
                self.log_message.emit(msg)
                results_log.append(msg)
                errors.append("Execution cancelled by user.")
                self._emit_skipped_from(idx, total_steps)
                break

            stype = step.get("type")
            sname = step.get("name", f"Step {idx}")
            step_start_time = datetime.now(timezone.utc).isoformat()
            t0 = datetime.now(timezone.utc)

            self.step_started.emit(idx, total_steps, sname)
            self.progress_updated.emit(idx, total_steps, sname)

            if stype == "import_csv":
                imp_items = step.get("items", [])
                imp_records = []
                step_failed = False
                step_err_msg = ""

                for imp in imp_items:
                    if self._is_cancelled:
                        step_failed = True
                        step_err_msg = "Execution cancelled by user."
                        break

                    f_path = (imp.get("file_path") or imp.get("csv_path") or "").strip()
                    d_table = imp.get("output_table", "").strip()
                    headers = imp.get("has_headers", True)
                    sheet_name = imp.get("sheet_name")
                    schema_mode = imp.get("schema_mode", "auto")
                    manual_schema = imp.get("manual_schema")

                    if not f_path or not d_table:
                        continue

                    if self.filename_date:
                        f_path = format_filename_with_date(f_path, self.filename_date)

                    resolved_file = Path(f_path)
                    if not resolved_file.is_absolute():
                        cand = self.report_folder / "inputs" / resolved_file
                        if cand.exists() or not resolved_file.exists():
                            resolved_file = cand

                    self.log_message.emit(f"[{idx}/{total_steps}] Importing {resolved_file.name} -> {d_table}...")

                    try:
                        imp_res = run_bigquery_import_file(
                            file_path=resolved_file,
                            destination_table=d_table,
                            has_headers=headers,
                            sheet_name=sheet_name,
                            schema_mode=schema_mode,
                            manual_schema=manual_schema,
                            on_job_created=self._on_job_created,
                        )
                        self._clear_active_job()

                        if self._is_cancelled:
                            step_failed = True
                            step_err_msg = "Execution cancelled by user."
                            break

                        row_cnt = imp_res.get("row_count")
                        cnt_str = f"{row_cnt:,} rows" if row_cnt is not None else "completed"
                        log_line = f"[{idx}/{total_steps}] Imported '{f_path}' into '{d_table}' ({cnt_str})"
                        results_log.append(log_line)
                        self.log_message.emit(log_line)
                        imp_records.append({
                            "file_path": str(f_path),
                            "destination_table": d_table,
                            "row_count": row_cnt,
                            "has_headers": headers,
                            "sheet_name": sheet_name,
                            "schema_mode": schema_mode,
                        })
                    except Exception as e:
                        self._clear_active_job()
                        failed_node_name = sname
                        if self._is_cancelled:
                            err_msg = f"Import cancelled for {d_table}."
                        else:
                            err_msg = f"Import failed for {d_table}: {e}"
                        step_err_msg = err_msg
                        errors.append(err_msg)
                        results_log.append(f"[{idx}/{total_steps}] ❌ {err_msg}")
                        self.log_message.emit(f"[{idx}/{total_steps}] ❌ {err_msg}")
                        step_failed = True

                        if self.repo:
                            t1 = datetime.now(timezone.utc)
                            self.repo.record_execution_log({
                                "run_id": run_id,
                                "flow_start_time": flow_start_time,
                                "node_start_time": step_start_time,
                                "node_end_time": t1.isoformat(),
                                "duration_seconds": (t1 - t0).total_seconds(),
                                "report_name": self.report_name,
                                "flow_name": self.flow_name,
                                "node_type": "import_csv",
                                "node_name": sname,
                                "status": "FAILED",
                                "error_message": err_msg,
                                "import_details_json": imp_records,
                            })
                        break

                t1 = datetime.now(timezone.utc)
                step_dur = (t1 - t0).total_seconds()

                if step_failed or self._is_cancelled:
                    if not step_err_msg and self._is_cancelled:
                        step_err_msg = "Execution cancelled by user."
                        errors.append(step_err_msg)
                    self.step_completed.emit(idx, total_steps, sname, "FAILED", step_dur, step_err_msg)
                    self._emit_skipped_from(idx + 1, total_steps)
                    break

                if self.repo and imp_records:
                    total_rows = sum(r.get("row_count") or 0 for r in imp_records)
                    self.repo.record_execution_log({
                        "run_id": run_id,
                        "flow_start_time": flow_start_time,
                        "node_start_time": step_start_time,
                        "node_end_time": t1.isoformat(),
                        "duration_seconds": step_dur,
                        "report_name": self.report_name,
                        "flow_name": self.flow_name,
                        "node_type": "import_csv",
                        "node_name": sname,
                        "status": "SUCCESS",
                        "output_rows": total_rows,
                        "import_details_json": imp_records,
                    })

                self.step_completed.emit(idx, total_steps, sname, "SUCCESS", step_dur, "")

            elif stype == "query":
                qname = sname
                qpath = step.get("file_path")
                if not qpath or not Path(qpath).exists():
                    failed_node_name = qname
                    err = f"Query '{qname}' SQL file not found: {qpath}"
                    errors.append(err)
                    results_log.append(f"[{idx}/{total_steps}] ❌ {qname}: {err}")
                    self.log_message.emit(f"[{idx}/{total_steps}] ❌ {qname}: {err}")
                    t1 = datetime.now(timezone.utc)
                    step_dur = (t1 - t0).total_seconds()
                    if self.repo:
                        self.repo.record_execution_log({
                            "run_id": run_id,
                            "flow_start_time": flow_start_time,
                            "node_start_time": step_start_time,
                            "node_end_time": t1.isoformat(),
                            "duration_seconds": step_dur,
                            "report_name": self.report_name,
                            "flow_name": self.flow_name,
                            "node_type": "query",
                            "node_name": qname,
                            "status": "FAILED",
                            "error_message": err,
                        })
                    self.step_completed.emit(idx, total_steps, qname, "FAILED", step_dur, err)
                    self._emit_skipped_from(idx + 1, total_steps)
                    break

                self.log_message.emit(f"[{idx}/{total_steps}] ⚙️ Running query {qname}...")

                try:
                    custom_csv = self.csv_map.get(qname)
                    if custom_csv and self.filename_date:
                        custom_csv = format_filename_with_date(custom_csv, self.filename_date)

                    res = run_bigquery_script(
                        sql_script_path=Path(qpath),
                        report_name=self.report_name,
                        outputs_dir=self.outputs_dir,
                        parameters=self.param_values,
                        output_filename=custom_csv,
                        on_job_created=self._on_job_created,
                    )
                    self._clear_active_job()

                    t1 = datetime.now(timezone.utc)
                    wall_node_dur = (t1 - t0).total_seconds()
                    eff_dur = max(float(res.get("duration_seconds", 0.0) or 0.0), wall_node_dur)

                    if self._is_cancelled:
                        err_msg = f"Query '{qname}' cancelled by user."
                        errors.append(err_msg)
                        results_log.append(f"[{idx}/{total_steps}] ⚠️ {err_msg}")
                        self.log_message.emit(f"[{idx}/{total_steps}] ⚠️ {err_msg}")
                        self.step_completed.emit(idx, total_steps, qname, "FAILED", eff_dur, err_msg)
                        self._emit_skipped_from(idx + 1, total_steps)
                        break

                    if res.get("is_export"):
                        details = res.get("export_details", [])
                        if len(details) == 1:
                            d = details[0]
                            line = f"[{idx}/{total_steps}] ✅ {qname}: Exported '{d['filename']}' ({d['row_count']:,} rows)"
                            results_log.append(line)
                            self.log_message.emit(line)
                        elif len(details) > 1:
                            lines = [f"[{idx}/{total_steps}] ✅ {qname}: Exported {len(details)} tables:"]
                            for d in details:
                                lines.append(f"  * {d['filename']} ({d['row_count']:,} rows)")
                            block = "\n".join(lines)
                            results_log.append(block)
                            self.log_message.emit(block)
                        else:
                            line = f"[{idx}/{total_steps}] ✅ {qname}: Exported {res.get('row_count', 0):,} rows to {res.get('output_file')}"
                            results_log.append(line)
                            self.log_message.emit(line)
                    else:
                        tbl_rows = res.get("row_count")
                        tbl_rows_str = f" ({tbl_rows:,} rows)" if tbl_rows is not None else ""
                        line = f"[{idx}/{total_steps}] ✅ {qname}: Executed table creation/update in BigQuery{tbl_rows_str}"
                        results_log.append(line)
                        self.log_message.emit(line)

                    if self.repo:
                        self.repo.record_execution_log({
                            "run_id": run_id,
                            "flow_start_time": flow_start_time,
                            "node_start_time": step_start_time,
                            "node_end_time": t1.isoformat(),
                            "duration_seconds": eff_dur,
                            "bq_duration_seconds": res.get("bq_duration_seconds"),
                            "report_name": self.report_name,
                            "flow_name": self.flow_name,
                            "node_type": "query",
                            "node_name": qname,
                            "status": "SUCCESS",
                            "submitted_query": res.get("submitted_query"),
                            "output_rows": res.get("row_count"),
                            "total_bytes_processed": res.get("total_bytes_processed"),
                            "total_bytes_billed": res.get("total_bytes_billed"),
                            "slot_millis": res.get("slot_millis"),
                            "cache_hit": res.get("cache_hit"),
                            "export_details_json": res.get("export_details", []),
                        })

                    self.step_completed.emit(idx, total_steps, qname, "SUCCESS", eff_dur, "")

                except Exception as e:
                    self._clear_active_job()
                    failed_node_name = qname
                    t1 = datetime.now(timezone.utc)
                    step_dur = (t1 - t0).total_seconds()
                    if self._is_cancelled:
                        err_msg = f"Cancelled by user ({e})"
                    else:
                        err_msg = str(e)
                    errors.append(f"{qname}: {err_msg}")
                    line = f"[{idx}/{total_steps}] ❌ {qname}: Failed ({err_msg})"
                    results_log.append(line)
                    self.log_message.emit(line)
                    if self.repo:
                        self.repo.record_execution_log({
                            "run_id": run_id,
                            "flow_start_time": flow_start_time,
                            "node_start_time": step_start_time,
                            "node_end_time": t1.isoformat(),
                            "duration_seconds": step_dur,
                            "report_name": self.report_name,
                            "flow_name": self.flow_name,
                            "node_type": "query",
                            "node_name": qname,
                            "status": "FAILED",
                            "error_message": err_msg,
                        })
                    self.step_completed.emit(idx, total_steps, qname, "FAILED", step_dur, err_msg)
                    self._emit_skipped_from(idx + 1, total_steps)
                    break

        success = not bool(errors) and not self._is_cancelled
        if self._is_cancelled:
            summary = "Execution was cancelled by user."
        elif success:
            summary = f"Process flow '{self.flow_name}' executed successfully ({total_steps} step(s))."
        else:
            failed_header = f"Error in step '{failed_node_name}':\n\n" if failed_node_name else ""
            summary = f"{failed_header}" + "\n".join(errors)

        self.flow_finished.emit(success, summary, results_log, errors)


class FlowRunner(QObject):
    """Coordinates non-blocking asynchronous execution of a process flow with FlowWorker."""

    def __init__(self, parent_window: Optional[QWidget] = None, repo: Optional[Repository] = None):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.repo = repo

    def create_worker(
        self,
        steps: List[Dict[str, Any]],
        report_folder: Path,
        report_name: str,
        flow_name: str,
        param_values: Dict[str, str],
        filename_date: str = "",
        csv_map: Optional[Dict[str, str]] = None,
        outputs_dir: Optional[Path] = None,
    ) -> FlowWorker:
        """Create and return a configured FlowWorker for non-blocking execution."""
        return FlowWorker(
            steps=steps,
            report_folder=report_folder,
            report_name=report_name,
            flow_name=flow_name,
            param_values=param_values,
            filename_date=filename_date,
            csv_map=csv_map,
            outputs_dir=outputs_dir,
            repo=self.repo,
            parent=self,
        )

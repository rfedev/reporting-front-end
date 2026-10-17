"""Background execution engine for process flows and queries."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
import uuid

import queue

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from reporting_app.core.audit_logger import record_audit_run_async
from reporting_app.core.bigquery_run import run_bigquery_import_file, run_bigquery_script
from reporting_app.persistence.repository import Repository
from reporting_app.utils.date_calc import calculate_import_file_date, format_filename_with_date

logger = logging.getLogger(__name__)


class LanTransferWorker(threading.Thread):
    """Background worker thread that transfers exported files across a LAN folder."""

    def __init__(
        self,
        lan_folder: Path,
        on_started: Optional[Any] = None,
        on_progress: Optional[Any] = None,
        on_completed: Optional[Any] = None,
        on_finished: Optional[Any] = None,
        chunk_size: int = 1024 * 512,
    ):
        super().__init__(daemon=True, name="LanTransferWorker")
        self.lan_folder = Path(lan_folder)
        self.on_started = on_started
        self.on_progress = on_progress
        self.on_completed = on_completed
        self.on_finished = on_finished
        self.chunk_size = chunk_size

        self._queue: queue.Queue = queue.Queue()
        self._total_enqueued = 0
        self._processed_count = 0
        self._copied_count = 0
        self._errors: List[str] = []
        self._stop_requested = False

    def enqueue(self, file_path: Path) -> None:
        """Enqueue an exported CSV file for copying to the LAN folder."""
        self._total_enqueued += 1
        self._queue.put(file_path)

    def finish_and_wait(self, timeout: float = 300.0) -> None:
        """Signal queue completion and block until all queued LAN transfers finish."""
        self._queue.put(None)
        self.join(timeout=timeout)

    def cancel(self) -> None:
        """Request immediate cancellation of pending LAN transfers."""
        self._stop_requested = True
        self._queue.put(None)

    def run(self) -> None:
        try:
            self.lan_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            err_msg = f"Cannot access LAN folder '{self.lan_folder}': {e}"
            logger.warning(err_msg)
            self._errors.append(err_msg)
            if self.on_finished:
                self.on_finished(False, 0, self._total_enqueued, err_msg)
            return

        while not self._stop_requested:
            item = self._queue.get()
            if item is None:
                break

            self._processed_count += 1
            file_idx = self._processed_count
            total_files = max(self._total_enqueued, file_idx)
            src_file = Path(item)
            dst_file = self.lan_folder / src_file.name

            if self.on_started:
                self.on_started(file_idx, total_files, src_file.name)

            copy_success = False
            copy_error = ""
            try:
                if not src_file.exists():
                    raise FileNotFoundError(f"Source file {src_file} does not exist.")

                total_bytes = src_file.stat().st_size
                copied_bytes = 0

                # Always overwrite dst_file
                with open(src_file, "rb") as fsrc, open(dst_file, "wb") as fdst:
                    if total_bytes == 0:
                        if self.on_progress:
                            self.on_progress(file_idx, total_files, src_file.name, 100.0)
                    else:
                        while not self._stop_requested:
                            chunk = fsrc.read(self.chunk_size)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            copied_bytes += len(chunk)
                            if self.on_progress:
                                pct = min(100.0, (copied_bytes / total_bytes) * 100.0)
                                self.on_progress(file_idx, total_files, src_file.name, pct)

                if not self._stop_requested:
                    copy_success = True
                    self._copied_count += 1
            except Exception as e:
                copy_error = str(e)
                self._errors.append(f"{src_file.name}: {copy_error}")
                logger.warning(f"Error copying '{src_file.name}' to LAN folder: {e}")

            if self.on_completed:
                self.on_completed(file_idx, total_files, src_file.name, copy_success, copy_error)

            self._queue.task_done()

        all_ok = (len(self._errors) == 0) and (self._copied_count == self._total_enqueued)
        if all_ok:
            summary = f"Successfully copied {self._copied_count}/{self._total_enqueued} file(s) to LAN."
        else:
            summary = f"Copied {self._copied_count}/{self._total_enqueued} file(s) with errors: {'; '.join(self._errors)}"
        if self.on_finished:
            self.on_finished(all_ok, self._copied_count, self._total_enqueued, summary)


class FlowWorker(QThread):
    """Background worker thread executing steps in a process flow."""

    flow_started = Signal(list)  # list of step dicts
    step_started = Signal(int, int, str)  # current_step (1-based), total_steps, step_name
    step_completed = Signal(int, int, str, str, float, str)  # step_idx, total, step_name, status, duration_sec, error_msg
    progress_updated = Signal(int, int, str)  # current_step, total_steps, step_name
    log_message = Signal(str)  # human-readable log line
    flow_finished = Signal(bool, str, list, list)  # success, summary_message, results_log, errors

    # Background LAN transfer signals
    lan_transfer_started = Signal(int, int, str)  # file_idx, total_files, filename
    lan_transfer_progress = Signal(int, int, str, float)  # file_idx, total_files, filename, pct
    lan_transfer_completed = Signal(int, int, str, bool, str)  # file_idx, total_files, filename, success, error_msg
    lan_transfer_finished = Signal(bool, int, int, str)  # all_success, total_copied, total_files, summary

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
        lan_output_folder: Optional[str] = None,
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
        self.lan_output_folder = Path(lan_output_folder.strip()) if (lan_output_folder and lan_output_folder.strip()) else None
        self._is_cancelled: bool = False
        self._active_bq_job: Optional[Any] = None
        self._job_lock = threading.Lock()
        self._lan_worker: Optional[LanTransferWorker] = None

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
        if self._lan_worker:
            self._lan_worker.cancel()
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

    def _emit_skipped_from(
        self,
        start_idx_one_based: int,
        total_steps: int,
        step_records: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Emit SKIPPED status for all remaining unexecuted steps from start_idx_one_based onwards."""
        for rem_idx in range(start_idx_one_based, total_steps + 1):
            rem_step = self.steps[rem_idx - 1]
            rem_name = rem_step.get("name", f"Step {rem_idx}")
            rem_type = rem_step.get("type", "query")
            self.step_completed.emit(rem_idx, total_steps, rem_name, "SKIPPED", 0.0, "")
            if step_records is not None:
                step_records.append({
                    "node_name": rem_name,
                    "node_type": rem_type,
                    "step_number": rem_idx,
                    "node_status": "SKIPPED",
                    "node_start_time": None,
                    "node_end_time": None,
                    "node_duration_seconds": 0.0,
                    "error_message": "",
                })

    def run(self) -> None:
        """Execute all steps sequentially in the background thread."""
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        t_flow_start = datetime.now(timezone.utc)
        flow_start_time = t_flow_start.isoformat()

        results_log: List[str] = []
        errors: List[str] = []
        step_records: List[Dict[str, Any]] = []
        total_steps = len(self.steps)
        failed_node_name: Optional[str] = None

        if self.lan_output_folder:
            self._lan_worker = LanTransferWorker(
                lan_folder=self.lan_output_folder,
                on_started=self.lan_transfer_started.emit,
                on_progress=self.lan_transfer_progress.emit,
                on_completed=self.lan_transfer_completed.emit,
                on_finished=self.lan_transfer_finished.emit,
            )
            self._lan_worker.start()

        self.flow_started.emit(self.steps)
        self.log_message.emit(f"Starting execution of '{self.flow_name}' ({total_steps} step(s))...")

        for idx, step in enumerate(self.steps, start=1):
            if self._is_cancelled:
                msg = "⚠️ Process flow cancelled by user."
                self.log_message.emit(msg)
                results_log.append(msg)
                errors.append("Execution cancelled by user.")
                self._emit_skipped_from(idx, total_steps, step_records)
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
                    date_option = imp.get("date_option", "Flow Date")

                    if not f_path or not d_table:
                        continue

                    effective_date = calculate_import_file_date(date_option, flow_date=self.filename_date)
                    if effective_date:
                        f_path = format_filename_with_date(f_path, effective_date)

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
                    step_records.append({
                        "node_name": sname,
                        "node_type": "import_csv",
                        "step_number": idx,
                        "node_status": "FAILED",
                        "node_start_time": step_start_time,
                        "node_end_time": t1.isoformat(),
                        "node_duration_seconds": step_dur,
                        "error_message": step_err_msg,
                    })
                    self.step_completed.emit(idx, total_steps, sname, "FAILED", step_dur, step_err_msg)
                    self._emit_skipped_from(idx + 1, total_steps, step_records)
                    break

                step_records.append({
                    "node_name": sname,
                    "node_type": "import_csv",
                    "step_number": idx,
                    "node_status": "SUCCESS",
                    "node_start_time": step_start_time,
                    "node_end_time": t1.isoformat(),
                    "node_duration_seconds": step_dur,
                    "error_message": "",
                })

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
                    step_records.append({
                        "node_name": qname,
                        "node_type": "query",
                        "step_number": idx,
                        "node_status": "FAILED",
                        "node_start_time": step_start_time,
                        "node_end_time": t1.isoformat(),
                        "node_duration_seconds": step_dur,
                        "error_message": err,
                    })
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
                    self._emit_skipped_from(idx + 1, total_steps, step_records)
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
                        step_records.append({
                            "node_name": qname,
                            "node_type": "query",
                            "step_number": idx,
                            "node_status": "FAILED",
                            "node_start_time": step_start_time,
                            "node_end_time": t1.isoformat(),
                            "node_duration_seconds": eff_dur,
                            "error_message": err_msg,
                        })
                        self.step_completed.emit(idx, total_steps, qname, "FAILED", eff_dur, err_msg)
                        self._emit_skipped_from(idx + 1, total_steps, step_records)
                        break

                    if res.get("is_export"):
                        details = res.get("export_details", [])
                        if self._lan_worker:
                            if details:
                                for d in details:
                                    p = d.get("path")
                                    if p and Path(p).exists():
                                        self._lan_worker.enqueue(Path(p))
                            elif res.get("output_file"):
                                out_cand = self.outputs_dir / res.get("output_file")
                                if out_cand.exists():
                                    self._lan_worker.enqueue(out_cand)

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

                    step_records.append({
                        "node_name": qname,
                        "node_type": "query",
                        "step_number": idx,
                        "node_status": "SUCCESS",
                        "node_start_time": step_start_time,
                        "node_end_time": t1.isoformat(),
                        "node_duration_seconds": eff_dur,
                        "error_message": "",
                    })

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
                    step_records.append({
                        "node_name": qname,
                        "node_type": "query",
                        "step_number": idx,
                        "node_status": "FAILED",
                        "node_start_time": step_start_time,
                        "node_end_time": t1.isoformat(),
                        "node_duration_seconds": step_dur,
                        "error_message": err_msg,
                    })
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
                    self._emit_skipped_from(idx + 1, total_steps, step_records)
                    break

        success = not bool(errors) and not self._is_cancelled
        if self._is_cancelled:
            summary = "Execution was cancelled by user."
        elif success:
            summary = f"Process flow '{self.flow_name}' executed successfully ({total_steps} step(s))."
        else:
            failed_header = f"Error in step '{failed_node_name}':\n\n" if failed_node_name else ""
            summary = f"{failed_header}" + "\n".join(errors)

        # Record audit run to Google Cloud BigQuery (<workbench_dataset>.audit)
        try:
            wb_dataset = ""
            if self.repo:
                wb_dataset = self.repo.get_workbench_dataset()
            if not wb_dataset:
                from reporting_app.persistence.database import DatabaseManager
                from reporting_app.persistence.repository import Repository
                wb_dataset = Repository(DatabaseManager()).get_workbench_dataset()

            if wb_dataset:
                flow_end_time = datetime.now(timezone.utc).isoformat()
                flow_dur = (datetime.now(timezone.utc) - t_flow_start).total_seconds()
                flow_status = "SUCCESS" if success else ("CANCELLED" if self._is_cancelled else "FAILED")
                flow_audit_data = {
                    "run_id": run_id,
                    "report_name": self.report_name,
                    "flow_name": self.flow_name,
                    "flow_status": flow_status,
                    "flow_start_time": flow_start_time,
                    "flow_end_time": flow_end_time,
                    "flow_duration_seconds": flow_dur,
                    "parameters": self.param_values,
                    "error_message": "; ".join(errors) if errors else "",
                }
                record_audit_run_async(wb_dataset, flow_audit_data, step_records)
        except Exception as e:
            logger.debug(f"Failed to initiate BigQuery audit logging: {e}")

        # Finalize background LAN transfer(s) if configured
        if self._lan_worker:
            if self._is_cancelled:
                self._lan_worker.cancel()
            else:
                self.log_message.emit("Waiting for background LAN transfer(s) to complete...")
                self._lan_worker.finish_and_wait()
                if self._lan_worker._errors:
                    for lerr in self._lan_worker._errors:
                        line = f"⚠️ LAN transfer warning: {lerr}"
                        results_log.append(line)
                        self.log_message.emit(line)
                elif self._lan_worker._copied_count > 0:
                    line = f"📁 Copied {self._lan_worker._copied_count} export file(s) to LAN: {self.lan_output_folder}"
                    results_log.append(line)
                    self.log_message.emit(line)

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
        lan_output_folder: Optional[str] = None,
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
            lan_output_folder=lan_output_folder,
            repo=self.repo,
            parent=self,
        )

"""Application controller for state management, scanning, and user actions."""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from reporting_app.core.file_scanner import FileScanner
from reporting_app.core.models import ProcessFlowInfo, QueryInfo, Report
from reporting_app.persistence.database import DatabaseManager
from reporting_app.persistence.repository import Repository
from reporting_app.controllers.watcher_service import FileWatcherService

logger = logging.getLogger(__name__)


class AppController(QObject):
    """Coordinates application business logic between views and persistence."""

    # Signals for presentation layer updates
    reports_updated = Signal(list)  # list of report names
    active_report_changed = Signal(object)  # Optional[Report]
    process_flows_updated = Signal(list)  # list of flow names
    queries_updated = Signal(list)  # list of query names
    status_changed = Signal(str)  # status text

    DEFAULT_PROCESS_FLOW_NAME = "Process Flow 01"

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        log_db_manager: Optional[DatabaseManager] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.db_manager = db_manager or DatabaseManager()
        self.repo = Repository(self.db_manager, log_db_manager=log_db_manager)

        # Initialize working directories from database or default
        working_dirs = self.repo.get_working_directories()
        self.scanner = FileScanner(working_dirs)

        # File watcher
        self.watcher = FileWatcherService(check_interval_ms=5000, parent=self)
        self._update_watcher_directories(working_dirs)
        self.watcher.set_enabled(self.repo.get_auto_scan())
        self.watcher.directory_changed.connect(self.scan)
        self.watcher.file_changed.connect(self._on_file_changed)

        # Runtime state
        self.reports_by_key: Dict[str, Report] = {}  # key: f"{r.name} [{r.directory_alias}]"
        self.reports_by_name: Dict[str, Report] = {}
        self.active_report: Optional[Report] = None
        self.active_flow_name: Optional[str] = None
        self.active_query_name: Optional[str] = None

    def _update_watcher_directories(self, working_dirs: List[Dict[str, str]]) -> None:
        target_paths = []
        for d in working_dirs:
            p_str = d.get("path", "")
            if p_str:
                p = Path(p_str).resolve()
                if p.exists():
                    target_paths.append(p)
        self.watcher.set_target_directories(target_paths)

    def initialize(self) -> None:
        """Initial scan and setup."""
        self.scan()

    def get_working_directories(self) -> List[Dict[str, str]]:
        return self.scanner.working_directories

    def set_working_directories(self, directories: List[Dict[str, str]]) -> None:
        """Update working directories, save to settings, and rescan."""
        self.repo.set_working_directories(directories)
        self.scanner.set_working_directories(directories)
        self._update_watcher_directories(directories)
        self.scan()

    def get_auto_scan(self) -> bool:
        return self.repo.get_auto_scan()

    def set_auto_scan(self, enabled: bool) -> None:
        """Update auto-scan preference."""
        self.repo.set_auto_scan(enabled)
        self.watcher.set_enabled(enabled)

    def get_log_database_path(self) -> str:
        return self.repo.get_log_database_path()

    def set_log_database_path(self, path_str: str) -> None:
        """Update configured log database path."""
        self.repo.set_log_database_path(path_str)

    def _create_default_process_flow(self, report: Report) -> ProcessFlowInfo:
        """Create a default 'Process Flow 01' if none exists for the report (Requirement 2)."""
        import json
        queries_dir = report.folder_path / "queries"
        queries_dir.mkdir(parents=True, exist_ok=True)
        flow_path = queries_dir / f"{self.DEFAULT_PROCESS_FLOW_NAME}.json"
        if not flow_path.exists():
            data = {
                "flow_name": self.DEFAULT_PROCESS_FLOW_NAME,
                "report_name": report.name,
                "query_names": [],
                "parameter_defaults": {},
                "show_full_table_names": False,
                "csv_filenames": {},
                "graph_session": {},
            }
            flow_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        info = ProcessFlowInfo(
            name=self.DEFAULT_PROCESS_FLOW_NAME,
            file_path=flow_path,
            report_name=report.name,
            query_names=[],
            parameter_defaults={},
            raw_data={},
            mtime=flow_path.stat().st_mtime,
        )
        report.process_flows.append(info)
        return info

    def scan(self) -> None:
        """Trigger scanning of reports and queries across all working directories."""
        self.status_changed.emit("Scanning reports directory...")
        reports = self.scanner.scan_all_reports()

        # Build map with display keys.
        # If multiple reports share the same name, append [alias] to distinguish them.
        from collections import Counter
        name_counts = Counter(r.name for r in reports)

        self.reports_by_key = {}
        for r in reports:
            if name_counts[r.name] > 1 and r.directory_alias:
                key = f"{r.name} [{r.directory_alias}]"
            else:
                key = r.name
            self.reports_by_key[key] = r

        self.reports_by_name = {r.name: r for r in reports}

        # Update SQLite table catalog for parsed queries
        for r in reports:
            for q in r.queries:
                if q.is_parsed:
                    self.repo.record_query_tables(r.name, q.name, q.input_tables, q.output_tables)

        display_keys = list(self.reports_by_key.keys())
        self.reports_updated.emit(display_keys)

        # Restore persisted report selection
        saved_report = self.repo.get_selected_report()

        # Find key corresponding to active_report or saved_report
        active_key = None
        if self.active_report:
            for k, r in self.reports_by_key.items():
                if r.folder_path == self.active_report.folder_path:
                    active_key = k
                    break

        if active_key and active_key in self.reports_by_key:
            self.select_report(active_key, preserve_flow=True)
        elif saved_report and saved_report in self.reports_by_key:
            self.select_report(saved_report)
        elif display_keys:
            self.select_report(display_keys[0])
        else:
            self.active_report = None
            self.active_report_changed.emit(None)
            self.process_flows_updated.emit([])
            self.queries_updated.emit([])

        self.status_changed.emit(f"Ready. Found {len(display_keys)} report(s).")

    def select_report(self, report_key: str, preserve_flow: bool = False) -> None:
        """Select active report by its key/name and populate flows and queries."""
        report = self.reports_by_key.get(report_key) or self.reports_by_name.get(report_key)
        self.active_report = report
        self.active_report_changed.emit(report)

        if not report:
            self.process_flows_updated.emit([])
            self.queries_updated.emit([])
            return

        # Persist selected report
        self.repo.set_selected_report(report_key)

        # Requirement 2: Remove 'All Queries'. If no process flow created yet, create 'Process Flow 01'
        if not report.process_flows:
            self._create_default_process_flow(report)

        flow_options = [f.name for f in report.process_flows]
        self.process_flows_updated.emit(flow_options)

        # Determine target process flow (persisted or preserved)
        saved_flow = self.repo.get_selected_flow(report.name)
        if preserve_flow and self.active_flow_name and self.active_flow_name in flow_options:
            target_flow = self.active_flow_name
        elif saved_flow and saved_flow in flow_options:
            target_flow = saved_flow
        else:
            target_flow = flow_options[0] if flow_options else ""

        self.select_process_flow(target_flow)

        # Populate queries list with all queries in the active report
        query_names = [q.name for q in report.queries]
        self.queries_updated.emit(query_names)

        saved_query = self.repo.get_selected_query(report.name)
        if saved_query and saved_query in query_names:
            self.select_query(saved_query)
        elif query_names:
            self.select_query(query_names[0])
        else:
            self.active_query_name = None

    def select_process_flow(self, flow_name: str) -> None:
        """Select active process flow and remember selection."""
        self.active_flow_name = flow_name
        if self.active_report and flow_name:
            self.repo.set_selected_flow(flow_name, self.active_report.name)

    def select_query(self, query_name: str) -> None:
        """Select active query and remember selection."""
        self.active_query_name = query_name
        if self.active_report and query_name:
            self.repo.set_selected_query(query_name, self.active_report.name)

    def get_query_info(self, query_name: str, ensure_parsed: bool = False) -> Optional[QueryInfo]:
        """Fetch QueryInfo for the currently active report."""
        if not self.active_report:
            return None
        return self.active_report.get_query(query_name, ensure_parsed=ensure_parsed)

    def _on_file_changed(self, file_path: Path) -> None:
        """Handle selective file modification detected by FileWatcherService."""
        resolved = file_path.resolve()
        suffix = resolved.suffix.lower()

        if suffix == ".sql":
            for report in self.reports_by_name.values():
                for q in report.queries:
                    if q.file_path and q.file_path.resolve() == resolved:
                        q.ensure_parsed(force=True)
                        self.repo.record_query_tables(report.name, q.name, q.input_tables, q.output_tables)
                        if self.active_report and self.active_report.name == report.name:
                            self.active_report_changed.emit(self.active_report)
                        return

        elif suffix == ".json":
            for report in self.reports_by_name.values():
                try:
                    if resolved.is_relative_to(report.folder_path.resolve()):
                        flows = self.scanner._scan_process_flows(report.folder_path, report.name)
                        report.process_flows = flows
                        if self.active_report and self.active_report.name == report.name:
                            self.process_flows_updated.emit([pf.name for pf in flows])
                            self.active_report_changed.emit(self.active_report)
                        return
                except Exception:
                    pass

    def open_query_in_editor(self, query_name: str) -> bool:
        """Open query .sql file using the default OS application."""
        qinfo = self.get_query_info(query_name)
        if not qinfo or not qinfo.file_path.exists():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(qinfo.file_path.resolve())))

    # --- Report / Process Flow / Query Management (Requirement 3) ---

    def add_report(self, report_name: str, directory_alias: Optional[str] = None) -> Optional[Report]:
        """Create a new report folder with queries subfolder and default flow."""
        clean_name = report_name.strip()
        if not clean_name:
            return None

        # Determine target working directory by alias
        working_dirs = self.get_working_directories()
        target_working_dir = None

        if directory_alias:
            for d in working_dirs:
                if d.get("alias") == directory_alias:
                    p = d.get("path")
                    if p and Path(p).exists():
                        target_working_dir = Path(p).resolve()
                        break

        if not target_working_dir and working_dirs:
            p = working_dirs[0].get("path")
            if p and Path(p).exists():
                target_working_dir = Path(p).resolve()

        if not target_working_dir:
            target_working_dir = Path.cwd().resolve()

        reports_sub = target_working_dir / "reports"
        if reports_sub.exists() and reports_sub.is_dir():
            target_dir = reports_sub
        elif target_working_dir.name == "reports":
            target_dir = target_working_dir
        else:
            reports_sub.mkdir(parents=True, exist_ok=True)
            target_dir = reports_sub

        rep_folder = target_dir / clean_name
        rep_folder.mkdir(parents=True, exist_ok=True)
        (rep_folder / "queries").mkdir(parents=True, exist_ok=True)
        (rep_folder / "outputs").mkdir(parents=True, exist_ok=True)
        (rep_folder / "inputs").mkdir(parents=True, exist_ok=True)

        self.scan()
        # Find the newly created report
        for k, rep in self.reports_by_key.items():
            if rep.folder_path == rep_folder:
                self.select_report(k)
                return self.active_report

        self.select_report(clean_name)
        return self.active_report

    def remove_report(self, report_key: str) -> bool:
        """Remove a report folder from disk."""
        rep = self.reports_by_key.get(report_key) or self.reports_by_name.get(report_key)
        if not rep or not rep.folder_path.exists():
            return False

        import shutil
        try:
            shutil.rmtree(rep.folder_path)
            self.scan()
            return True
        except Exception as e:
            logger.error(f"Failed to remove report folder {rep.folder_path}: {e}")
            return False

    def rename_report(self, report_key: str, new_name: str) -> bool:
        """Rename a report folder on disk and update selections."""
        clean_new_name = new_name.strip()
        if not clean_new_name:
            return False

        rep = self.reports_by_key.get(report_key) or self.reports_by_name.get(report_key)
        if not rep or not rep.folder_path.exists():
            return False

        if rep.name == clean_new_name:
            return True

        target_folder = rep.folder_path.parent / clean_new_name
        if target_folder.exists():
            logger.error(f"Target folder {target_folder} already exists.")
            return False

        try:
            rep.folder_path.rename(target_folder)
            self.repo.set_selected_report(clean_new_name)
            self.scan()
            for k, r in self.reports_by_key.items():
                if r.folder_path == target_folder or r.name == clean_new_name:
                    self.select_report(k)
                    break
            return True
        except Exception as e:
            logger.error(f"Failed to rename report: {e}")
            return False

    def add_process_flow(self, flow_name: str, source_flow_name: Optional[str] = None) -> Optional[ProcessFlowInfo]:
        """Create a new process flow in the active report, optionally cloning from an existing flow."""
        if not self.active_report:
            return None

        clean_name = flow_name.strip()
        if clean_name.endswith(".json"):
            clean_name = clean_name[:-5]
        if not clean_name:
            return None

        import json
        queries_dir = self.active_report.folder_path / "queries"
        queries_dir.mkdir(parents=True, exist_ok=True)
        flow_path = queries_dir / f"{clean_name}.json"

        if not flow_path.exists():
            data = {
                "flow_name": clean_name,
                "report_name": self.active_report.name,
                "query_names": [],
                "parameter_defaults": {},
                "show_full_table_names": False,
                "csv_filenames": {},
                "graph_session": {},
            }
            if source_flow_name:
                src_info = self.active_report.get_process_flow(source_flow_name)
                if src_info and src_info.file_path.exists():
                    try:
                        src_data = json.loads(src_info.file_path.read_text(encoding="utf-8"))
                        data.update(src_data)
                        data["flow_name"] = clean_name
                        data["report_name"] = self.active_report.name
                    except Exception as e:
                        logger.error(f"Failed to clone flow data from {source_flow_name}: {e}")
            flow_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        self.scan()
        self.select_process_flow(clean_name)
        return self.active_report.get_process_flow(clean_name)

    def remove_process_flow(self, flow_name: str) -> bool:
        """Remove a process flow file from disk."""
        if not self.active_report:
            return False

        flow_info = self.active_report.get_process_flow(flow_name)
        if not flow_info or not flow_info.file_path.exists():
            return False

        try:
            flow_info.file_path.unlink()
            self.scan()
            return True
        except Exception as e:
            logger.error(f"Failed to remove process flow {flow_name}: {e}")
            return False

    def rename_process_flow(self, old_flow_name: str, new_flow_name: str) -> bool:
        """Rename a process flow JSON file on disk and update internal flow_name."""
        if not self.active_report:
            return False

        clean_old = old_flow_name.strip()
        if clean_old.endswith(".json"):
            clean_old = clean_old[:-5]

        clean_new = new_flow_name.strip()
        if clean_new.endswith(".json"):
            clean_new = clean_new[:-5]

        if not clean_new:
            return False
        if clean_old == clean_new:
            return True

        flow_info = self.active_report.get_process_flow(clean_old)
        if not flow_info or not flow_info.file_path.exists():
            return False

        target_file = flow_info.file_path.parent / f"{clean_new}.json"
        if target_file.exists():
            logger.error(f"Target process flow file {target_file} already exists.")
            return False

        import json
        try:
            try:
                data = json.loads(flow_info.file_path.read_text(encoding="utf-8"))
                data["flow_name"] = clean_new
                flow_info.file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass

            flow_info.file_path.rename(target_file)
            self.scan()
            self.select_process_flow(clean_new)
            return True
        except Exception as e:
            logger.error(f"Failed to rename process flow: {e}")
            return False

    def add_query(self, query_name: str, template_sql: str = "") -> Optional[QueryInfo]:
        """Create a new query file in the active report."""
        if not self.active_report:
            return None

        # Clean name and ensure .sql extension
        base_name = query_name.strip()
        if base_name.lower().endswith(".sql"):
            base_name = base_name[:-4]
        elif base_name.lower().endswith(".csv"):
            base_name = base_name[:-4]
        base_name = base_name.strip()

        # Save in ./<report>/queries/
        queries_dir = self.active_report.folder_path / "queries"
        queries_dir.mkdir(parents=True, exist_ok=True)
        file_path = queries_dir / f"{base_name}.sql"
        if not file_path.exists():
            content = template_sql or f"-- Query: {base_name}\nSELECT 1;\n"
            file_path.write_text(content, encoding="utf-8")

        self.scan()
        return self.get_query_info(base_name)

    def remove_query(self, query_name: str) -> bool:
        """Delete query file from disk."""
        qinfo = self.get_query_info(query_name)
        if not qinfo or not qinfo.file_path.exists():
            return False
        try:
            qinfo.file_path.unlink()
            self.scan()
            return True
        except Exception as e:
            logger.error(f"Failed to remove query file: {e}")
            return False

    def rename_query(self, old_name: str, new_name: str) -> bool:
        """Rename query file."""
        qinfo = self.get_query_info(old_name)
        if not qinfo or not qinfo.file_path.exists():
            return False

        clean_new = new_name.strip()
        if clean_new.endswith(".sql"):
            clean_new = clean_new[:-4]

        target_file = qinfo.file_path.parent / f"{clean_new}.sql"
        if target_file.exists():
            return False

        try:
            qinfo.file_path.rename(target_file)
            self.scan()
            self.select_query(clean_new)
            return True
        except Exception as e:
            logger.error(f"Failed to rename query: {e}")
            return False

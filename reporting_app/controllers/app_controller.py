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

    def __init__(self, db_manager: Optional[DatabaseManager] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db_manager = db_manager or DatabaseManager()
        self.repo = Repository(self.db_manager)

        # Initialize working directory from database or default
        working_dir = self.repo.get_working_directory()
        self.scanner = FileScanner(working_dir)

        # File watcher
        self.watcher = FileWatcherService(check_interval_ms=5000, parent=self)
        self.watcher.set_target_directory(working_dir)
        self.watcher.set_enabled(self.repo.get_auto_scan())
        self.watcher.directory_changed.connect(self.scan)

        # Runtime state
        self.reports_by_name: Dict[str, Report] = {}
        self.active_report: Optional[Report] = None
        self.active_flow_name: Optional[str] = None
        self.active_query_name: Optional[str] = None

    def initialize(self) -> None:
        """Initial scan and setup."""
        self.scan()

    def get_working_directory(self) -> Path:
        return self.scanner.working_directory

    def set_working_directory(self, path: Path) -> None:
        """Update working directory, save to settings, and rescan."""
        resolved = path.resolve()
        self.repo.set_working_directory(resolved)
        self.scanner.set_working_directory(resolved)
        self.watcher.set_target_directory(resolved)
        self.scan()

    def get_auto_scan(self) -> bool:
        return self.repo.get_auto_scan()

    def set_auto_scan(self, enabled: bool) -> None:
        """Update auto-scan preference."""
        self.repo.set_auto_scan(enabled)
        self.watcher.set_enabled(enabled)

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
                "show_full_table_names": True,
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
        """Trigger scanning of reports and queries."""
        self.status_changed.emit("Scanning reports directory...")
        reports = self.scanner.scan_all_reports()
        self.reports_by_name = {r.name: r for r in reports}

        # Update SQLite table catalog for all queries
        for r in reports:
            for q in r.queries:
                self.repo.record_query_tables(r.name, q.name, q.input_tables, q.output_tables)

        report_names = list(self.reports_by_name.keys())
        self.reports_updated.emit(report_names)

        # Requirement 1: Restore persisted report selection
        saved_report = self.repo.get_selected_report()

        if self.active_report and self.active_report.name in self.reports_by_name:
            self.select_report(self.active_report.name, preserve_flow=True)
        elif saved_report and saved_report in self.reports_by_name:
            self.select_report(saved_report)
        elif report_names:
            self.select_report(report_names[0])
        else:
            self.active_report = None
            self.active_report_changed.emit(None)
            self.process_flows_updated.emit([])
            self.queries_updated.emit([])

        self.status_changed.emit(f"Ready. Found {len(report_names)} report(s).")

    def select_report(self, report_name: str, preserve_flow: bool = False) -> None:
        """Select active report and populate flows and queries."""
        report = self.reports_by_name.get(report_name)
        self.active_report = report
        self.active_report_changed.emit(report)

        if not report:
            self.process_flows_updated.emit([])
            self.queries_updated.emit([])
            return

        # Requirement 1: Persist selected report
        self.repo.set_selected_report(report_name)

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

    def get_query_info(self, query_name: str) -> Optional[QueryInfo]:
        """Fetch QueryInfo for the currently active report."""
        if not self.active_report:
            return None
        return self.active_report.get_query(query_name)

    def open_query_in_editor(self, query_name: str) -> bool:
        """Open query .sql file using the default OS application."""
        qinfo = self.get_query_info(query_name)
        if not qinfo or not qinfo.file_path.exists():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(qinfo.file_path.resolve())))

    # --- Report / Process Flow / Query Management (Requirement 3) ---

    def add_report(self, report_name: str) -> Optional[Report]:
        """Create a new report folder with queries subfolder and default flow."""
        clean_name = report_name.strip()
        if not clean_name:
            return None

        # Determine target directory
        working_dir = self.get_working_directory()
        reports_sub = working_dir / "reports"
        target_dir = reports_sub if (reports_sub.exists() and reports_sub.is_dir()) else working_dir
        rep_folder = target_dir / clean_name
        rep_folder.mkdir(parents=True, exist_ok=True)
        (rep_folder / "queries").mkdir(parents=True, exist_ok=True)

        self.scan()
        self.select_report(clean_name)
        return self.active_report

    def remove_report(self, report_name: str) -> bool:
        """Remove a report folder from disk."""
        rep = self.reports_by_name.get(report_name)
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

    def add_process_flow(self, flow_name: str) -> Optional[ProcessFlowInfo]:
        """Create a new process flow in the active report."""
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
                "show_full_table_names": True,
                "csv_filenames": {},
                "graph_session": {},
            }
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

    def add_query(self, query_name: str, template_sql: str = "") -> Optional[QueryInfo]:
        """Create a new query file in the active report."""
        if not self.active_report:
            return None

        # Clean name and ensure .sql extension
        base_name = query_name.strip()
        if base_name.endswith(".sql"):
            base_name = base_name[:-4]

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
            return True
        except Exception as e:
            logger.error(f"Failed to rename query: {e}")
            return False

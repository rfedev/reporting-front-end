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
    process_flows_updated = Signal(list)  # list of flow names (including "All Queries")
    queries_updated = Signal(list)  # list of query names
    status_changed = Signal(str)  # status text

    ALL_QUERIES_OPTION = "All Queries"

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
        self.active_flow_name: str = self.ALL_QUERIES_OPTION
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

        # Re-select active report if possible
        if self.active_report and self.active_report.name in self.reports_by_name:
            self.select_report(self.active_report.name, preserve_flow=True)
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

        # Build process flows list with "All Queries" as default first entry
        flow_options = [self.ALL_QUERIES_OPTION]
        flow_options.extend([f.name for f in report.process_flows])
        self.process_flows_updated.emit(flow_options)

        target_flow = self.active_flow_name if (preserve_flow and self.active_flow_name in flow_options) else self.ALL_QUERIES_OPTION
        self.select_process_flow(target_flow)

    def select_process_flow(self, flow_name: str) -> None:
        """Select active process flow and filter queries dropdown accordingly."""
        self.active_flow_name = flow_name

        if not self.active_report:
            self.queries_updated.emit([])
            return

        if flow_name == self.ALL_QUERIES_OPTION or not flow_name:
            # Show all queries
            query_names = [q.name for q in self.active_report.queries]
        else:
            # Show only queries used in this process flow
            flow = self.active_report.get_process_flow(flow_name)
            if flow and flow.query_names:
                query_names = [name for name in flow.query_names if self.active_report.get_query(name)]
            else:
                # If flow has no queries yet or empty, show none
                query_names = []

        self.queries_updated.emit(query_names)
        if query_names:
            self.active_query_name = query_names[0]
        else:
            self.active_query_name = None

    def select_query(self, query_name: str) -> None:
        self.active_query_name = query_name

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

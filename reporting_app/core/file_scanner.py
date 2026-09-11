"""Directory scanner for reports, queries, and process flows.

Features:
- Discovers reports (top-level folders in the working directory)
- Discovers queries in ./<report>/queries/*.sql (and fallback in ./code/ or root)
- Discovers process flows in ./<report>/queries/*.json (and fallback in root)
- Caches parsed metadata by file modification time (mtime)
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from reporting_app.core.models import (
    ProcessFlowInfo,
    QueryInfo,
    QueryParameter,
    Report,
)
from reporting_app.core.sql_parser import (
    scan_query_parameters,
    scan_query_tables,
    sync_query_csv_comments,
)

logger = logging.getLogger(__name__)

# Standard non-report directories to ignore if working directory is set to a project root
IGNORED_DIRS: Set[str] = {
    ".git",
    ".venv",
    ".idea",
    ".vscode",
    "__pycache__",
    "reporting_app",
    "tests",
    "dist",
    "build",
    "node_modules",
}


class FileScanner:
    """Scans and caches report assets within the working directory."""

    def __init__(self, working_directory: Optional[Path] = None):
        self.working_directory: Path = working_directory or Path.cwd()
        self._query_cache: Dict[str, QueryInfo] = {}
        self._flow_cache: Dict[str, ProcessFlowInfo] = {}

    def set_working_directory(self, path: Path) -> None:
        """Update the working directory and clear outdated caches."""
        self.working_directory = path.resolve()
        self._query_cache.clear()
        self._flow_cache.clear()

    def _is_valid_report_dir(self, directory: Path) -> bool:
        """Check if a directory looks like a valid report."""
        if directory.name in IGNORED_DIRS or directory.name.startswith((".", "_")):
            return False

        # If inside a 'reports' directory, any non-ignored directory is a report
        if directory.parent.name == "reports":
            return True

        # Check queries/ directory
        queries_dir = directory / "queries"
        if queries_dir.is_dir():
            return True

        # Check for sql files in root or code/
        has_sql = any(directory.glob("*.sql"))
        if not has_sql:
            code_dir = directory / "code"
            if code_dir.is_dir():
                has_sql = any(code_dir.glob("*.sql"))

        has_json = any(directory.glob("*.json"))
        try:
            is_empty = not any(directory.iterdir())
        except Exception:
            is_empty = False

        return has_sql or has_json or is_empty

    def scan_all_reports(self) -> List[Report]:
        """Scan the working directory and return all discovered reports."""
        if not self.working_directory.exists() or not self.working_directory.is_dir():
            logger.warning(f"Working directory does not exist: {self.working_directory}")
            return []

        # If working_directory has a 'reports' subdirectory, scan inside 'reports/'
        reports_sub = self.working_directory / "reports"
        target_dir = reports_sub if reports_sub.exists() and reports_sub.is_dir() else self.working_directory

        reports: List[Report] = []
        for entry in sorted(target_dir.iterdir()):
            if entry.is_dir() and self._is_valid_report_dir(entry):
                reports.append(self.scan_report(entry))

        return reports

    def scan_report(self, report_dir: Path) -> Report:
        """Scan an individual report directory for queries and process flows."""
        report_name = report_dir.name
        queries = self._scan_queries(report_dir, report_name)
        process_flows = self._scan_process_flows(report_dir, report_name)

        return Report(
            name=report_name,
            folder_path=report_dir,
            queries=queries,
            process_flows=process_flows,
        )

    def _scan_queries(self, report_dir: Path, report_name: str) -> List[QueryInfo]:
        """Scan SQL files in ./queries/*.sql, ./code/*.sql, and root/*.sql."""
        sql_files: List[Path] = []

        # Primary location: ./<report>/queries/*.sql
        queries_dir = report_dir / "queries"
        if queries_dir.exists() and queries_dir.is_dir():
            sql_files.extend(queries_dir.glob("*.sql"))

        # Fallback locations
        code_dir = report_dir / "code"
        if code_dir.exists() and code_dir.is_dir():
            for p in code_dir.glob("*.sql"):
                if p not in sql_files:
                    sql_files.append(p)

        for p in report_dir.glob("*.sql"):
            if p not in sql_files:
                sql_files.append(p)

        # Collect existing CSV names in report outputs directory
        existing_report_csvs: Set[str] = set()
        outputs_dir = report_dir / "outputs"
        if outputs_dir.exists() and outputs_dir.is_dir():
            for cf in outputs_dir.glob("*.csv"):
                existing_report_csvs.add(cf.name)

        queries: List[QueryInfo] = []
        for file_path in sorted(sql_files, key=lambda x: x.name):
            try:
                # Sync # ouput: comments for standalone SELECT statements
                csv_files = sync_query_csv_comments(file_path, existing_report_csvs)
                existing_report_csvs.update(csv_files)

                mtime = file_path.stat().st_mtime
                cache_key = str(file_path.resolve())

                # Check if cache is still valid
                cached = self._query_cache.get(cache_key)
                if cached and cached.mtime == mtime:
                    queries.append(cached)
                    continue

                # Parse file content
                content = file_path.read_text(encoding="utf-8", errors="replace")
                param_names = scan_query_parameters(content)
                input_tables, output_tables, output_csv_tables = scan_query_tables(content)
                if csv_files:
                    output_csv_tables = csv_files

                query_info = QueryInfo(
                    name=file_path.stem,
                    file_path=file_path,
                    report_name=report_name,
                    parameters=[QueryParameter(name=p) for p in param_names],
                    input_tables=input_tables,
                    output_tables=output_tables,
                    output_csv_tables=output_csv_tables,
                    mtime=mtime,
                )
                self._query_cache[cache_key] = query_info
                queries.append(query_info)
            except Exception as e:
                logger.error(f"Failed to scan query file {file_path}: {e}")

        return queries

    def _scan_process_flows(self, report_dir: Path, report_name: str) -> List[ProcessFlowInfo]:
        """Scan JSON files representing process flows in ./queries/*.json and root/*.json."""
        json_files: List[Path] = []

        # Primary location: ./<report>/queries/*.json
        queries_dir = report_dir / "queries"
        if queries_dir.exists() and queries_dir.is_dir():
            json_files.extend(queries_dir.glob("*.json"))

        # Fallback in report root
        for p in report_dir.glob("*.json"):
            if p not in json_files:
                json_files.append(p)

        flows: List[ProcessFlowInfo] = []
        for file_path in sorted(json_files, key=lambda x: x.name):
            try:
                mtime = file_path.stat().st_mtime
                cache_key = str(file_path.resolve())

                cached = self._flow_cache.get(cache_key)
                if cached and cached.mtime == mtime:
                    flows.append(cached)
                    continue

                raw_text = file_path.read_text(encoding="utf-8", errors="replace").strip()
                data = json.loads(raw_text) if raw_text else {}

                query_names = data.get("query_names", [])
                parameter_defaults = data.get("parameter_defaults", {})

                # If query_names not in metadata, inspect node names if available
                if not query_names and "nodes" in data:
                    for node_data in data["nodes"].values():
                        if isinstance(node_data, dict) and "name" in node_data:
                            name = node_data["name"]
                            # Only include if it's a query node
                            if not name.startswith("Table:") and not name.endswith("[In]") and not name.endswith("[Out]") and name not in query_names:
                                query_names.append(name)

                flow_info = ProcessFlowInfo(
                    name=file_path.stem,
                    file_path=file_path,
                    report_name=report_name,
                    query_names=query_names,
                    parameter_defaults=parameter_defaults,
                    raw_data=data,
                    mtime=mtime,
                )
                self._flow_cache[cache_key] = flow_info
                flows.append(flow_info)
            except Exception as e:
                logger.error(f"Failed to scan process flow file {file_path}: {e}")

        return flows

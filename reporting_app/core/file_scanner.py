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
import re
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

    def __init__(self, working_directories: Optional[List[Dict[str, str]]] = None):
        self.working_directories: List[Dict[str, str]] = working_directories or [{"alias": "Default", "path": str(Path.cwd().resolve())}]
        self._query_cache: Dict[str, QueryInfo] = {}
        self._flow_cache: Dict[str, ProcessFlowInfo] = {}

    def set_working_directories(self, directories: List[Dict[str, str]]) -> None:
        """Update the working directories list and clear outdated caches."""
        self.working_directories = directories
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
        """Scan all configured working directories and return all discovered reports."""
        all_reports: List[Report] = []

        for entry in self.working_directories:
            alias = entry.get("alias", "Default")
            p_str = entry.get("path", "")
            if not p_str:
                continue
            base_path = Path(p_str).resolve()
            if not base_path.exists() or not base_path.is_dir():
                logger.warning(f"Working directory [{alias}] does not exist: {base_path}")
                continue

            # If base_path has a 'reports' subdirectory, scan inside 'reports/'
            reports_sub = base_path / "reports"
            target_dir = reports_sub if reports_sub.exists() and reports_sub.is_dir() else base_path

            for item in sorted(target_dir.iterdir()):
                if item.is_dir() and self._is_valid_report_dir(item):
                    rep = self.scan_report(item, directory_alias=alias, working_directory=base_path)
                    all_reports.append(rep)

        return all_reports

    def scan_report(
        self,
        report_dir: Path,
        directory_alias: str = "",
        working_directory: Optional[Path] = None,
    ) -> Report:
        """Scan an individual report directory for queries and process flows."""
        report_name = report_dir.name
        queries = self._scan_queries(report_dir, report_name)
        process_flows = self._scan_process_flows(report_dir, report_name)

        return Report(
            name=report_name,
            folder_path=report_dir,
            queries=queries,
            process_flows=process_flows,
            directory_alias=directory_alias,
            working_directory=working_directory,
        )

    def parse_query(self, qinfo: QueryInfo, force: bool = False) -> QueryInfo:
        """Parse a query on-demand and update internal cache."""
        res = parse_single_query(qinfo, force=force)
        cache_key = str(qinfo.file_path.resolve())
        self._query_cache[cache_key] = res
        return res

    def _scan_queries(self, report_dir: Path, report_name: str) -> List[QueryInfo]:
        """Discover SQL files in ./queries/*.sql, ./code/*.sql, and root/*.sql without reading or parsing file content."""
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

        queries: List[QueryInfo] = []
        for file_path in sorted(sql_files, key=lambda x: x.name):
            try:
                cache_key = str(file_path.resolve())
                mtime = file_path.stat().st_mtime

                # Check if cache is still valid
                cached = self._query_cache.get(cache_key)
                if cached:
                    queries.append(cached)
                    continue

                query_info = QueryInfo(
                    name=file_path.stem,
                    file_path=file_path,
                    report_name=report_name,
                    mtime=mtime,
                    is_parsed=False,
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


def parse_single_query(qinfo: QueryInfo, force: bool = False) -> QueryInfo:
    """Parse query parameters, input/output tables, and sync CSV comments for a single query."""
    if not qinfo.file_path or not qinfo.file_path.exists():
        qinfo.is_parsed = True
        return qinfo

    try:
        mtime = qinfo.file_path.stat().st_mtime
    except Exception:
        mtime = 0.0

    if qinfo.is_parsed and not force and getattr(qinfo, "mtime", 0.0) == mtime:
        return qinfo

    # Collect existing CSV names in report outputs directory
    existing_report_csvs: Set[str] = set()
    report_dir = qinfo.file_path.parent
    if report_dir.name in ("queries", "code"):
        report_dir = report_dir.parent
    outputs_dir = report_dir / "outputs"
    if outputs_dir.exists() and outputs_dir.is_dir():
        for cf in outputs_dir.glob("*.csv"):
            existing_report_csvs.add(cf.name)

    # Collect existing output comments and reserve table_NN.csv for alphabetically earlier SQL files
    all_sql_files = sorted(report_dir.glob("**/*.sql"), key=lambda p: p.name)
    uncommented_earlier_files = []
    for sql_file in all_sql_files:
        if sql_file == qinfo.file_path:
            continue
        try:
            txt = sql_file.read_text(encoding="utf-8", errors="replace")
            has_comments = False
            for m in re.finditer(r"(?:#|--)\s*out?put\s*:\s*([^\r\n;]+)", txt, re.IGNORECASE):
                has_comments = True
                for part in m.group(1).split(","):
                    p = part.strip()
                    if p:
                        if not p.lower().endswith(".csv"):
                            p = f"{p}.csv"
                        existing_report_csvs.add(p)
            if not has_comments and sql_file.name < qinfo.file_path.name:
                from reporting_app.core.sql_parser import find_standalone_select_statements
                stmts = find_standalone_select_statements(txt)
                if stmts:
                    uncommented_earlier_files.extend([sql_file] * len(stmts))
        except Exception:
            pass

    for _ in uncommented_earlier_files:
        num = 1
        while f"table_{num:02d}.csv" in existing_report_csvs:
            num += 1
        existing_report_csvs.add(f"table_{num:02d}.csv")

    try:
        # Sync # output: comments for standalone SELECT statements
        csv_files = sync_query_csv_comments(qinfo.file_path, existing_report_csvs)
        mtime = qinfo.file_path.stat().st_mtime

        # Parse file content
        content = qinfo.file_path.read_text(encoding="utf-8", errors="replace")
        param_names = scan_query_parameters(content)
        input_tables, output_tables, output_csv_tables = scan_query_tables(content)
        if csv_files:
            output_csv_tables = csv_files

        qinfo.parameters = [QueryParameter(name=p) for p in param_names]
        qinfo.input_tables = input_tables
        qinfo.output_tables = output_tables
        qinfo.output_csv_tables = output_csv_tables
        qinfo.mtime = mtime
        qinfo.is_parsed = True
    except Exception as e:
        logger.error(f"Failed to parse query file {qinfo.file_path}: {e}")
        qinfo.is_parsed = True

    return qinfo


"""Repository pattern for database operations."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy import delete, select
from reporting_app.persistence.database import DatabaseManager
from reporting_app.persistence.db_models import (
    AppSetting,
    CatalogTable,
    ExecutionLog,
    ParameterDefault,
)


class Repository:
    """Provides high-level data access methods using SQLAlchemy."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.db_manager.initialize_schema()

    # --- Settings ---

    def get_setting(self, key: str, default: str = "") -> str:
        with self.db_manager.get_session() as session:
            stmt = select(AppSetting).where(AppSetting.key == key)
            setting = session.execute(stmt).scalar_one_or_none()
            return setting.value if setting else default

    def set_setting(self, key: str, value: str) -> None:
        with self.db_manager.get_session() as session:
            with session.begin():
                stmt = select(AppSetting).where(AppSetting.key == key)
                setting = session.execute(stmt).scalar_one_or_none()
                if setting:
                    setting.value = value
                else:
                    session.add(AppSetting(key=key, value=value))

    def get_working_directories(self) -> List[Dict[str, str]]:
        """Return list of configured working directories: [{'alias': '...', 'path': '...'}]"""
        import json
        val = self.get_setting("working_directories", "")
        if val:
            try:
                dirs = json.loads(val)
                if isinstance(dirs, list):
                    return dirs
            except Exception:
                pass
        return [{"alias": "Default", "path": str(Path.cwd().resolve())}]

    def set_working_directories(self, directories: List[Dict[str, str]]) -> None:
        """Store list of configured working directories."""
        import json
        self.set_setting("working_directories", json.dumps(directories))

    def get_auto_scan(self) -> bool:
        val = self.get_setting("auto_scan", "true").lower()
        return val in ("true", "1", "yes")

    def set_auto_scan(self, enabled: bool) -> None:
        self.set_setting("auto_scan", "true" if enabled else "false")

    # --- Workbench Dataset (Requirement 4) ---

    def get_workbench_dataset(self) -> str:
        """Return the default workbench dataset address."""
        return self.get_setting("workbench_dataset", "")

    def set_workbench_dataset(self, value: str) -> None:
        """Store the default workbench dataset address."""
        self.set_setting("workbench_dataset", value.strip())

    # --- Dropdown Selections Persistence (Requirement 1) ---

    def get_selected_report(self) -> str:
        return self.get_setting("selected_report", "")

    def set_selected_report(self, report_name: str) -> None:
        self.set_setting("selected_report", report_name)

    def get_selected_flow(self, report_name: Optional[str] = None) -> str:
        if report_name:
            val = self.get_setting(f"selected_flow_{report_name}", "")
            if val:
                return val
        return self.get_setting("selected_flow", "")

    def set_selected_flow(self, flow_name: str, report_name: Optional[str] = None) -> None:
        self.set_setting("selected_flow", flow_name)
        if report_name:
            self.set_setting(f"selected_flow_{report_name}", flow_name)

    def get_selected_query(self, report_name: Optional[str] = None) -> str:
        if report_name:
            val = self.get_setting(f"selected_query_{report_name}", "")
            if val:
                return val
        return self.get_setting("selected_query", "")

    def set_selected_query(self, query_name: str, report_name: Optional[str] = None) -> None:
        self.set_setting("selected_query", query_name)
        if report_name:
            self.set_setting(f"selected_query_{report_name}", query_name)

    # --- Parameter Defaults ---

    def get_parameter_default(self, scope_type: str, scope_name: str, param_name: str) -> str:
        with self.db_manager.get_session() as session:
            stmt = select(ParameterDefault).where(
                ParameterDefault.scope_type == scope_type,
                ParameterDefault.scope_name == scope_name,
                ParameterDefault.param_name == param_name,
            )
            item = session.execute(stmt).scalar_one_or_none()
            return item.default_value if item else ""

    def set_parameter_default(
        self, scope_type: str, scope_name: str, param_name: str, default_value: str
    ) -> None:
        with self.db_manager.get_session() as session:
            with session.begin():
                stmt = select(ParameterDefault).where(
                    ParameterDefault.scope_type == scope_type,
                    ParameterDefault.scope_name == scope_name,
                    ParameterDefault.param_name == param_name,
                )
                item = session.execute(stmt).scalar_one_or_none()
                if item:
                    item.default_value = default_value
                else:
                    session.add(
                        ParameterDefault(
                            scope_type=scope_type,
                            scope_name=scope_name,
                            param_name=param_name,
                            default_value=default_value,
                        )
                    )

    def get_all_defaults_for_scope(self, scope_type: str, scope_name: str) -> Dict[str, str]:
        with self.db_manager.get_session() as session:
            stmt = select(ParameterDefault).where(
                ParameterDefault.scope_type == scope_type,
                ParameterDefault.scope_name == scope_name,
            )
            items = session.execute(stmt).scalars().all()
            return {item.param_name: item.default_value for item in items}

    # --- Tables Catalog ---

    def record_query_tables(
        self,
        report_name: str,
        query_name: str,
        input_tables: List[str],
        output_tables: List[str],
    ) -> None:
        """Update tables catalog entries for a specific query."""
        with self.db_manager.get_session() as session:
            with session.begin():
                # Delete existing entries for this query
                session.execute(
                    delete(CatalogTable).where(
                        CatalogTable.report_name == report_name,
                        CatalogTable.query_name == query_name,
                    )
                )
                for tbl in input_tables:
                    session.add(
                        CatalogTable(
                            table_name=tbl,
                            report_name=report_name,
                            query_name=query_name,
                            table_type="input",
                        )
                    )
                for tbl in output_tables:
                    session.add(
                        CatalogTable(
                            table_name=tbl,
                            report_name=report_name,
                            query_name=query_name,
                            table_type="output",
                        )
                    )

    def get_all_tables(self) -> List[Dict[str, str]]:
        with self.db_manager.get_session() as session:
            stmt = select(CatalogTable)
            items = session.execute(stmt).scalars().all()
            return [
                {
                    "table_name": item.table_name,
                    "report_name": item.report_name,
                    "query_name": item.query_name,
                    "table_type": item.table_type,
                }
                for item in items
            ]

    # --- Execution Logs ---

    def record_execution_log(self, log_data: Dict[str, Any]) -> None:
        """Insert a single node execution log record."""
        import json
        with self.db_manager.get_session() as session:
            with session.begin():
                export_json = log_data.get("export_details_json")
                if isinstance(export_json, (list, dict)):
                    export_json = json.dumps(export_json)
                import_json = log_data.get("import_details_json")
                if isinstance(import_json, (list, dict)):
                    import_json = json.dumps(import_json)

                entry = ExecutionLog(
                    run_id=log_data.get("run_id", ""),
                    flow_start_time=log_data.get("flow_start_time", ""),
                    node_start_time=log_data.get("node_start_time", ""),
                    node_end_time=log_data.get("node_end_time", ""),
                    duration_seconds=float(log_data.get("duration_seconds", 0.0)),
                    report_name=log_data.get("report_name", ""),
                    flow_name=log_data.get("flow_name", ""),
                    node_type=log_data.get("node_type", "query"),
                    node_name=log_data.get("node_name", ""),
                    status=log_data.get("status", "SUCCESS"),
                    error_message=log_data.get("error_message"),
                    submitted_query=log_data.get("submitted_query"),
                    output_rows=log_data.get("output_rows"),
                    total_bytes_processed=log_data.get("total_bytes_processed"),
                    total_bytes_billed=log_data.get("total_bytes_billed"),
                    slot_millis=log_data.get("slot_millis"),
                    cache_hit=1 if log_data.get("cache_hit") else (0 if log_data.get("cache_hit") is False else None),
                    export_details_json=export_json or "[]",
                    import_details_json=import_json or "[]",
                )
                session.add(entry)

    def get_distinct_log_dates(
        self,
        report_name: Optional[str] = None,
        flow_name: Optional[str] = None,
        node_name: Optional[str] = None,
    ) -> List[str]:
        """Return distinct dates (YYYY-MM-DD) from flow_start_time in descending order."""
        with self.db_manager.get_session() as session:
            stmt = select(ExecutionLog.flow_start_time)
            if report_name:
                stmt = stmt.where(ExecutionLog.report_name == report_name)
            if flow_name and flow_name != "All Flows":
                stmt = stmt.where(ExecutionLog.flow_name == flow_name)
            if node_name and node_name != "All Nodes":
                stmt = stmt.where(ExecutionLog.node_name == node_name)

            timestamps = session.execute(stmt).scalars().all()
            dates = set()
            for ts in timestamps:
                if ts and len(ts) >= 10:
                    dates.add(ts[:10])
            return sorted(list(dates), reverse=True)

    def get_runs(
        self,
        date_str: Optional[str] = None,
        run_id: Optional[str] = None,
        report_name: Optional[str] = None,
        flow_name: Optional[str] = None,
        node_name: Optional[str] = None,
    ) -> List[ExecutionLog]:
        """Fetch execution logs filtered by date, run_id, report, flow, or node."""
        with self.db_manager.get_session() as session:
            stmt = select(ExecutionLog)
            if date_str:
                stmt = stmt.where(ExecutionLog.flow_start_time.startswith(date_str))
            if run_id:
                stmt = stmt.where(ExecutionLog.run_id == run_id)
            if report_name:
                stmt = stmt.where(ExecutionLog.report_name == report_name)
            if flow_name and flow_name != "All Flows":
                stmt = stmt.where(ExecutionLog.flow_name == flow_name)
            if node_name and node_name != "All Nodes":
                stmt = stmt.where(ExecutionLog.node_name == node_name)

            stmt = stmt.order_by(ExecutionLog.node_start_time.asc())
            return list(session.execute(stmt).scalars().all())

    def get_run_sessions(
        self,
        date_str: Optional[str] = None,
        report_name: Optional[str] = None,
        flow_name: Optional[str] = None,
        node_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return aggregated run sessions (run_id, flow_start_time, overall_status, node_count)."""
        runs = self.get_runs(
            date_str=date_str,
            report_name=report_name,
            flow_name=flow_name,
            node_name=node_name,
        )
        sessions_dict: Dict[str, Dict[str, Any]] = {}
        for r in runs:
            rid = r.run_id
            if rid not in sessions_dict:
                start_ts = r.flow_start_time or ""
                d_str = start_ts[:10] if len(start_ts) >= 10 else "Unknown Date"
                sessions_dict[rid] = {
                    "run_id": rid,
                    "date": d_str,
                    "flow_start_time": start_ts,
                    "flow_name": r.flow_name,
                    "status": "SUCCESS",
                    "nodes": [],
                }
            sessions_dict[rid]["nodes"].append(r)
            if r.status != "SUCCESS":
                sessions_dict[rid]["status"] = "FAILED"

        # Sort sessions descending by flow_start_time
        return sorted(list(sessions_dict.values()), key=lambda s: s["flow_start_time"], reverse=True)

    def prune_logs(self, older_than_days: int) -> int:
        """Prune logs older than N days. Returns count of deleted logs."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self.db_manager.get_session() as session:
            with session.begin():
                stmt = delete(ExecutionLog).where(ExecutionLog.flow_start_time < cutoff)
                result = session.execute(stmt)
                return result.rowcount

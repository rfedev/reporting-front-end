"""Repository pattern for database operations."""

from pathlib import Path
from typing import Dict, List, Optional
from sqlalchemy import delete, select
from reporting_app.persistence.database import DatabaseManager
from reporting_app.persistence.db_models import (
    AppSetting,
    CatalogTable,
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

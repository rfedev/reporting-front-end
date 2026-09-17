"""SQLAlchemy ORM models for application persistence."""

from sqlalchemy import Column, Float, Integer, String, Text, UniqueConstraint
from reporting_app.persistence.database import Base, LogBase


class AppSetting(Base):
    """Key-value application settings (e.g. working_directory, auto_scan)."""

    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False)


class ParameterDefault(Base):
    """Stored default values for query parameters at query or process flow scope."""

    __tablename__ = "parameter_defaults"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_type = Column(String(32), nullable=False)  # "query" or "flow"
    scope_name = Column(String(256), nullable=False)  # query_name or flow_name
    param_name = Column(String(128), nullable=False)
    default_value = Column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("scope_type", "scope_name", "param_name", name="uq_scope_param"),
    )


class CatalogTable(Base):
    """Catalog of all tables used across queries in the application."""

    __tablename__ = "catalog_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(256), nullable=False, index=True)
    report_name = Column(String(128), nullable=False, index=True)
    query_name = Column(String(128), nullable=False, index=True)
    table_type = Column(String(32), nullable=False)  # "input" or "output"


class CachedQueryMeta(Base):
    """Cached query metadata for quick retrieval."""

    __tablename__ = "cached_query_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_name = Column(String(128), nullable=False)
    query_name = Column(String(128), nullable=False)
    file_path = Column(Text, nullable=False)
    mtime = Column(Float, nullable=False)
    parameters_json = Column(Text, nullable=False, default="[]")
    input_tables_json = Column(Text, nullable=False, default="[]")
    output_tables_json = Column(Text, nullable=False, default="[]")

    __table_args__ = (
        UniqueConstraint("report_name", "query_name", name="uq_report_query"),
    )


class ExecutionLog(LogBase):
    """Log record for query and file import executions."""

    __tablename__ = "execution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    flow_start_time = Column(String(32), nullable=False, index=True)  # ISO timestamp
    node_start_time = Column(String(32), nullable=False)  # ISO timestamp
    node_end_time = Column(String(32), nullable=False)  # ISO timestamp
    duration_seconds = Column(Float, nullable=False, default=0.0)
    report_name = Column(String(128), nullable=False, index=True)
    flow_name = Column(String(128), nullable=False, index=True)
    node_type = Column(String(32), nullable=False)  # "query" or "import_csv"
    node_name = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False)  # "SUCCESS" or "FAILED"
    error_message = Column(Text, nullable=True)
    submitted_query = Column(Text, nullable=True)
    output_rows = Column(Integer, nullable=True)
    total_bytes_processed = Column(Integer, nullable=True)
    total_bytes_billed = Column(Integer, nullable=True)
    slot_millis = Column(Integer, nullable=True)
    cache_hit = Column(Integer, nullable=True)  # 0 or 1
    export_details_json = Column(Text, nullable=True, default="[]")
    import_details_json = Column(Text, nullable=True, default="[]")

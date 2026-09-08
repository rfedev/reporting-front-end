"""Domain models for reports, queries, parameters, and process flows."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class QueryParameter:
    """Represents a named parameter extracted from a query."""
    name: str
    default_value: str = ""


@dataclass
class TableInfo:
    """Information about a database table referenced in a query."""
    name: str
    kind: str  # "input" or "output"


@dataclass
class QueryInfo:
    """Metadata for a SQL query file."""
    name: str
    file_path: Path
    report_name: str
    parameters: List[QueryParameter] = field(default_factory=list)
    input_tables: List[str] = field(default_factory=list)
    output_tables: List[str] = field(default_factory=list)
    output_csv_tables: List[str] = field(default_factory=list)
    mtime: float = 0.0

    @property
    def filename(self) -> str:
        return self.file_path.name

    @property
    def parameter_names(self) -> List[str]:
        return [p.name for p in self.parameters]

    @property
    def has_output_csv(self) -> bool:
        return len(self.output_csv_tables) > 0


@dataclass
class ProcessFlowInfo:
    """Metadata and definition of a process flow."""
    name: str
    file_path: Path
    report_name: str
    query_names: List[str] = field(default_factory=list)
    parameter_defaults: Dict[str, str] = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)
    mtime: float = 0.0


@dataclass
class Report:
    """Represents a report directory containing queries and process flows."""
    name: str
    folder_path: Path
    queries: List[QueryInfo] = field(default_factory=list)
    process_flows: List[ProcessFlowInfo] = field(default_factory=list)

    def get_query(self, query_name: str) -> Optional[QueryInfo]:
        """Find a query by base name or filename."""
        clean_name = query_name[:-4] if query_name.endswith(".sql") else query_name
        for q in self.queries:
            if q.name == clean_name or q.filename == query_name:
                return q
        return None

    def get_process_flow(self, flow_name: str) -> Optional[ProcessFlowInfo]:
        """Find a process flow by name."""
        for pf in self.process_flows:
            if pf.name == flow_name:
                return pf
        return None

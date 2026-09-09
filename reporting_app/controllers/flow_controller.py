"""Controller for Process Flow Editor operations."""

from collections import defaultdict, deque
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from PySide6.QtCore import QObject, Signal

from reporting_app.core.models import ProcessFlowInfo, QueryInfo, Report
from reporting_app.persistence.flow_storage import FlowStorage
from reporting_app.persistence.repository import Repository

logger = logging.getLogger(__name__)


class ProcessFlowController(QObject):
    """Manages process flow graph state, node persistence, run ordering, and parameter consolidation."""

    flow_saved = Signal(str)  # flow name

    def __init__(
        self,
        report: Report,
        flow_info: Optional[ProcessFlowInfo] = None,
        flow_name: Optional[str] = None,
        repo: Optional[Repository] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.report = report
        self.repo = repo
        self.flow_name = flow_name or (flow_info.name if flow_info else "New-Process-Flow")

        if flow_info:
            self.file_path = flow_info.file_path
        else:
            # Check queries/ directory first
            queries_path = report.folder_path / "queries" / f"{self.flow_name}.json"
            root_path = report.folder_path / f"{self.flow_name}.json"
            if root_path.exists() and not queries_path.exists():
                self.file_path = root_path
            else:
                self.file_path = queries_path

        self.flow_data: Dict[str, Any] = {}
        self.parameter_defaults: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load JSON data if existing."""
        if self.file_path.exists():
            self.flow_data = FlowStorage.load(self.file_path)
            self.parameter_defaults = self.flow_data.get("parameter_defaults", {})
        else:
            self.flow_data = {
                "flow_name": self.flow_name,
                "report_name": self.report.name,
                "query_names": [],
                "parameter_defaults": {},
                "show_full_table_names": True,
                "csv_filenames": {},
                "graph_session": {},
            }
            self.parameter_defaults = {}

    def get_show_full_table_names(self) -> bool:
        """Return persisted toggle state for full vs short table names."""
        return self.flow_data.get("show_full_table_names", True)

    def get_csv_filenames(self) -> Dict[str, str]:
        """Return dictionary mapping query_name to custom CSV filename."""
        return self.flow_data.get("csv_filenames", {})

    def set_csv_filename(self, query_name: str, filename: str) -> None:
        """Set a custom CSV filename for a query in this flow."""
        if "csv_filenames" not in self.flow_data:
            self.flow_data["csv_filenames"] = {}
        self.flow_data["csv_filenames"][query_name] = filename

    def get_graph_session(self) -> Dict[str, Any]:
        """Return the serialized NodeGraph session dictionary."""
        return self.flow_data.get("graph_session", {})

    def get_query_names(self) -> List[str]:
        """Return list of query names used in this flow."""
        return self.flow_data.get("query_names", [])

    def get_unique_parameters(self, active_query_names: List[str]) -> List[str]:
        """Get list of unique parameter names across the queries in this flow."""
        unique_params: List[str] = []
        seen: Set[str] = set()

        for qname in active_query_names:
            qinfo = self.report.get_query(qname)
            if qinfo:
                for param in qinfo.parameters:
                    if param.name not in seen:
                        seen.add(param.name)
                        unique_params.append(param.name)

        return unique_params

    def get_execution_order(self, graph_session: Optional[Dict[str, Any]] = None) -> List[str]:
        """Determine topological execution order of queries in the process flow."""
        session = graph_session or self.get_graph_session()
        nodes_dict = session.get("nodes", {})
        connections = session.get("connections", [])

        # Map node id to query name
        id_to_qname: Dict[str, str] = {}
        for nid, ndata in nodes_dict.items():
            if ndata.get("type_") == "reporting.nodes.QueryNode":
                qname = ndata.get("custom", {}).get("query_name") or ndata.get("name")
                id_to_qname[nid] = qname

        if not id_to_qname:
            return self.get_query_names()

        # Build dependency graph
        in_degree: Dict[str, int] = {nid: 0 for nid in id_to_qname}
        adj_list: Dict[str, List[str]] = defaultdict(list)

        for conn in connections:
            out_info = conn.get("out", [])
            in_info = conn.get("in", [])
            if len(out_info) >= 2 and len(in_info) >= 2:
                out_id, out_port = out_info[0], out_info[1]
                in_id, in_port = in_info[0], in_info[1]
                if out_port == "run_out" and in_port == "run_in":
                    if out_id in id_to_qname and in_id in id_to_qname:
                        adj_list[out_id].append(in_id)
                        in_degree[in_id] += 1

        # Kahn's algorithm for topological sort
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        ordered_ids: List[str] = []

        while queue:
            curr = queue.popleft()
            ordered_ids.append(curr)
            for neighbor in adj_list[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Include any remaining nodes (in case of cycles or disconnected components)
        for nid in id_to_qname:
            if nid not in ordered_ids:
                ordered_ids.append(nid)

        return [id_to_qname[nid] for nid in ordered_ids]

    def save_flow(
        self,
        active_query_names: List[str],
        parameter_defaults: Dict[str, str],
        graph_session: Dict[str, Any],
        show_full_table_names: bool = True,
        csv_filenames: Optional[Dict[str, str]] = None,
    ) -> None:
        """Save process flow to JSON and update database parameter defaults."""
        self.parameter_defaults.update(parameter_defaults)
        self.flow_data["query_names"] = active_query_names
        self.flow_data["parameter_defaults"] = self.parameter_defaults
        self.flow_data["show_full_table_names"] = show_full_table_names
        if csv_filenames is not None:
            self.flow_data["csv_filenames"] = csv_filenames
        self.flow_data["graph_session"] = graph_session

        FlowStorage.save(
            file_path=self.file_path,
            flow_name=self.flow_name,
            report_name=self.report.name,
            query_names=active_query_names,
            parameter_defaults=self.parameter_defaults,
            graph_session=graph_session,
            show_full_table_names=show_full_table_names,
            csv_filenames=self.flow_data.get("csv_filenames", {}),
        )

        # Update persistence layer defaults if repository is provided
        if self.repo:
            for param, default_val in self.parameter_defaults.items():
                self.repo.set_parameter_default("flow", self.flow_name, param, default_val)

        logger.info(f"Process flow saved to {self.file_path}")
        self.flow_saved.emit(self.flow_name)

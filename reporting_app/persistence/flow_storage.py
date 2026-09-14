"""Process flow JSON file loader and saver."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class FlowStorage:
    """Manages reading and writing process flow JSON files."""

    @staticmethod
    def load(file_path: Path) -> Dict[str, Any]:
        """Load process flow JSON data from disk."""
        if not file_path.exists():
            return {}
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                return {}
            return json.loads(content)
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def save(
        file_path: Path,
        flow_name: str,
        report_name: str,
        query_names: List[str],
        parameter_defaults: Dict[str, str],
        graph_session: Dict[str, Any],
        show_full_table_names: bool = False,
        csv_filenames: Optional[Dict[str, str]] = None,
        csv_imports: Optional[List[dict]] = None,
        view_state: Optional[Dict[str, Any]] = None,
        splitter_sizes: Optional[List[int]] = None,
        parameter_date_options: Optional[Dict[str, str]] = None,
        selected_filename_date_param: Optional[str] = None,
        has_report_date: Optional[bool] = None,
    ) -> None:
        """Save process flow JSON data to disk."""
        data = {
            "flow_name": flow_name,
            "report_name": report_name,
            "query_names": query_names,
            "parameter_defaults": parameter_defaults,
            "show_full_table_names": show_full_table_names,
            "csv_filenames": csv_filenames or {},
            "csv_imports": csv_imports or [],
            "graph_session": graph_session,
            "view_state": view_state or {},
            "splitter_sizes": splitter_sizes,
            "parameter_date_options": parameter_date_options or {},
            "selected_filename_date_param": selected_filename_date_param,
            "has_report_date": has_report_date,
        }
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

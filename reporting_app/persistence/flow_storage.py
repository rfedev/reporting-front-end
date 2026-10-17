"""Process flow JSON file loader, saver, and schema migrator."""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class FlowStorage:
    """Manages reading and writing process flow JSON files and separated meta.json."""

    FLOW_SCHEMA_VERSION = 2
    META_FILENAME = "meta.json"

    @staticmethod
    def get_meta_path(flow_file_path: Path) -> Path:
        """Return the meta.json path in the same folder as the process flow file."""
        return flow_file_path.parent / FlowStorage.META_FILENAME

    @staticmethod
    def is_meta_file(file_path: Path) -> bool:
        """Return True if file_path refers to the process flow metadata file."""
        return file_path.name.lower() == FlowStorage.META_FILENAME

    @staticmethod
    def _load_all_meta(meta_path: Path) -> Dict[str, Any]:
        """Load the full meta.json dictionary from disk."""
        if not meta_path.exists():
            return {"version": FlowStorage.FLOW_SCHEMA_VERSION, "flows": {}}
        try:
            content = meta_path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                return {"version": FlowStorage.FLOW_SCHEMA_VERSION, "flows": {}}
            data = json.loads(content)
            if not isinstance(data, dict):
                return {"version": FlowStorage.FLOW_SCHEMA_VERSION, "flows": {}}
            if "flows" not in data or not isinstance(data.get("flows"), dict):
                # Migrate or normalize flat meta structure
                flows_dict = {
                    k: v for k, v in data.items()
                    if k != "version" and isinstance(v, dict)
                }
                return {"version": FlowStorage.FLOW_SCHEMA_VERSION, "flows": flows_dict}
            data["version"] = FlowStorage.FLOW_SCHEMA_VERSION
            return data
        except Exception:
            return {"version": FlowStorage.FLOW_SCHEMA_VERSION, "flows": {}}

    @staticmethod
    def _save_flow_meta(flow_file_path: Path, flow_name: str, meta_entry: Dict[str, Any]) -> None:
        """Write or update a single flow's metadata inside meta.json."""
        meta_path = FlowStorage.get_meta_path(flow_file_path)
        all_meta = FlowStorage._load_all_meta(meta_path)
        all_meta["version"] = FlowStorage.FLOW_SCHEMA_VERSION
        if "flows" not in all_meta or not isinstance(all_meta["flows"], dict):
            all_meta["flows"] = {}
        all_meta["flows"][flow_name] = meta_entry
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(all_meta, indent=2), encoding="utf-8")

    @staticmethod
    def rename_flow_meta(flow_file_path: Path, old_flow_name: str, new_flow_name: str) -> None:
        """Rename a flow key inside meta.json when a process flow is renamed."""
        meta_path = FlowStorage.get_meta_path(flow_file_path)
        if not meta_path.exists():
            return
        all_meta = FlowStorage._load_all_meta(meta_path)
        flows_dict = all_meta.get("flows", {})
        if old_flow_name in flows_dict:
            flows_dict[new_flow_name] = flows_dict.pop(old_flow_name)
            meta_path.write_text(json.dumps(all_meta, indent=2), encoding="utf-8")

    @staticmethod
    def remove_flow_meta(flow_file_path: Path, flow_name: str) -> None:
        """Remove a flow's metadata entry from meta.json when deleted."""
        meta_path = FlowStorage.get_meta_path(flow_file_path)
        if not meta_path.exists():
            return
        all_meta = FlowStorage._load_all_meta(meta_path)
        flows_dict = all_meta.get("flows", {})
        if flow_name in flows_dict:
            flows_dict.pop(flow_name, None)
            meta_path.write_text(json.dumps(all_meta, indent=2), encoding="utf-8")

    @staticmethod
    def clone_flow_meta(flow_file_path: Path, source_flow_name: str, target_flow_name: str) -> None:
        """Clone metadata entry in meta.json from source_flow_name to target_flow_name."""
        meta_path = FlowStorage.get_meta_path(flow_file_path)
        if not meta_path.exists():
            return
        all_meta = FlowStorage._load_all_meta(meta_path)
        flows_dict = all_meta.get("flows", {})
        if source_flow_name in flows_dict:
            flows_dict[target_flow_name] = copy.deepcopy(flows_dict[source_flow_name])
            meta_path.write_text(json.dumps(all_meta, indent=2), encoding="utf-8")

    @staticmethod
    def _extract_node_positions(
        graph_session: Dict[str, Any],
        existing_positions: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, List[float]]:
        """Extract node layout coordinates from graph_session into a {node_name: [x, y]} dict."""
        positions: Dict[str, List[float]] = {}
        if isinstance(existing_positions, dict):
            for k, v in existing_positions.items():
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    positions[str(k)] = [float(v[0]), float(v[1])]

        if not isinstance(graph_session, dict):
            return positions

        nodes_dict = graph_session.get("nodes")
        if not isinstance(nodes_dict, dict) or not nodes_dict:
            return positions

        # When live nodes exist in graph_session, build positions for all current nodes
        extracted: Dict[str, List[float]] = {}
        for nid, ndata in nodes_dict.items():
            if not isinstance(ndata, dict):
                continue
            pos = ndata.get("pos")
            nname = ndata.get("name") or str(nid)
            qname = (ndata.get("custom") or {}).get("query_name") if isinstance(ndata.get("custom"), dict) else None
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                coord = [float(pos[0]), float(pos[1])]
            elif nname in positions:
                coord = list(positions[nname])
            elif qname and qname in positions:
                coord = list(positions[qname])
            else:
                coord = [0.0, 0.0]
            extracted[str(nname)] = coord
            if qname and qname != nname:
                extracted[str(qname)] = coord

        return extracted

    @staticmethod
    def _canonicalize_graph_session(graph_session: Dict[str, Any]) -> Dict[str, Any]:
        """Return a deterministic structural copy of graph_session with layout coordinates stripped."""
        if not isinstance(graph_session, dict) or not graph_session:
            return {}

        nodes_raw = graph_session.get("nodes")
        if not isinstance(nodes_raw, dict) or not nodes_raw:
            return {}

        # Map volatile hex IDs to deterministic canonical IDs based on node name
        sorted_items = sorted(
            nodes_raw.items(),
            key=lambda kv: (
                str(kv[1].get("name", "")) if isinstance(kv[1], dict) else "",
                str(kv[0]),
            ),
        )

        id_map: Dict[str, str] = {}
        used_canonical_ids: set = set()
        canonical_nodes: Dict[str, Dict[str, Any]] = {}

        for orig_id, ndata in sorted_items:
            if not isinstance(ndata, dict):
                continue
            base_id = str(ndata.get("name") or orig_id).strip() or str(orig_id)
            candidate_id = base_id
            suffix = 2
            while candidate_id in used_canonical_ids:
                candidate_id = f"{base_id}#{suffix}"
                suffix += 1
            used_canonical_ids.add(candidate_id)
            id_map[str(orig_id)] = candidate_id

            clean_node = copy.deepcopy(ndata)
            # Strip volatile layout coordinates and transient UI properties
            clean_node.pop("pos", None)
            clean_node.pop("selected", None)
            clean_node.pop("width", None)
            clean_node.pop("height", None)
            canonical_nodes[candidate_id] = clean_node

        # Canonicalize and sort connections
        canonical_conns: List[Dict[str, List[str]]] = []
        seen_conns: set = set()
        for conn in graph_session.get("connections", []):
            if not isinstance(conn, dict):
                continue
            in_pair = conn.get("in", [])
            out_pair = conn.get("out", [])
            if isinstance(in_pair, (list, tuple)) and len(in_pair) >= 2 and isinstance(out_pair, (list, tuple)) and len(out_pair) >= 2:
                c_in_node = id_map.get(str(in_pair[0]), str(in_pair[0]))
                c_in_port = str(in_pair[1])
                c_out_node = id_map.get(str(out_pair[0]), str(out_pair[0]))
                c_out_port = str(out_pair[1])
                conn_key = (c_out_node, c_out_port, c_in_node, c_in_port)
                if conn_key not in seen_conns:
                    seen_conns.add(conn_key)
                    canonical_conns.append({
                        "in": [c_in_node, c_in_port],
                        "out": [c_out_node, c_out_port],
                    })

        canonical_conns.sort(key=lambda c: (c["out"][0], c["out"][1], c["in"][0], c["in"][1]))

        result: Dict[str, Any] = {}
        if "graph" in graph_session and isinstance(graph_session["graph"], dict):
            result["graph"] = copy.deepcopy(graph_session["graph"])
        result["nodes"] = canonical_nodes
        result["connections"] = canonical_conns
        return result

    @staticmethod
    def _rehydrate_graph_session(
        graph_session: Dict[str, Any],
        node_positions: Dict[str, List[float]],
    ) -> Dict[str, Any]:
        """Inject node coordinates from meta.json back into graph_session['nodes']."""
        if not isinstance(graph_session, dict) or not graph_session:
            return {}
        session = copy.deepcopy(graph_session)
        nodes_dict = session.get("nodes")
        if not isinstance(nodes_dict, dict):
            return session

        for nid, ndata in nodes_dict.items():
            if not isinstance(ndata, dict):
                continue
            nname = ndata.get("name") or str(nid)
            qname = (ndata.get("custom") or {}).get("query_name") if isinstance(ndata.get("custom"), dict) else None
            pos = None
            if isinstance(node_positions, dict):
                if nname in node_positions:
                    pos = node_positions[nname]
                elif qname and qname in node_positions:
                    pos = node_positions[qname]
                elif str(nid) in node_positions:
                    pos = node_positions[str(nid)]
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                ndata["pos"] = [float(pos[0]), float(pos[1])]
            elif "pos" not in ndata:
                ndata["pos"] = [0.0, 0.0]
            ndata["selected"] = False

        return session

    @staticmethod
    def _build_structural_payload(
        flow_name: str,
        report_name: str,
        query_names: List[str],
        csv_filenames: Optional[Dict[str, str]],
        csv_imports: Optional[List[dict]],
        lan_output_folder: Optional[str],
        graph_session: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Construct the versioned structural dictionary for <FlowName>.json."""
        canonical_session = FlowStorage._canonicalize_graph_session(graph_session or {})
        has_canvas_nodes = bool(canonical_session.get("nodes"))

        clean_queries = list(query_names or [])
        if has_canvas_nodes:
            clean_queries = sorted(set(clean_queries), key=str)

        clean_imports = copy.deepcopy(list(csv_imports or []))
        if has_canvas_nodes and clean_imports:
            clean_imports.sort(key=lambda item: str(item.get("node_name", "")) if isinstance(item, dict) else "")

        payload = {
            "version": FlowStorage.FLOW_SCHEMA_VERSION,
            "flow_name": flow_name,
            "report_name": report_name,
            "query_names": clean_queries,
            "csv_filenames": dict(csv_filenames or {}),
            "csv_imports": clean_imports,
            "lan_output_folder": lan_output_folder,
            "graph_session": canonical_session,
        }
        return json.loads(json.dumps(payload))

    @staticmethod
    def migrate(file_path: Path, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Migrate an unversioned or mismatched process flow JSON file to FLOW_SCHEMA_VERSION."""
        flow_name = str(raw_data.get("flow_name") or file_path.stem)
        report_name = str(raw_data.get("report_name") or (file_path.parent.parent.name if file_path.parent else ""))

        meta_path = FlowStorage.get_meta_path(file_path)
        all_meta = FlowStorage._load_all_meta(meta_path)
        existing_meta = all_meta.get("flows", {}).get(flow_name, {})

        raw_session = raw_data.get("graph_session", {})
        node_positions = FlowStorage._extract_node_positions(
            raw_session,
            existing_positions=raw_data.get("node_positions") or existing_meta.get("node_positions"),
        )

        meta_entry = {
            "node_positions": node_positions,
            "parameter_defaults": raw_data.get("parameter_defaults", existing_meta.get("parameter_defaults", {})) or {},
            "parameter_date_options": raw_data.get("parameter_date_options", existing_meta.get("parameter_date_options", {})) or {},
            "selected_filename_date_param": raw_data.get(
                "selected_filename_date_param", existing_meta.get("selected_filename_date_param")
            ),
            "has_report_date": raw_data.get("has_report_date", existing_meta.get("has_report_date")),
            "show_full_table_names": bool(
                raw_data.get("show_full_table_names", existing_meta.get("show_full_table_names", False))
            ),
            "view_state": raw_data.get("view_state", existing_meta.get("view_state", {})) or {},
            "splitter_sizes": raw_data.get("splitter_sizes", existing_meta.get("splitter_sizes")),
        }

        FlowStorage._save_flow_meta(file_path, flow_name, meta_entry)

        structural_payload = FlowStorage._build_structural_payload(
            flow_name=flow_name,
            report_name=report_name,
            query_names=raw_data.get("query_names", []),
            csv_filenames=raw_data.get("csv_filenames", {}),
            csv_imports=raw_data.get("csv_imports", []),
            lan_output_folder=raw_data.get("lan_output_folder"),
            graph_session=raw_session,
        )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(structural_payload, indent=2), encoding="utf-8")

        combined = copy.deepcopy(structural_payload)
        combined.update(copy.deepcopy(meta_entry))
        combined["graph_session"] = FlowStorage._rehydrate_graph_session(
            structural_payload.get("graph_session", {}),
            node_positions,
        )
        return combined

    @staticmethod
    def load(file_path: Path) -> Dict[str, Any]:
        """Load process flow JSON data and merge meta.json, migrating automatically if version mismatches."""
        if not file_path.exists():
            return {}
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                return {}
            data = json.loads(content)
            if not isinstance(data, dict):
                return {}

            if data.get("version") != FlowStorage.FLOW_SCHEMA_VERSION:
                return FlowStorage.migrate(file_path, data)

            flow_name = str(data.get("flow_name") or file_path.stem)
            meta_path = FlowStorage.get_meta_path(file_path)
            all_meta = FlowStorage._load_all_meta(meta_path)
            flow_meta = all_meta.get("flows", {}).get(flow_name, {})

            node_positions = flow_meta.get("node_positions", {})
            combined = copy.deepcopy(data)
            combined["node_positions"] = copy.deepcopy(node_positions)
            combined["parameter_defaults"] = copy.deepcopy(flow_meta.get("parameter_defaults", {}))
            combined["parameter_date_options"] = copy.deepcopy(flow_meta.get("parameter_date_options", {}))
            combined["selected_filename_date_param"] = flow_meta.get("selected_filename_date_param")
            combined["has_report_date"] = flow_meta.get("has_report_date")
            combined["show_full_table_names"] = bool(flow_meta.get("show_full_table_names", False))
            combined["view_state"] = copy.deepcopy(flow_meta.get("view_state", {}))
            combined["splitter_sizes"] = copy.deepcopy(flow_meta.get("splitter_sizes"))
            combined["graph_session"] = FlowStorage._rehydrate_graph_session(
                data.get("graph_session", {}),
                node_positions,
            )
            return combined
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
        lan_output_folder: Optional[str] = None,
        node_positions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Save layout coordinates & run parameters to meta.json, and only update <FlowName>.json if execution nodes changed.

        Returns True if the main <FlowName>.json file was written, False if only meta.json was updated.
        """
        meta_path = FlowStorage.get_meta_path(file_path)
        all_meta = FlowStorage._load_all_meta(meta_path)
        existing_meta = all_meta.get("flows", {}).get(flow_name, {})

        extracted_positions = FlowStorage._extract_node_positions(
            graph_session,
            existing_positions=node_positions or existing_meta.get("node_positions"),
        )

        meta_entry = {
            "node_positions": extracted_positions,
            "parameter_defaults": parameter_defaults or {},
            "parameter_date_options": parameter_date_options or {},
            "selected_filename_date_param": selected_filename_date_param,
            "has_report_date": has_report_date,
            "show_full_table_names": bool(show_full_table_names),
            "view_state": view_state or {},
            "splitter_sizes": splitter_sizes,
        }

        FlowStorage._save_flow_meta(file_path, flow_name, meta_entry)

        structural_payload = FlowStorage._build_structural_payload(
            flow_name=flow_name,
            report_name=report_name,
            query_names=query_names,
            csv_filenames=csv_filenames,
            csv_imports=csv_imports,
            lan_output_folder=lan_output_folder,
            graph_session=graph_session,
        )

        should_write_main = True
        if file_path.exists():
            try:
                existing_text = file_path.read_text(encoding="utf-8", errors="replace").strip()
                if existing_text:
                    existing_disk_data = json.loads(existing_text)
                    if existing_disk_data == structural_payload:
                        should_write_main = False
            except Exception:
                should_write_main = True

        if should_write_main:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(json.dumps(structural_payload, indent=2), encoding="utf-8")

        return should_write_main


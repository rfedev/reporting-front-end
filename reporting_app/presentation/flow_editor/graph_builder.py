"""Graph topology builder for Process Flow Editor following the architectural specification:

1. External Input Sources (Green Nodes / Green Edges): Root-level providers originating outside the graph.
2. Query Nodes (Blue Nodes): Processing units.
3. Output Tables (Orange Nodes / Orange Edges): Direct creation from Query Nodes.
4. Convergence Pattern: When a downstream query requires tables from multiple upstream queries
   (or a subset), a consolidated Output Box is created with incoming orange edges and outgoing green intake edges.
5. Direct Linear Chaining: When an output box feeds exclusively into a downstream query.
6. Reactive Synchronization: Dynamic recalculation when queries are added, edited, or removed.
"""

from collections import defaultdict
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from NodeGraphQt import BaseNode, NodeGraph
from reporting_app.core.models import QueryInfo
from reporting_app.presentation.flow_editor.nodes import GroupBoxNode, ImportCsvNode, QueryNode, TableBoxNode

logger = logging.getLogger(__name__)


def get_node_dims(n, default_w: float = 180.0, default_h: float = 60.0) -> Tuple[float, float]:
    if not n:
        return default_w, default_h
    try:
        view = getattr(n, "view", None)
        if view:
            br = view.boundingRect()
            return max(br.width(), default_w), max(br.height(), default_h)
    except Exception:
        pass
    return default_w, default_h


class ProcessFlowGraphBuilder:
    """Builds and updates the NodeGraph according to Process Flow Graph Logic."""

    @staticmethod
    def rebuild_graph(
        graph: NodeGraph,
        queries: List[QueryInfo],
        existing_positions: Optional[Dict[str, Tuple[float, float]]] = None,
        show_full_table_names: bool = False,
        csv_filenames: Optional[Dict[str, str]] = None,
        import_csv_data: Optional[List[dict]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """Reconstruct the entire graph topology according to the architectural rules

        while preserving node positions where possible.
        """
        existing_positions = dict(existing_positions or {})
        csv_filenames = csv_filenames or {}
        import_csv_data = import_csv_data or []

        # 0. Capture user execution connections (run_out -> run_in) to preserve them
        preserved_run_conns: List[Tuple[str, str]] = []
        for node in graph.all_nodes():
            if isinstance(node, GroupBoxNode) or getattr(node, "type_", "") == "reporting.nodes.GroupBoxNode":
                continue
            run_out = node.get_output("run_out")
            if run_out:
                for target_port in run_out.connected_ports():
                    target_node = target_port.node()
                    src_name = node.get_property("query_name") or node.name()
                    tgt_name = target_node.get_property("query_name") or target_node.name()
                    preserved_run_conns.append((src_name, tgt_name))

        # 1. Capture current live positions of all existing nodes from canvas.
        # Canvas positions MUST take precedence over any stale/passed-in positions.
        for node in graph.all_nodes():
            if isinstance(node, GroupBoxNode) or getattr(node, "type_", "") == "reporting.nodes.GroupBoxNode":
                continue
            pos = node.pos()
            existing_positions[node.name()] = (pos[0], pos[1])
            qname = node.get_property("query_name") if hasattr(node, "get_property") else None
            if qname:
                existing_positions[qname] = (pos[0], pos[1])

        # Remove keys belonging to queries no longer present on canvas
        active_query_names = {q.name for q in queries}
        active_query_names.add("Import Files")
        active_query_names.add("Import csv")
        for idx in range(len(import_csv_data) + 5):
            active_query_names.add(f"Import Files {idx}")
            active_query_names.add(f"Import csv {idx}")

        keys_to_remove = [
            k for k in list(existing_positions.keys())
            if not any(k == qn or k.startswith(f"{qn} [") for qn in active_query_names)
        ]
        for k in keys_to_remove:
            existing_positions.pop(k, None)

        # Clear existing non-group nodes (leave GroupBoxNode instances intact)
        nodes_to_delete = [
            n for n in graph.all_nodes()
            if not (isinstance(n, GroupBoxNode) or getattr(n, "type_", "") == "reporting.nodes.GroupBoxNode")
        ]
        if nodes_to_delete:
            graph.delete_nodes(nodes_to_delete)

        if not queries and not import_csv_data:
            return existing_positions

        # 2. Build table provenance index across active queries and CSV imports on canvas
        # Single-Writer Provenance: table -> creating query/import name
        table_producers: Dict[str, str] = {}
        for q in queries:
            for out_t in q.output_tables:
                table_producers[out_t] = q.name
        for idx, imp_group in enumerate(import_csv_data):
            imp_name = imp_group.get("node_name", "Import Files" if idx == 0 else f"Import Files {idx}")
            for item in imp_group.get("items", []):
                t = item.get("output_table", "").strip()
                if t:
                    table_producers[t] = imp_name

        # 3. Create all Query Nodes and their immediate Output Boxes & CSV Boxes
        query_nodes: Dict[str, QueryNode] = {}
        output_boxes: Dict[str, TableBoxNode] = {}
        csv_boxes: Dict[str, TableBoxNode] = {}

        # Lay out horizontally: query1, query2...
        col_x = 0
        for q in queries:
            q_pos = existing_positions.get(q.name, (col_x, 0))
            qx, qy = q_pos

            qnode: QueryNode = graph.create_node(
                "reporting.nodes.QueryNode",
                name=q.name,
                pos=[qx, qy],
            )
            qnode.set_property("query_name", q.name)
            qnode.set_property("query_path", str(q.file_path.resolve()) if q.file_path else "")
            qnode.set_property("report_name", q.report_name)
            qnode.set_parameters(q.parameter_names)
            query_nodes[q.name] = qnode

            # Create immediate Output Box (Orange) if query produces output tables
            if q.output_tables:
                out_box_key = f"{q.name} [Out]"
                out_pos = existing_positions.get(out_box_key, (qx + 260, qy))
                out_box: TableBoxNode = graph.create_node(
                    "reporting.nodes.TableBoxNode",
                    name=out_box_key,
                    pos=[out_pos[0], out_pos[1]],
                )
                out_box.setup_as_output(q.output_tables)
                out_box.set_display_mode(show_full_table_names)
                try:
                    qnode.get_output("tables_out").connect_to(out_box.get_input("in_tables"))
                except Exception as e:
                    logger.debug(f"Could not connect {q.name} to Output Box: {e}")
                output_boxes[q.name] = out_box

            # Create Output CSV Box (Dark Purple) if query exports to CSV
            if q.has_output_csv or q.output_csv_tables:
                csv_key = f"{q.name} [CSV]"
                csv_y_offset = 120 if q.output_tables else 0
                csv_pos = existing_positions.get(csv_key, (qx + 260, qy + csv_y_offset))
                csv_box: TableBoxNode = graph.create_node(
                    "reporting.nodes.TableBoxNode",
                    name=csv_key,
                    pos=[csv_pos[0], csv_pos[1]],
                )
                files_to_show = []
                if q.output_csv_tables:
                    files_to_show = [f for f in q.output_csv_tables if f]
                elif csv_filenames and q.name in csv_filenames:
                    val = csv_filenames[q.name]
                    if isinstance(val, (list, tuple)):
                        files_to_show = list(val)
                    elif "," in str(val):
                        files_to_show = [s.strip() for s in str(val).split(",") if s.strip()]
                    elif val:
                        files_to_show = [str(val).strip()]

                if not files_to_show:
                    files_to_show = [f"{q.name}.csv"]

                csv_box.setup_as_csv_output(files_to_show)
                csv_box.set_property("query_owner", q.name)
                csv_box.set_display_mode(show_full_table_names)
                try:
                    csv_port = qnode.get_output("csv_out") or qnode.get_output("tables_out")
                    csv_port.connect_to(csv_box.get_input("in_tables"))
                except Exception as e:
                    logger.debug(f"Could not connect {q.name} to CSV Box: {e}")
                csv_boxes[q.name] = csv_box

            col_x += 650

        import_nodes: Dict[str, ImportCsvNode] = {}
        for idx, imp_group in enumerate(import_csv_data):
            imp_name = imp_group.get("node_name", "Import Files" if idx == 0 else f"Import Files {idx}")
            ipos = existing_positions.get(imp_name, (col_x, 0))
            inode: ImportCsvNode = graph.create_node(
                "reporting.nodes.ImportCsvNode",
                name=imp_name,
                pos=[ipos[0], ipos[1]],
            )
            items = imp_group.get("items", [])
            inode.set_imports(items)
            import_nodes[imp_name] = inode

            in_files = inode.get_input_files()
            if in_files:
                in_box_key = f"{imp_name} [In]"
                in_pos = existing_positions.get(in_box_key, (ipos[0] - 280, ipos[1]))
                in_box: TableBoxNode = graph.create_node(
                    "reporting.nodes.TableBoxNode",
                    name=in_box_key,
                    pos=[in_pos[0], in_pos[1]],
                )
                in_box.setup_as_input(in_files)
                in_box.set_display_mode(show_full_table_names)
                try:
                    in_box.get_output("out_tables").connect_to(inode.get_input("tables_in"))
                except Exception as e:
                    logger.debug(f"Could not connect {imp_name} to Input Box: {e}")

            out_tables = inode.get_output_tables()
            if out_tables:
                out_box_key = f"{imp_name} [Out]"
                out_pos = existing_positions.get(out_box_key, (ipos[0] + 260, ipos[1]))
                out_box: TableBoxNode = graph.create_node(
                    "reporting.nodes.TableBoxNode",
                    name=out_box_key,
                    pos=[out_pos[0], out_pos[1]],
                )
                out_box.setup_as_output(out_tables)
                out_box.set_display_mode(show_full_table_names)
                try:
                    inode.get_output("tables_out").connect_to(out_box.get_input("in_tables"))
                except Exception as e:
                    logger.debug(f"Could not connect {imp_name} to Output Box: {e}")
                output_boxes[imp_name] = out_box

            col_x += 650

        # 5. Resolve Input Requirements for each Query Node
        for q in queries:
            qnode = query_nodes[q.name]
            qx, qy = qnode.pos()

            # Partition required input tables into:
            # - external_tables: not produced by any active query or import in this graph
            # - derived_tables: produced by upstream queries or imports active in this graph
            external_tables: List[str] = []
            derived_tables_by_producer: Dict[str, List[str]] = defaultdict(list)

            for in_t in q.input_tables:
                producer_qname = table_producers.get(in_t)
                if producer_qname and producer_qname in output_boxes:
                    derived_tables_by_producer[producer_qname].append(in_t)
                else:
                    external_tables.append(in_t)

            # A. Fresh external root inputs (Green Box -> Green Noodle -> Query Node)
            if external_tables:
                in_box_key = f"{q.name} [In]"
                in_pos = existing_positions.get(in_box_key) or existing_positions.get(f"{q.name} [Convergence]") or (qx - 280, qy - 40)
                in_box: TableBoxNode = graph.create_node(
                    "reporting.nodes.TableBoxNode",
                    name=in_box_key,
                    pos=[in_pos[0], in_pos[1]],
                )
                in_box.setup_as_input(external_tables)
                in_box.set_display_mode(show_full_table_names)
                try:
                    in_box.get_output("out_tables").connect_to(qnode.get_input("tables_in"))
                except Exception as e:
                    logger.debug(f"Could not connect External Input Box to {q.name}: {e}")

            # B. Derived tables intake:
            if not derived_tables_by_producer:
                continue

            num_producers = len(derived_tables_by_producer)
            single_producer = list(derived_tables_by_producer.keys())[0] if num_producers == 1 else None
            is_complete_output = False
            if single_producer and single_producer in output_boxes:
                producer_box = output_boxes[single_producer]
                is_complete_output = set(derived_tables_by_producer[single_producer]) == set(producer_box.raw_tables)

            if num_producers == 1 and is_complete_output:
                # Direct Linear Chaining:
                # Direct intake connection: Upstream Output Box -> Green Noodle -> Downstream Query Node
                upstream_out_box = output_boxes[single_producer]
                try:
                    upstream_out_box.get_output("out_tables").connect_to(qnode.get_input("tables_in"))
                except Exception as e:
                    logger.debug(f"Could not connect linear chaining {single_producer} to {q.name}: {e}")
            else:
                # Convergence Pattern / Selective Aggregation:
                # Create consolidated Output Box (Orange) for downstream query
                all_selected_tables: List[str] = []
                for tables in derived_tables_by_producer.values():
                    all_selected_tables.extend(tables)

                junction_key = f"{q.name} [Convergence]"
                j_pos = existing_positions.get(junction_key) or existing_positions.get(f"{q.name} [In]") or (qx - 280, qy + 60)
                junction_box: TableBoxNode = graph.create_node(
                    "reporting.nodes.TableBoxNode",
                    name=junction_key,
                    pos=[j_pos[0], j_pos[1]],
                )
                junction_box.setup_as_output(all_selected_tables)
                junction_box.set_property("box_type", "Output Tables")
                junction_box.view.set_custom_title("Output Tables")
                junction_box.set_display_mode(show_full_table_names)

                # Connect upstream output boxes -> Orange noodles -> junction box
                for producer_name in derived_tables_by_producer.keys():
                    producer_out_box = output_boxes[producer_name]
                    try:
                        producer_out_box.get_output("out_tables").connect_to(junction_box.get_input("in_tables"))
                    except Exception as e:
                        logger.debug(f"Could not connect upstream box {producer_name} to junction {q.name}: {e}")

                # Connect junction box -> Green noodle -> downstream query
                try:
                    junction_box.get_output("out_tables").connect_to(qnode.get_input("tables_in"))
                except Exception as e:
                    logger.debug(f"Could not connect junction box to query {q.name}: {e}")

        # 6. Restore preserved user execution connections (run_out -> run_in)
        all_executable_nodes: Dict[str, BaseNode] = {**query_nodes, **import_nodes}
        for src_name, tgt_name in preserved_run_conns:
            src_node = all_executable_nodes.get(src_name)
            tgt_node = all_executable_nodes.get(tgt_name)
            if src_node and tgt_node:
                try:
                    src_out = src_node.get_output("run_out")
                    tgt_in = tgt_node.get_input("run_in")
                    if src_out and tgt_in:
                        src_out.connect_to(tgt_in)
                except Exception as e:
                    logger.debug(f"Could not restore connection {src_name} -> {tgt_name}: {e}")

        # Reset pipe colors across the scene
        for item in graph.viewer().scene().items():
            if hasattr(item, "reset"):
                try:
                    item.reset()
                except Exception:
                    pass

        return existing_positions

    @staticmethod
    def auto_layout(
        graph: NodeGraph,
        queries: List[QueryInfo],
        direction: str = "horizontal",
        import_csv_data: Optional[List[dict]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """Format the process flow layout horizontally (left to right) or vertically (top to bottom).

        Layout Rules:
        - Starting tables (input tables on the first execution node in a chain, or on an unconnected node)
          are placed to the LEFT of the execution node in horizontal mode and ABOVE in vertical mode.
        - Ending tables (output/CSV tables on the last execution node in a chain, or on an unconnected node)
          are placed to the RIGHT of the execution node in horizontal mode and BELOW in vertical mode.
        - Intermediate tables (inputs on nodes with upstream execution dependencies, or outputs that feed
          downstream execution nodes) remain flanked perpendicular to the flow axis (above/below in horizontal,
          left/right in vertical) so blue execution connections have a clear straight path.
        - Connected components are laid out with Sugiyama-style rank assignment and barycenter/permutation
          crossing minimization (prioritizing blue run_out -> run_in connections).
        - Individual, unconnected execution nodes are arranged in a clean horizontal or vertical line.
        """
        import itertools

        all_nodes = graph.all_nodes()
        nodes_by_name = {n.name(): n for n in all_nodes}

        import_nodes = [
            n for n in all_nodes
            if getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
            or n.__class__.__name__ in ("ImportCsvNode", "ImportFilesNode")
        ]

        if not queries and not import_nodes:
            return {}

        all_ops: List[str] = [inode.name() for inode in import_nodes] + [q.name for q in queries]
        primary_nodes: Dict[str, BaseNode] = {}
        for inode in import_nodes:
            primary_nodes[inode.name()] = inode
        for q in queries:
            qn = nodes_by_name.get(q.name)
            if qn:
                primary_nodes[q.name] = qn

        # Deduplicate while preserving order
        valid_ops: List[str] = []
        for op_name in all_ops:
            if op_name in primary_nodes and op_name not in valid_ops:
                valid_ops.append(op_name)
        if not valid_ops:
            return {}

        # 1. Map input and output table boxes to their respective operations
        in_boxes_by_op: Dict[str, List[BaseNode]] = defaultdict(list)
        out_boxes_by_op: Dict[str, List[BaseNode]] = defaultdict(list)

        for op_name in valid_ops:
            in_list = []
            for suffix in ["[In]", "[Convergence]"]:
                b = nodes_by_name.get(f"{op_name} {suffix}")
                if b and b not in in_list:
                    in_list.append(b)

            out_list = []
            for suffix in ["[Out]", "[CSV]"]:
                b = nodes_by_name.get(f"{op_name} {suffix}")
                if b and b not in out_list:
                    out_list.append(b)

            in_boxes_by_op[op_name] = in_list
            out_boxes_by_op[op_name] = out_list

        for n in all_nodes:
            if getattr(n, "type_", "") == "reporting.nodes.TableBoxNode" or n.__class__.__name__ == "TableBoxNode":
                owner = n.get_property("query_owner") if hasattr(n, "get_property") else None
                b_type = n.get_property("box_type") if hasattr(n, "get_property") else ""
                if owner and owner in valid_ops:
                    if "Input" in b_type:
                        if n not in in_boxes_by_op[owner]:
                            in_boxes_by_op[owner].append(n)
                    elif "Output" in b_type or "CSV" in b_type:
                        if n not in out_boxes_by_op[owner]:
                            out_boxes_by_op[owner].append(n)

        # 2. Build dependency graph (both table provenance and blue execution edges)
        table_producers: Dict[str, str] = {}
        for inode in import_nodes:
            out_tables = inode.get_output_tables() if hasattr(inode, "get_output_tables") else []
            for t in out_tables:
                table_producers[t] = inode.name()

        for q in queries:
            for out_t in q.output_tables:
                table_producers[out_t] = q.name

        all_producers = set(valid_ops)
        upstreams: Dict[str, Set[str]] = {op_name: set() for op_name in valid_ops}
        blue_edges: Set[Tuple[str, str]] = set()
        data_edges: Set[Tuple[str, str]] = set()

        for op_name in valid_ops:
            pnode = primary_nodes[op_name]
            run_in = pnode.get_input("run_in") if hasattr(pnode, "get_input") else None
            if run_in:
                for port in run_in.connected_ports():
                    up_node = port.node()
                    up_name = (
                        (up_node.get_property("query_name") if hasattr(up_node, "get_property") else None)
                        or up_node.name()
                    )
                    if up_name in all_producers and up_name != op_name:
                        upstreams[op_name].add(up_name)
                        blue_edges.add((up_name, op_name))

        for q in queries:
            if q.name not in all_producers:
                continue
            for in_t in q.input_tables:
                prod = table_producers.get(in_t)
                if prod and prod in all_producers and prod != q.name:
                    upstreams[q.name].add(prod)
                    data_edges.add((prod, q.name))

        downstreams: Dict[str, Set[str]] = {op_name: set() for op_name in valid_ops}
        for op_name in valid_ops:
            for up in upstreams[op_name]:
                downstreams[up].add(op_name)

        all_edges: Set[Tuple[str, str]] = blue_edges | data_edges

        # 3. Classify each operation's input and output table boxes into 4 directional slots
        #    (left, right, above, below) relative to its execution node:
        #    - Starting tables (no upstream execution node): Left in Horizontal, Above in Vertical
        #    - Ending tables (no downstream execution node): Right in Horizontal, Below in Vertical
        #    - Intermediate tables: Above/Below in Horizontal, Left/Right in Vertical
        class ClusterMetrics:
            def __init__(
                self,
                name: str,
                prim: BaseNode,
                in_b: List[BaseNode],
                out_b: List[BaseNode],
                is_start: bool,
                is_end: bool,
                layout_dir: str,
            ):
                self.name = name
                self.prim = prim
                self.w_prim, self.h_prim = get_node_dims(prim, 180.0, 70.0)

                if layout_dir == "horizontal":
                    self.left_b = list(in_b) if is_start else []
                    self.above_b = [] if is_start else list(in_b)
                    self.right_b = list(out_b) if is_end else []
                    self.below_b = [] if is_end else list(out_b)
                else:
                    self.above_b = list(in_b) if is_start else []
                    self.left_b = [] if is_start else list(in_b)
                    self.below_b = list(out_b) if is_end else []
                    self.right_b = [] if is_end else list(out_b)

                self.left_dims = [get_node_dims(b, 200.0, 60.0) for b in self.left_b]
                self.right_dims = [get_node_dims(b, 200.0, 60.0) for b in self.right_b]
                self.above_dims = [get_node_dims(b, 200.0, 60.0) for b in self.above_b]
                self.below_dims = [get_node_dims(b, 200.0, 60.0) for b in self.below_b]

                self.intra_gap_x = 20.0
                self.intra_gap_y = 20.0
                self.gap_x_side = 55.0
                self.gap_y_side = 50.0

                # Left stack (stacked vertically to the left of prim)
                if self.left_dims:
                    self.left_w = max(d[0] for d in self.left_dims)
                    self.left_h = sum(d[1] for d in self.left_dims) + (len(self.left_dims) - 1) * self.intra_gap_y
                else:
                    self.left_w, self.left_h = 0.0, 0.0

                # Right stack (stacked vertically to the right of prim)
                if self.right_dims:
                    self.right_w = max(d[0] for d in self.right_dims)
                    self.right_h = sum(d[1] for d in self.right_dims) + (len(self.right_dims) - 1) * self.intra_gap_y
                else:
                    self.right_w, self.right_h = 0.0, 0.0

                # Above row (placed side-by-side above prim)
                if self.above_dims:
                    self.above_w = sum(d[0] for d in self.above_dims) + (len(self.above_dims) - 1) * self.intra_gap_x
                    self.above_h = max(d[1] for d in self.above_dims)
                else:
                    self.above_w, self.above_h = 0.0, 0.0

                # Below row (placed side-by-side below prim)
                if self.below_dims:
                    self.below_w = sum(d[0] for d in self.below_dims) + (len(self.below_dims) - 1) * self.intra_gap_x
                    self.below_h = max(d[1] for d in self.below_dims)
                else:
                    self.below_w, self.below_h = 0.0, 0.0

                # Extents from the center (cx, cy) of the primary execution node
                half_w = self.w_prim / 2.0
                half_h = self.h_prim / 2.0

                self.ext_left = max(
                    half_w + (self.gap_x_side + self.left_w if self.left_b else 0.0),
                    self.above_w / 2.0,
                    self.below_w / 2.0,
                )
                self.ext_right = max(
                    half_w + (self.gap_x_side + self.right_w if self.right_b else 0.0),
                    self.above_w / 2.0,
                    self.below_w / 2.0,
                )
                self.ext_above = max(
                    half_h + (self.gap_y_side + self.above_h if self.above_b else 0.0),
                    self.left_h / 2.0,
                    self.right_h / 2.0,
                )
                self.ext_below = max(
                    half_h + (self.gap_y_side + self.below_h if self.below_b else 0.0),
                    self.left_h / 2.0,
                    self.right_h / 2.0,
                )

        clusters: Dict[str, ClusterMetrics] = {}
        for name in valid_ops:
            is_start = len(upstreams[name]) == 0
            is_end = len(downstreams[name]) == 0
            clusters[name] = ClusterMetrics(
                name=name,
                prim=primary_nodes[name],
                in_b=in_boxes_by_op[name],
                out_b=out_boxes_by_op[name],
                is_start=is_start,
                is_end=is_end,
                layout_dir=direction,
            )

        def _compute_entity_centers(
            entity_ids: List[str],
            entity_extents: Dict[str, Tuple[float, float, float, float]],
            sub_upstreams: Dict[str, Set[str]],
            sub_downstreams: Dict[str, Set[str]],
            sub_blue_edges: Set[Tuple[str, str]],
            sub_data_edges: Set[Tuple[str, str]],
        ) -> Tuple[Dict[str, float], Dict[str, float]]:
            """Compute (center_x_map, center_y_map) for a list of entities with given extents and dependencies."""
            if not entity_ids:
                return {}, {}

            sub_all_edges: Set[Tuple[str, str]] = sub_blue_edges | sub_data_edges
            visited_ent: Set[str] = set()
            connected_components: List[List[str]] = []
            unconnected_ents: List[str] = []

            for eid in entity_ids:
                if eid in visited_ent:
                    continue
                if not sub_upstreams.get(eid) and not sub_downstreams.get(eid):
                    visited_ent.add(eid)
                    unconnected_ents.append(eid)
                    continue

                comp: List[str] = []
                queue = [eid]
                visited_ent.add(eid)
                while queue:
                    curr = queue.pop(0)
                    comp.append(curr)
                    neighbors = list(sub_upstreams.get(curr, set())) + list(sub_downstreams.get(curr, set()))
                    for nb in neighbors:
                        if nb not in visited_ent:
                            visited_ent.add(nb)
                            queue.append(nb)
                comp.sort(key=lambda x: entity_ids.index(x))
                connected_components.append(comp)

            ranks: Dict[str, int] = {}
            ent_lane: Dict[str, int] = {}
            current_base_lane = 0

            for comp in connected_components:
                comp_set = set(comp)
                comp_ranks: Dict[str, int] = {u: 0 for u in comp}

                # Longest-path topological rank assignment (with cycle guard)
                for _ in range(len(comp) + 1):
                    changed = False
                    for u in comp:
                        ups = [p for p in sub_upstreams[u] if p in comp_set]
                        if ups:
                            target_r = max(comp_ranks[p] for p in ups) + 1
                            if comp_ranks[u] < target_r and target_r <= len(comp):
                                comp_ranks[u] = target_r
                                changed = True
                    if not changed:
                        break

                # Pull source nodes (no upstreams) closer to their immediate downstream targets
                for u in comp:
                    if not sub_upstreams[u] and sub_downstreams[u]:
                        min_down_r = min(comp_ranks[d] for d in sub_downstreams[u] if d in comp_set)
                        if min_down_r > 1:
                            comp_ranks[u] = min_down_r - 1

                # Normalize ranks in this component to start at 0
                min_r = min(comp_ranks.values(), default=0)
                for u in comp:
                    comp_ranks[u] -= min_r
                    ranks[u] = comp_ranks[u]

                rank_groups: Dict[int, List[str]] = defaultdict(list)
                for u in comp:
                    rank_groups[comp_ranks[u]].append(u)
                sorted_r = sorted(rank_groups.keys())

                order_in_rank: Dict[int, List[str]] = {
                    r: list(rank_groups[r]) for r in sorted_r
                }

                def edge_weight(u: str, v: str) -> float:
                    w = 0.0
                    if (u, v) in sub_blue_edges:
                        w += 10.0
                    if (u, v) in sub_data_edges:
                        w += 2.0
                    return w

                # Barycenter sweeps (forward + backward)
                for _sweep in range(6):
                    for r_idx in range(1, len(sorted_r)):
                        r = sorted_r[r_idx]
                        nodes_r = order_in_rank[r]
                        if len(nodes_r) <= 1:
                            continue
                        bary: Dict[str, float] = {}
                        for idx_u, u in enumerate(nodes_r):
                            ups = [p for p in sub_upstreams[u] if p in comp_set and comp_ranks[p] < r]
                            if ups:
                                num = 0.0
                                den = 0.0
                                for p in ups:
                                    p_r = comp_ranks[p]
                                    p_pos = order_in_rank[p_r].index(p)
                                    ew = edge_weight(p, u)
                                    num += p_pos * ew
                                    den += ew
                                bary[u] = num / den if den > 0 else float(idx_u)
                            else:
                                bary[u] = float(idx_u)
                        nodes_r.sort(key=lambda u: (bary[u], entity_ids.index(u)))

                    for r_idx in range(len(sorted_r) - 2, -1, -1):
                        r = sorted_r[r_idx]
                        nodes_r = order_in_rank[r]
                        if len(nodes_r) <= 1:
                            continue
                        bary: Dict[str, float] = {}
                        for idx_u, u in enumerate(nodes_r):
                            downs = [d for d in sub_downstreams[u] if d in comp_set and comp_ranks[d] > r]
                            if downs:
                                num = 0.0
                                den = 0.0
                                for d in downs:
                                    d_r = comp_ranks[d]
                                    d_pos = order_in_rank[d_r].index(d)
                                    ew = edge_weight(u, d)
                                    num += d_pos * ew
                                    den += ew
                                bary[u] = num / den if den > 0 else float(idx_u)
                            else:
                                bary[u] = float(idx_u)
                        nodes_r.sort(key=lambda u: (bary[u], entity_ids.index(u)))

                # Exact permutation refinement for ranks with <= 6 nodes
                comp_edges = [
                    (u, v) for (u, v) in sub_all_edges
                    if u in comp_set and v in comp_set and comp_ranks[u] != comp_ranks[v]
                ]

                def count_crossings(ordering_map: Dict[int, List[str]]) -> float:
                    pos_of: Dict[str, int] = {}
                    for r_val, n_list in ordering_map.items():
                        for i_pos, n_name in enumerate(n_list):
                            pos_of[n_name] = i_pos
                    cost = 0.0
                    n_edges = len(comp_edges)
                    for i in range(n_edges):
                        u1, v1 = comp_edges[i]
                        r_u1, r_v1 = comp_ranks[u1], comp_ranks[v1]
                        if r_u1 > r_v1:
                            u1, v1, r_u1, r_v1 = v1, u1, r_v1, r_u1
                        for j in range(i + 1, n_edges):
                            u2, v2 = comp_edges[j]
                            r_u2, r_v2 = comp_ranks[u2], comp_ranks[v2]
                            if r_u2 > r_v2:
                                u2, v2, r_u2, r_v2 = v2, u2, r_v2, r_u2
                            if max(r_u1, r_u2) < min(r_v1, r_v2) or (r_u1 == r_u2 and r_v1 == r_v2):
                                diff_u = pos_of[u1] - pos_of[u2]
                                diff_v = pos_of[v1] - pos_of[v2]
                                if diff_u * diff_v < 0:
                                    is_b1 = (u1, v1) in sub_blue_edges or (v1, u1) in sub_blue_edges
                                    is_b2 = (u2, v2) in sub_blue_edges or (v2, u2) in sub_blue_edges
                                    if is_b1 and is_b2:
                                        cost += 100.0
                                    elif is_b1 or is_b2:
                                        cost += 25.0
                                    else:
                                        cost += 5.0
                            if r_v1 - r_u1 > 1 and pos_of[u1] == pos_of[v1]:
                                for mid_r in range(r_u1 + 1, r_v1):
                                    if mid_r in ordering_map and pos_of[u1] < len(ordering_map[mid_r]):
                                        cost += 50.0
                    return cost

                for _pass in range(2):
                    for r in sorted_r:
                        nodes_r = order_in_rank[r]
                        if 1 < len(nodes_r) <= 6:
                            best_perm = list(nodes_r)
                            best_cost = count_crossings(order_in_rank)
                            if best_cost > 0:
                                for perm in itertools.permutations(nodes_r):
                                    order_in_rank[r] = list(perm)
                                    c_val = count_crossings(order_in_rank)
                                    if c_val < best_cost:
                                        best_cost = c_val
                                        best_perm = list(perm)
                                        if best_cost == 0:
                                            break
                                order_in_rank[r] = best_perm

                comp_lanes: Dict[str, int] = {}
                max_lane_used = 0
                for r_idx, r in enumerate(sorted_r):
                    nodes_r = order_in_rank[r]
                    used_in_r: Set[int] = set()
                    for idx_u, u in enumerate(nodes_r):
                        desired_lane = idx_u
                        if r_idx > 0:
                            ups_in_prev = [
                                p for p in sub_upstreams[u]
                                if p in comp_lanes and comp_ranks[p] < r
                            ]
                            if ups_in_prev:
                                blue_ups = [p for p in ups_in_prev if (p, u) in sub_blue_edges]
                                ref_ups = blue_ups if blue_ups else ups_in_prev
                                min_up_lane = min(comp_lanes[p] for p in ref_ups)
                                if min_up_lane >= desired_lane and min_up_lane not in used_in_r:
                                    desired_lane = min_up_lane
                        while desired_lane in used_in_r:
                            desired_lane += 1
                        if idx_u > 0:
                            prev_node_lane = comp_lanes[nodes_r[idx_u - 1]]
                            if desired_lane <= prev_node_lane:
                                desired_lane = prev_node_lane + 1
                                while desired_lane in used_in_r:
                                    desired_lane += 1
                        used_in_r.add(desired_lane)
                        comp_lanes[u] = desired_lane
                        if desired_lane > max_lane_used:
                            max_lane_used = desired_lane

                for u in comp:
                    ent_lane[u] = current_base_lane + comp_lanes[u]

                current_base_lane += max_lane_used + 1

            # Arrange unconnected entities at rank 0
            if unconnected_ents:
                for idx_u, u in enumerate(unconnected_ents):
                    ranks[u] = 0
                    ent_lane[u] = current_base_lane + idx_u

            rank_groups_final: Dict[int, List[str]] = defaultdict(list)
            for eid in entity_ids:
                rank_groups_final[ranks[eid]].append(eid)
            sorted_ranks = sorted(rank_groups_final.keys())
            all_lanes = sorted(list(set(ent_lane.values())))

            if direction == "horizontal":
                rank_gap_x = 90.0
                lane_gap_y = 80.0

                lane_max_above: Dict[int, float] = {}
                lane_max_below: Dict[int, float] = {}
                for lane in all_lanes:
                    ents_in_lane = [entity_extents[eid] for eid, l in ent_lane.items() if l == lane]
                    lane_max_above[lane] = max((ext[2] for ext in ents_in_lane), default=60.0)
                    lane_max_below[lane] = max((ext[3] for ext in ents_in_lane), default=60.0)

                lane_center_y: Dict[int, float] = {}
                cur_y = lane_max_above[all_lanes[0]]
                for idx, lane in enumerate(all_lanes):
                    if idx > 0:
                        prev_lane = all_lanes[idx - 1]
                        cur_y += lane_max_below[prev_lane] + lane_gap_y + lane_max_above[lane]
                    lane_center_y[lane] = cur_y

                center_x_map: Dict[str, float] = {}
                cur_x = 0.0
                for idx, r in enumerate(sorted_ranks):
                    ents_in_r = rank_groups_final[r]
                    max_left = max((entity_extents[eid][0] for eid in ents_in_r), default=90.0)
                    max_right = max((entity_extents[eid][1] for eid in ents_in_r), default=90.0)
                    if idx == 0:
                        cur_x = max_left
                    else:
                        cur_x += max_left
                    for eid in ents_in_r:
                        center_x_map[eid] = cur_x
                    cur_x += max_right + rank_gap_x

                center_y_map = {eid: lane_center_y[ent_lane[eid]] for eid in entity_ids}
            else:
                rank_gap_y = 80.0
                lane_gap_x = 90.0

                lane_max_left: Dict[int, float] = {}
                lane_max_right: Dict[int, float] = {}
                for lane in all_lanes:
                    ents_in_lane = [entity_extents[eid] for eid, l in ent_lane.items() if l == lane]
                    lane_max_left[lane] = max((ext[0] for ext in ents_in_lane), default=90.0)
                    lane_max_right[lane] = max((ext[1] for ext in ents_in_lane), default=90.0)

                lane_center_x: Dict[int, float] = {}
                cur_x = lane_max_left[all_lanes[0]]
                for idx, lane in enumerate(all_lanes):
                    if idx > 0:
                        prev_lane = all_lanes[idx - 1]
                        cur_x += lane_max_right[prev_lane] + lane_gap_x + lane_max_left[lane]
                    lane_center_x[lane] = cur_x

                center_y_map = {}
                cur_y = 0.0
                for idx, r in enumerate(sorted_ranks):
                    ents_in_r = rank_groups_final[r]
                    max_above = max((entity_extents[eid][2] for eid in ents_in_r), default=60.0)
                    max_below = max((entity_extents[eid][3] for eid in ents_in_r), default=60.0)
                    if idx == 0:
                        cur_y = max_above
                    else:
                        cur_y += max_above
                    for eid in ents_in_r:
                        center_y_map[eid] = cur_y
                    cur_y += max_below + rank_gap_y

                center_x_map = {eid: lane_center_x[ent_lane[eid]] for eid in entity_ids}

            return center_x_map, center_y_map

        def _place_ops_from_centers(
            ops_subset: List[str],
            cx_map: Dict[str, float],
            cy_map: Dict[str, float],
            out_positions: Dict[str, Tuple[float, float]],
        ) -> None:
            for name in ops_subset:
                c = clusters[name]
                cx = cx_map[name]
                cy = cy_map[name]

                out_positions[c.prim.name()] = (cx - c.w_prim / 2.0, cy - c.h_prim / 2.0)
                qname = c.prim.get_property("query_name") if hasattr(c.prim, "get_property") else None
                if qname:
                    out_positions[qname] = (cx - c.w_prim / 2.0, cy - c.h_prim / 2.0)

                if c.left_b:
                    cur_ly = cy - c.left_h / 2.0
                    left_right_edge = cx - c.w_prim / 2.0 - c.gap_x_side
                    for lb, (w_lb, h_lb) in zip(c.left_b, c.left_dims):
                        out_positions[lb.name()] = (left_right_edge - w_lb, cur_ly)
                        cur_ly += h_lb + c.intra_gap_y

                if c.right_b:
                    cur_ry = cy - c.right_h / 2.0
                    right_left_edge = cx + c.w_prim / 2.0 + c.gap_x_side
                    for rb, (w_rb, h_rb) in zip(c.right_b, c.right_dims):
                        out_positions[rb.name()] = (right_left_edge, cur_ry)
                        cur_ry += h_rb + c.intra_gap_y

                if c.above_b:
                    above_bottom_edge = cy - c.h_prim / 2.0 - c.gap_y_side
                    cur_ax = cx - c.above_w / 2.0
                    for ab, (w_ab, h_ab) in zip(c.above_b, c.above_dims):
                        out_positions[ab.name()] = (cur_ax, above_bottom_edge - h_ab)
                        cur_ax += w_ab + c.intra_gap_x

                if c.below_b:
                    below_top_edge = cy + c.h_prim / 2.0 + c.gap_y_side
                    cur_bx = cx - c.below_w / 2.0
                    for bb, (w_bb, h_bb) in zip(c.below_b, c.below_dims):
                        out_positions[bb.name()] = (cur_bx, below_top_edge)
                        cur_bx += w_bb + c.intra_gap_x

        # 4. Check for GroupBoxNode instances on the canvas
        group_nodes: List[GroupBoxNode] = [
            n for n in all_nodes
            if getattr(n, "type_", "") == "reporting.nodes.GroupBoxNode"
            or n.__class__.__name__ == "GroupBoxNode"
        ]

        new_positions: Dict[str, Tuple[float, float]] = {}

        if not group_nodes:
            op_extents = {
                name: (clusters[name].ext_left, clusters[name].ext_right, clusters[name].ext_above, clusters[name].ext_below)
                for name in valid_ops
            }
            cx_map, cy_map = _compute_entity_centers(
                valid_ops,
                op_extents,
                upstreams,
                downstreams,
                blue_edges,
                data_edges,
            )
            _place_ops_from_centers(valid_ops, cx_map, cy_map, new_positions)
        else:
            # Determine which operations belong to which GroupBoxNode before moving any nodes
            op_to_group_idx: Dict[str, int] = {}
            group_contained_views: List[Set[str]] = []
            group_areas: List[float] = []

            for g in group_nodes:
                if hasattr(g, "view") and g.view:
                    views_in_g = g.view.get_nodes(inc_intersects=False)
                    group_contained_views.append({getattr(v, "id", None) for v in views_in_g})
                    gw = float(getattr(g.view, "width", 380.0) or 380.0)
                    gh = float(getattr(g.view, "height", 250.0) or 250.0)
                    group_areas.append(gw * gh)
                else:
                    group_contained_views.append(set())
                    group_areas.append(380.0 * 250.0)

            groups_with_explicit_members: Set[int] = set()
            # First priority: check explicit member_node_names recorded on group nodes
            for g_idx, gnode in enumerate(group_nodes):
                explicit_names = gnode.get_property("member_node_names") if hasattr(gnode, "get_property") else None
                if explicit_names and isinstance(explicit_names, (list, tuple, set)) and len(explicit_names) > 0:
                    groups_with_explicit_members.add(g_idx)
                    exp_set = set(explicit_names)
                    for op_name in valid_ops:
                        if op_name in op_to_group_idx:
                            continue
                        pnode = primary_nodes[op_name]
                        qname = pnode.get_property("query_name") if hasattr(pnode, "get_property") else None
                        if op_name in exp_set or pnode.name() in exp_set or (qname and qname in exp_set):
                            op_to_group_idx[op_name] = g_idx

            for op_name in valid_ops:
                if op_name in op_to_group_idx:
                    continue
                pnode = primary_nodes[op_name]
                p_vid = getattr(getattr(pnode, "view", None), "id", None) or pnode.id
                candidate_g_indices = [
                    g_idx for g_idx, vset in enumerate(group_contained_views)
                    if p_vid in vset and g_idx not in groups_with_explicit_members
                ]
                if not candidate_g_indices:
                    # Fallback: check if any of this operation's table boxes is inside a group
                    op_boxes = in_boxes_by_op[op_name] + out_boxes_by_op[op_name]
                    for b in op_boxes:
                        b_vid = getattr(getattr(b, "view", None), "id", None) or b.id
                        for g_idx, vset in enumerate(group_contained_views):
                            if b_vid in vset and g_idx not in candidate_g_indices and g_idx not in groups_with_explicit_members:
                                candidate_g_indices.append(g_idx)

                if candidate_g_indices:
                    # If inside multiple overlapping groups, assign to the most specific (smallest-area) group
                    best_g_idx = min(candidate_g_indices, key=lambda idx: (group_areas[idx], idx))
                    op_to_group_idx[op_name] = best_g_idx

            ops_by_group: Dict[int, List[str]] = defaultdict(list)
            ungrouped_ops: List[str] = []
            for op_name in valid_ops:
                if op_name in op_to_group_idx:
                    ops_by_group[op_to_group_idx[op_name]].append(op_name)
                else:
                    ungrouped_ops.append(op_name)

            # 4a. Layout internal nodes within each non-empty GroupBoxNode using the traditional rules,
            #     and calculate the exact wrapped group box size (W_g, H_g) and member node offsets
            group_layout_info: Dict[int, Dict[str, Any]] = {}
            for g_idx, gnode in enumerate(group_nodes):
                g_ops = ops_by_group.get(g_idx, [])
                if not g_ops:
                    gw = float(getattr(gnode.view, "width", 380.0) if hasattr(gnode, "view") and gnode.view else 380.0)
                    gh = float(getattr(gnode.view, "height", 250.0) if hasattr(gnode, "view") and gnode.view else 250.0)
                    group_layout_info[g_idx] = {
                        "empty": True,
                        "width": gw,
                        "height": gh,
                        "member_nodes": [],
                        "offsets_from_center": {},
                    }
                    continue

                g_ops_set = set(g_ops)
                g_extents = {
                    name: (clusters[name].ext_left, clusters[name].ext_right, clusters[name].ext_above, clusters[name].ext_below)
                    for name in g_ops
                }
                g_ups = {u: {p for p in upstreams[u] if p in g_ops_set} for u in g_ops}
                g_downs = {u: {d for d in downstreams[u] if d in g_ops_set} for u in g_ops}
                g_blue = {(u, v) for (u, v) in blue_edges if u in g_ops_set and v in g_ops_set}
                g_data = {(u, v) for (u, v) in data_edges if u in g_ops_set and v in g_ops_set}

                local_cx, local_cy = _compute_entity_centers(
                    g_ops,
                    g_extents,
                    g_ups,
                    g_downs,
                    g_blue,
                    g_data,
                )
                local_pos: Dict[str, Tuple[float, float]] = {}
                _place_ops_from_centers(g_ops, local_cx, local_cy, local_pos)

                member_nodes: List[BaseNode] = []
                for op_name in g_ops:
                    if primary_nodes[op_name] not in member_nodes:
                        member_nodes.append(primary_nodes[op_name])
                    for b in in_boxes_by_op[op_name] + out_boxes_by_op[op_name]:
                        if b not in member_nodes:
                            member_nodes.append(b)

                # Temporarily move member_nodes to local_pos so calc_backdrop_size computes exact geometry
                for mn in member_nodes:
                    if mn.name() in local_pos:
                        lx, ly = local_pos[mn.name()]
                        mn.set_pos(lx, ly)

                bd_size = gnode.view.calc_backdrop_size([mn.view for mn in member_nodes if hasattr(mn, "view") and mn.view])
                gw = float(bd_size["width"])
                gh = float(bd_size["height"])
                gx_local = float(bd_size["pos"][0])
                gy_local = float(bd_size["pos"][1])
                g_center_local_x = gx_local + gw / 2.0
                g_center_local_y = gy_local + gh / 2.0

                offsets_from_center: Dict[str, Tuple[float, float]] = {}
                for k, (lx, ly) in local_pos.items():
                    offsets_from_center[k] = (lx - g_center_local_x, ly - g_center_local_y)

                group_layout_info[g_idx] = {
                    "empty": False,
                    "width": gw,
                    "height": gh,
                    "member_nodes": member_nodes,
                    "offsets_from_center": offsets_from_center,
                }

            # 4b. Build macro-level graph where each GroupBoxNode is a single entity and each ungrouped op is an entity
            macro_ids: List[str] = []
            macro_extents: Dict[str, Tuple[float, float, float, float]] = {}
            op_to_macro_id: Dict[str, str] = {}

            for g_idx in range(len(group_nodes)):
                info = group_layout_info[g_idx]
                if not info["empty"]:
                    gid = f"__group__{g_idx}"
                    macro_ids.append(gid)
                    half_w = info["width"] / 2.0
                    half_h = info["height"] / 2.0
                    macro_extents[gid] = (half_w, half_w, half_h, half_h)
                    for op_name in ops_by_group[g_idx]:
                        op_to_macro_id[op_name] = gid

            for op_name in ungrouped_ops:
                macro_ids.append(op_name)
                c = clusters[op_name]
                macro_extents[op_name] = (c.ext_left, c.ext_right, c.ext_above, c.ext_below)
                op_to_macro_id[op_name] = op_name

            for g_idx in range(len(group_nodes)):
                info = group_layout_info[g_idx]
                if info["empty"]:
                    gid = f"__group__{g_idx}"
                    macro_ids.append(gid)
                    half_w = info["width"] / 2.0
                    half_h = info["height"] / 2.0
                    macro_extents[gid] = (half_w, half_w, half_h, half_h)

            macro_upstreams: Dict[str, Set[str]] = {mid: set() for mid in macro_ids}
            macro_downstreams: Dict[str, Set[str]] = {mid: set() for mid in macro_ids}
            macro_blue_edges: Set[Tuple[str, str]] = set()
            macro_data_edges: Set[Tuple[str, str]] = set()

            for u, v in blue_edges:
                mu = op_to_macro_id.get(u)
                mv = op_to_macro_id.get(v)
                if mu and mv and mu != mv:
                    macro_upstreams[mv].add(mu)
                    macro_downstreams[mu].add(mv)
                    macro_blue_edges.add((mu, mv))

            for u, v in data_edges:
                mu = op_to_macro_id.get(u)
                mv = op_to_macro_id.get(v)
                if mu and mv and mu != mv:
                    macro_upstreams[mv].add(mu)
                    macro_downstreams[mu].add(mv)
                    macro_data_edges.add((mu, mv))

            macro_cx, macro_cy = _compute_entity_centers(
                macro_ids,
                macro_extents,
                macro_upstreams,
                macro_downstreams,
                macro_blue_edges,
                macro_data_edges,
            )

            # Place ungrouped operations
            if ungrouped_ops:
                _place_ops_from_centers(ungrouped_ops, macro_cx, macro_cy, new_positions)

            # Place grouped operations according to their group entity's macro center
            for g_idx, gnode in enumerate(group_nodes):
                gid = f"__group__{g_idx}"
                info = group_layout_info[g_idx]
                gcx = macro_cx[gid]
                gcy = macro_cy[gid]
                if not info["empty"]:
                    for k, (ox, oy) in info["offsets_from_center"].items():
                        new_positions[k] = (gcx + ox, gcy + oy)

        # Apply new positions to all matching nodes in the graph
        for node_name, (nx, ny) in new_positions.items():
            node = nodes_by_name.get(node_name)
            if node:
                node.set_pos(nx, ny)

        # Resize and reposition each GroupBoxNode around its newly arranged internal nodes
        if group_nodes:
            for g_idx, gnode in enumerate(group_nodes):
                gid = f"__group__{g_idx}"
                info = group_layout_info[g_idx]
                if not info["empty"] and info["member_nodes"]:
                    try:
                        gnode.wrap_nodes(info["member_nodes"])
                        if hasattr(gnode, "view") and hasattr(gnode.view, "_sync_header_items"):
                            gnode.view._sync_header_items()
                    except Exception as e:
                        logger.debug(f"Could not wrap group box {gnode.name()} during auto_layout: {e}")
                else:
                    gcx = macro_cx[gid]
                    gcy = macro_cy[gid]
                    gw = info["width"]
                    gh = info["height"]
                    gnode.set_pos(gcx - gw / 2.0, gcy - gh / 2.0)

        # Ensure all existing non-group nodes are tracked in new_positions
        for node in all_nodes:
            if getattr(node, "type_", "") == "reporting.nodes.GroupBoxNode" or node.__class__.__name__ == "GroupBoxNode":
                continue
            if node.name() not in new_positions:
                new_positions[node.name()] = (node.pos()[0], node.pos()[1])

        # Redraw pipes
        for item in graph.viewer().scene().items():
            if hasattr(item, "reset"):
                try:
                    item.reset()
                except Exception:
                    pass

        return new_positions


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
from typing import Dict, List, Optional, Set, Tuple

from NodeGraphQt import BaseNode, NodeGraph
from reporting_app.core.models import QueryInfo
from reporting_app.presentation.flow_editor.nodes import ImportCsvNode, QueryNode, TableBoxNode

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

        # Clear existing nodes
        graph.delete_nodes(graph.all_nodes())

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

        Follows a query-centric spine layout:
        - Horizontal: Primary operations (Import Files, Queries) form a clean horizontal center line.
          Input tables sit directly ABOVE each operation (side-by-side if multiple).
          Output tables sit directly BELOW each operation (side-by-side if multiple).
          Parallel branches run in dynamic, collision-free horizontal tracks below the main spine.
        - Vertical: Primary operations form a clean vertical center column.
          Input tables sit directly to the LEFT of each operation (stacked vertically if multiple).
          Output tables sit directly to the RIGHT of each operation (stacked vertically if multiple).
          Parallel branches run in dynamic, collision-free vertical columns beside the main column.
        - Import Files nodes are placed as Rank 0 operations at the start of the pipeline in-line with queries.
        """
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

        valid_ops = [op_name for op_name in all_ops if op_name in primary_nodes]
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

        # 2. Build dependency graph (data provenance + execution flow)
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

        for inode in import_nodes:
            run_in = inode.get_input("run_in")
            if run_in:
                for port in run_in.connected_ports():
                    up_node = port.node()
                    up_name = up_node.get_property("query_name") if hasattr(up_node, "get_property") else up_node.name()
                    if up_name in all_producers and up_name != inode.name():
                        upstreams[inode.name()].add(up_name)

        for q in queries:
            if q.name not in valid_ops:
                continue
            for in_t in q.input_tables:
                prod = table_producers.get(in_t)
                if prod and prod in all_producers and prod != q.name:
                    upstreams[q.name].add(prod)

            qnode = primary_nodes.get(q.name)
            if qnode:
                run_in = qnode.get_input("run_in")
                if run_in:
                    for port in run_in.connected_ports():
                        up_node = port.node()
                        up_name = up_node.get_property("query_name") if hasattr(up_node, "get_property") else up_node.name()
                        if up_name in all_producers and up_name != q.name:
                            upstreams[q.name].add(up_name)

        # 3. Compute topological ranks for each operation
        ranks: Dict[str, int] = {op_name: 0 for op_name in valid_ops}
        for _ in range(len(valid_ops)):
            changed = False
            for op_name in valid_ops:
                ups = upstreams[op_name]
                if ups:
                    max_up = max(ranks.get(u, 0) for u in ups)
                    if ranks[op_name] <= max_up:
                        ranks[op_name] = max_up + 1
                        changed = True
            if not changed:
                break

        # If import nodes exist, place them as rank 0 operations at the start of the pipeline
        if import_nodes:
            max_imp_rank = max((ranks[inode.name()] for inode in import_nodes if inode.name() in ranks), default=0)
            for q in queries:
                if q.name in ranks and ranks[q.name] <= max_imp_rank:
                    ranks[q.name] += (max_imp_rank + 1)
            for _ in range(len(queries)):
                changed = False
                for q in queries:
                    if q.name not in ranks:
                        continue
                    ups = upstreams[q.name]
                    if ups:
                        max_up = max(ranks.get(u, 0) for u in ups)
                        if ranks[q.name] <= max_up:
                            ranks[q.name] = max_up + 1
                            changed = True
                if not changed:
                    break

        # 4. Build downstream consumer mappings to assign branch levels / lanes
        downstreams: Dict[str, List[str]] = defaultdict(list)
        for op_name in valid_ops:
            for up in upstreams[op_name]:
                downstreams[up].append(op_name)

        branch_levels: Dict[str, int] = {op_name: 0 for op_name in valid_ops}
        for target_name in valid_ops:
            prods = [u for u in valid_ops if target_name in downstreams[u]]
            if len(prods) > 1:
                for b_idx, p_name in enumerate(prods):
                    branch_levels[p_name] = max(branch_levels.get(p_name, 0), b_idx)

        for _ in range(len(valid_ops)):
            changed = False
            for op_name in valid_ops:
                b = branch_levels.get(op_name, 0)
                if b > 0:
                    for up in upstreams[op_name]:
                        if branch_levels.get(up, 0) < b:
                            branch_levels[up] = b
                            changed = True
            if not changed:
                break

        rank_groups: Dict[int, List[str]] = defaultdict(list)
        for op_name in valid_ops:
            rank_groups[ranks[op_name]].append(op_name)
        sorted_ranks = sorted(rank_groups.keys())

        # Ensure unique lane for each operation in the same rank
        op_lane: Dict[str, int] = {}
        for r in sorted_ranks:
            ops_in_r = rank_groups[r]
            ops_in_r.sort(key=lambda name: (branch_levels.get(name, 0), name))
            used_lanes = set()
            for name in ops_in_r:
                lane = branch_levels.get(name, 0)
                while lane in used_lanes:
                    lane += 1
                used_lanes.add(lane)
                op_lane[name] = lane

        # 5. Measure cluster dimensions dynamically
        class ClusterMetrics:
            def __init__(self, name: str, prim: BaseNode, in_b: List[BaseNode], out_b: List[BaseNode]):
                self.name = name
                self.prim = prim
                self.in_b = in_b
                self.out_b = out_b
                self.w_prim, self.h_prim = get_node_dims(prim, 180.0, 70.0)
                self.in_dims = [get_node_dims(b, 200.0, 60.0) for b in in_b]
                self.out_dims = [get_node_dims(b, 200.0, 60.0) for b in out_b]

                # Horizontal metrics (inputs above, outputs below)
                gap_x = 20.0
                gap_y_above = 50.0
                gap_y_below = 50.0

                if self.in_dims:
                    self.h_in_w = sum(d[0] for d in self.in_dims) + (len(self.in_dims) - 1) * gap_x
                    self.h_in_h = max((d[1] for d in self.in_dims), default=0.0)
                else:
                    self.h_in_w, self.h_in_h = 0.0, 0.0

                if self.out_dims:
                    self.h_out_w = sum(d[0] for d in self.out_dims) + (len(self.out_dims) - 1) * gap_x
                    self.h_out_h = max((d[1] for d in self.out_dims), default=0.0)
                else:
                    self.h_out_w, self.h_out_h = 0.0, 0.0

                self.h_w = max(self.w_prim, self.h_in_w, self.h_out_w)
                self.h_ext_above = self.h_prim / 2.0 + (gap_y_above + self.h_in_h if self.in_b else 0.0)
                self.h_ext_below = self.h_prim / 2.0 + (gap_y_below + self.h_out_h if self.out_b else 0.0)

                # Vertical metrics (inputs left, outputs right)
                v_gap_y = 20.0
                gap_x_left = 60.0
                gap_x_right = 60.0

                if self.in_dims:
                    self.v_in_w = max((d[0] for d in self.in_dims), default=0.0)
                    self.v_in_h = sum(d[1] for d in self.in_dims) + (len(self.in_dims) - 1) * v_gap_y
                else:
                    self.v_in_w, self.v_in_h = 0.0, 0.0

                if self.out_dims:
                    self.v_out_w = max((d[0] for d in self.out_dims), default=0.0)
                    self.v_out_h = sum(d[1] for d in self.out_dims) + (len(self.out_dims) - 1) * v_gap_y
                else:
                    self.v_out_w, self.v_out_h = 0.0, 0.0

                self.v_h = max(self.h_prim, self.v_in_h, self.v_out_h)
                self.v_ext_left = self.w_prim / 2.0 + (gap_x_left + self.v_in_w if self.in_b else 0.0)
                self.v_ext_right = self.w_prim / 2.0 + (gap_x_right + self.v_out_w if self.out_b else 0.0)

        clusters: Dict[str, ClusterMetrics] = {
            name: ClusterMetrics(name, primary_nodes[name], in_boxes_by_op[name], out_boxes_by_op[name])
            for name in valid_ops
        }

        new_positions: Dict[str, Tuple[float, float]] = {}

        if direction == "horizontal":
            intra_gap_x = 20.0
            gap_above = 50.0
            gap_below = 50.0
            rank_gap_x = 100.0
            lane_gap_y = 80.0

            all_lanes = sorted(list(set(op_lane.values())))

            lane_max_above: Dict[int, float] = {}
            lane_max_below: Dict[int, float] = {}
            for lane in all_lanes:
                ops_in_lane = [clusters[name] for name, l in op_lane.items() if l == lane]
                lane_max_above[lane] = max(c.h_ext_above for c in ops_in_lane)
                lane_max_below[lane] = max(c.h_ext_below for c in ops_in_lane)

            lane_center_y: Dict[int, float] = {}
            cur_y = lane_max_above[all_lanes[0]]
            for idx, lane in enumerate(all_lanes):
                if idx > 0:
                    prev_lane = all_lanes[idx - 1]
                    cur_y += lane_max_below[prev_lane] + lane_gap_y + lane_max_above[lane]
                lane_center_y[lane] = cur_y

            rank_start_x = 0.0
            op_center_x: Dict[str, float] = {}
            for r in sorted_ranks:
                ops_in_r = [clusters[name] for name in rank_groups[r]]
                max_rank_w = max(c.h_w for c in ops_in_r)
                for c in ops_in_r:
                    op_center_x[c.name] = rank_start_x + max_rank_w / 2.0
                rank_start_x += max_rank_w + rank_gap_x

            for name, c in clusters.items():
                cx = op_center_x[name]
                cy = lane_center_y[op_lane[name]]

                # Primary node centered at (cx, cy)
                new_positions[c.prim.name()] = (cx - c.w_prim / 2.0, cy - c.h_prim / 2.0)
                qname = c.prim.get_property("query_name") if hasattr(c.prim, "get_property") else None
                if qname:
                    new_positions[qname] = (cx - c.w_prim / 2.0, cy - c.h_prim / 2.0)

                # Input boxes: directly above primary node, centered horizontally
                if c.in_b:
                    in_bottom_y = cy - c.h_prim / 2.0 - gap_above
                    cur_in_x = cx - c.h_in_w / 2.0
                    for ib, (w_ib, h_ib) in zip(c.in_b, c.in_dims):
                        new_positions[ib.name()] = (cur_in_x, in_bottom_y - h_ib)
                        cur_in_x += w_ib + intra_gap_x

                # Output boxes: directly below primary node, centered horizontally
                if c.out_b:
                    out_top_y = cy + c.h_prim / 2.0 + gap_below
                    cur_out_x = cx - c.h_out_w / 2.0
                    for ob, (w_ob, h_ob) in zip(c.out_b, c.out_dims):
                        new_positions[ob.name()] = (cur_out_x, out_top_y)
                        cur_out_x += w_ob + intra_gap_x

        else:
            intra_gap_y = 20.0
            gap_left = 60.0
            gap_right = 60.0
            rank_gap_y = 80.0
            lane_gap_x = 80.0

            all_lanes = sorted(list(set(op_lane.values())))

            lane_max_left: Dict[int, float] = {}
            lane_max_right: Dict[int, float] = {}
            for lane in all_lanes:
                ops_in_lane = [clusters[name] for name, l in op_lane.items() if l == lane]
                lane_max_left[lane] = max(c.v_ext_left for c in ops_in_lane)
                lane_max_right[lane] = max(c.v_ext_right for c in ops_in_lane)

            lane_center_x: Dict[int, float] = {}
            cur_x = lane_max_left[all_lanes[0]]
            for idx, lane in enumerate(all_lanes):
                if idx > 0:
                    prev_lane = all_lanes[idx - 1]
                    cur_x += lane_max_right[prev_lane] + lane_gap_x + lane_max_left[lane]
                lane_center_x[lane] = cur_x

            rank_start_y = 0.0
            op_center_y: Dict[str, float] = {}
            for r in sorted_ranks:
                ops_in_r = [clusters[name] for name in rank_groups[r]]
                max_rank_h = max(c.v_h for c in ops_in_r)
                for c in ops_in_r:
                    op_center_y[c.name] = rank_start_y + max_rank_h / 2.0
                rank_start_y += max_rank_h + rank_gap_y

            for name, c in clusters.items():
                cx = lane_center_x[op_lane[name]]
                cy = op_center_y[name]

                # Primary node centered at (cx, cy)
                new_positions[c.prim.name()] = (cx - c.w_prim / 2.0, cy - c.h_prim / 2.0)
                qname = c.prim.get_property("query_name") if hasattr(c.prim, "get_property") else None
                if qname:
                    new_positions[qname] = (cx - c.w_prim / 2.0, cy - c.h_prim / 2.0)

                # Input boxes: to the left of primary node, vertically centered
                if c.in_b:
                    cur_in_y = cy - c.v_in_h / 2.0
                    in_right_x = cx - c.w_prim / 2.0 - gap_left
                    for ib, (w_ib, h_ib) in zip(c.in_b, c.in_dims):
                        new_positions[ib.name()] = (in_right_x - w_ib, cur_in_y)
                        cur_in_y += h_ib + intra_gap_y

                # Output boxes: to the right of primary node, vertically centered
                if c.out_b:
                    cur_out_y = cy - c.v_out_h / 2.0
                    out_left_x = cx + c.w_prim / 2.0 + gap_right
                    for ob, (w_ob, h_ob) in zip(c.out_b, c.out_dims):
                        new_positions[ob.name()] = (out_left_x, cur_out_y)
                        cur_out_y += h_ob + intra_gap_y

        # Apply new positions to all matching nodes in the graph
        for node_name, (nx, ny) in new_positions.items():
            node = nodes_by_name.get(node_name)
            if node:
                node.set_pos(nx, ny)

        # Ensure all existing nodes are tracked in new_positions
        for node in all_nodes:
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

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
        active_query_names.add("Import csv")
        for idx in range(len(import_csv_data) + 5):
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
            imp_name = imp_group.get("node_name", "Import csv" if idx == 0 else f"Import csv {idx}")
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

        # 4. Create ImportCsvNode(s) if import_csv_data is provided
        import_nodes: Dict[str, ImportCsvNode] = {}
        for idx, imp_group in enumerate(import_csv_data):
            imp_name = imp_group.get("node_name", "Import csv" if idx == 0 else f"Import csv {idx}")
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

        Ensures all boxes (queries, tables, CSV imports) are spaced apart cleanly according to dependencies.
        """
        # 1. Map nodes by identifier
        all_nodes = graph.all_nodes()
        nodes_by_name = {n.name(): n for n in all_nodes}

        import_nodes = [
            n for n in all_nodes
            if getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
            or n.__class__.__name__ == "ImportCsvNode"
        ]

        if not queries and not import_nodes:
            return {}

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

        # 2. Build dependency graph (data provenance + execution flow)
        table_producers: Dict[str, str] = {}
        for inode in import_nodes:
            out_tables = inode.get_output_tables() if hasattr(inode, "get_output_tables") else []
            for t in out_tables:
                table_producers[t] = inode.name()

        for q in queries:
            for out_t in q.output_tables:
                table_producers[out_t] = q.name

        query_names = {q.name for q in queries}
        all_producers = set(query_names) | {inode.name() for inode in import_nodes}
        upstreams: Dict[str, Set[str]] = {q.name: set() for q in queries}

        for q in queries:
            # Data dependencies
            for in_t in q.input_tables:
                prod = table_producers.get(in_t)
                if prod and prod in all_producers and prod != q.name:
                    upstreams[q.name].add(prod)

            # Direct run connection dependencies
            qnode = nodes_by_name.get(q.name)
            if qnode:
                run_in = qnode.get_input("run_in")
                if run_in:
                    for port in run_in.connected_ports():
                        up_node = port.node()
                        up_name = up_node.get_property("query_name") if hasattr(up_node, "get_property") else up_node.name()
                        if up_name in all_producers and up_name != q.name:
                            upstreams[q.name].add(up_name)

        # 3. Compute topological ranks for each query (0, 1, 2...)
        ranks: Dict[str, int] = {q.name: 0 for q in queries}
        for _ in range(len(queries)):
            changed = False
            for q in queries:
                ups = upstreams[q.name]
                if ups:
                    max_up = max(ranks.get(u, 0) for u in ups)
                    if ranks[q.name] <= max_up:
                        ranks[q.name] = max_up + 1
                        changed = True
            if not changed:
                break

        # Group queries by rank
        rank_groups: Dict[int, List[QueryInfo]] = defaultdict(list)
        for q in queries:
            rank_groups[ranks[q.name]].append(q)
        sorted_ranks = sorted(rank_groups.keys())

        # Build downstream consumer mappings to assign branch levels
        downstreams: Dict[str, List[str]] = defaultdict(list)
        for q in queries:
            for up in upstreams[q.name]:
                downstreams[up].append(q.name)

        # Compute branch level (0 = main line, 1 = first branch below, 2 = second branch below, etc.)
        # If a downstream node has multiple upstreams, the first upstream stays on branch 0,
        # subsequent upstreams get branch levels 1, 2...
        branch_levels: Dict[str, int] = {q.name: 0 for q in queries}
        for inode in import_nodes:
            branch_levels[inode.name()] = 0

        for target_name in all_producers:
            # Find all nodes that feed directly into target_name
            prods = [u for u in all_producers if target_name in downstreams[u]]
            if len(prods) > 1:
                # Sort for deterministic ordering (e.g. imports or earlier queries first)
                for b_idx, p_name in enumerate(prods):
                    branch_levels[p_name] = max(branch_levels.get(p_name, 0), b_idx)

        # Propagate branch levels upstream if a branch node has its own upstreams
        for _ in range(len(queries)):
            changed = False
            for q in queries:
                b = branch_levels.get(q.name, 0)
                if b > 0:
                    for up in upstreams[q.name]:
                        if branch_levels.get(up, 0) < b:
                            branch_levels[up] = b
                            changed = True
            if not changed:
                break

        new_positions: Dict[str, Tuple[float, float]] = {}

        if direction == "horizontal":
            # Left to Right:
            # Ranks advance along X; branch levels offset along Y below the main row.
            # - Top: Output tables / output CSV
            # - Middle: Import CSV / query nodes
            # - Bottom: Input tables / input CSV tables
            col_gap_x = 100.0
            intra_gap_y = 60.0
            intra_gap_x = 20.0
            cluster_gap_x = 80.0

            # First determine maximum heights for each tier across the entire graph
            max_out_h = 60.0
            max_node_h = 70.0

            for inode in import_nodes:
                out_box = nodes_by_name.get(f"{inode.name()} [Out]")
                w_inode, h_inode = get_node_dims(inode, 180.0, 70.0)
                w_out, h_out = get_node_dims(out_box, 200.0, 60.0)
                max_node_h = max(max_node_h, h_inode)
                if out_box:
                    max_out_h = max(max_out_h, h_out)

            for q in queries:
                q_node = nodes_by_name.get(q.name)
                out_box = nodes_by_name.get(f"{q.name} [Out]")
                csv_box = nodes_by_name.get(f"{q.name} [CSV]")
                w_q, h_q = get_node_dims(q_node, 180.0, 70.0)
                w_out, h_out = get_node_dims(out_box, 200.0, 60.0)
                w_csv, h_csv = get_node_dims(csv_box, 180.0, 60.0)
                max_node_h = max(max_node_h, h_q)
                if out_box and csv_box:
                    max_out_h = max(max_out_h, max(h_out, h_csv))
                elif out_box:
                    max_out_h = max(max_out_h, h_out)
                elif csv_box:
                    max_out_h = max(max_out_h, h_csv)

            # Base horizontal alignment lines (main level Y=0):
            base_out_y = 0.0
            base_node_y = base_out_y + max_out_h + intra_gap_y
            base_in_y = base_node_y + max_node_h + intra_gap_y

            # Tier height between main flow and lower branch levels
            tier_height = (base_in_y - base_out_y) + 140.0 + 80.0

            # X-advancing layout
            cur_x = 0.0

            # Place import CSV nodes at rank 0
            if import_nodes:
                max_imp_w = 0.0
                rank_start_x = cur_x
                for inode in import_nodes:
                    in_box = nodes_by_name.get(f"{inode.name()} [In]")
                    out_box = nodes_by_name.get(f"{inode.name()} [Out]")

                    w_in, h_in = get_node_dims(in_box, 200.0, 60.0)
                    w_inode, h_inode = get_node_dims(inode, 180.0, 70.0)
                    w_out, h_out = get_node_dims(out_box, 200.0, 60.0)

                    in_w_span = w_in if in_box else 0.0
                    out_w_span = w_out if out_box else 0.0

                    b_level = branch_levels.get(inode.name(), 0)
                    y_offset = b_level * tier_height

                    in_x = rank_start_x
                    node_x = in_x + (in_w_span + intra_gap_x if in_w_span > 0 else 0.0)
                    out_x = node_x + w_inode + intra_gap_x

                    # 1. Top & Right: Output tables
                    if out_box:
                        new_positions[out_box.name()] = (out_x, base_out_y + y_offset)

                    # 2. Middle: Node
                    new_positions[inode.name()] = (node_x, base_node_y + y_offset)

                    # 3. Bottom & Left: Input tables
                    if in_box:
                        new_positions[in_box.name()] = (in_x, base_in_y + y_offset)

                    cluster_w = (node_x - rank_start_x) + w_inode + (intra_gap_x + out_w_span if out_w_span > 0 else 0.0)
                    max_imp_w = max(max_imp_w, cluster_w)

                cur_x += max_imp_w + col_gap_x

            # Place query nodes grouped by rank
            for r in sorted_ranks:
                queries_in_rank = rank_groups[r]
                max_rank_w = 0.0
                rank_start_x = cur_x

                for q in queries_in_rank:
                    in_box = nodes_by_name.get(f"{q.name} [In]")
                    conv_box = nodes_by_name.get(f"{q.name} [Convergence]")
                    q_node = nodes_by_name.get(q.name)
                    out_box = nodes_by_name.get(f"{q.name} [Out]")
                    csv_box = nodes_by_name.get(f"{q.name} [CSV]")

                    w_in, h_in = get_node_dims(in_box, 200.0, 60.0)
                    w_conv, h_conv = get_node_dims(conv_box, 200.0, 60.0)
                    w_q, h_q = get_node_dims(q_node, 180.0, 70.0)
                    w_out, h_out = get_node_dims(out_box, 200.0, 60.0)
                    w_csv, h_csv = get_node_dims(csv_box, 180.0, 60.0)

                    # Input section (bottom-left)
                    has_in = bool(in_box)
                    has_conv = bool(conv_box)
                    if has_in and has_conv:
                        w_in_sec = w_in + intra_gap_x + w_conv
                    elif has_in:
                        w_in_sec = w_in
                    elif has_conv:
                        w_in_sec = w_conv
                    else:
                        w_in_sec = 0.0

                    # Output section (top-right)
                    has_out = bool(out_box)
                    has_csv = bool(csv_box)
                    if has_out and has_csv:
                        w_out_sec = w_out + intra_gap_x + w_csv
                    elif has_out:
                        w_out_sec = w_out
                    elif has_csv:
                        w_out_sec = w_csv
                    else:
                        w_out_sec = 0.0

                    # Stagger horizontally: Inputs (left) -> Query Node (middle) -> Outputs (right)
                    b_level = branch_levels.get(q.name, 0)
                    y_offset = b_level * tier_height

                    in_x = rank_start_x
                    node_x = in_x + (w_in_sec + intra_gap_x if w_in_sec > 0 else 0.0)
                    out_x = node_x + w_q + intra_gap_x

                    # 1. Top & Right: Output tables / output CSV
                    if has_out and has_csv:
                        new_positions[out_box.name()] = (out_x, base_out_y + y_offset)
                        new_positions[csv_box.name()] = (out_x + w_out + intra_gap_x, base_out_y + y_offset)
                    elif has_out:
                        new_positions[out_box.name()] = (out_x, base_out_y + y_offset)
                    elif has_csv:
                        new_positions[csv_box.name()] = (out_x, base_out_y + y_offset)

                    # 2. Middle: Query node
                    if q_node:
                        new_positions[q.name] = (node_x, base_node_y + y_offset)

                    # 3. Bottom & Left: Input tables / convergence tables
                    if has_in and has_conv:
                        new_positions[in_box.name()] = (in_x, base_in_y + y_offset)
                        new_positions[conv_box.name()] = (in_x + w_in + intra_gap_x, base_in_y + y_offset)
                    elif has_in:
                        new_positions[in_box.name()] = (in_x, base_in_y + y_offset)
                    elif has_conv:
                        new_positions[conv_box.name()] = (in_x, base_in_y + y_offset)

                    cluster_w = (node_x - rank_start_x) + w_q + (intra_gap_x + w_out_sec if w_out_sec > 0 else 0.0)
                    max_rank_w = max(max_rank_w, cluster_w)

                cur_x = rank_start_x + max_rank_w + col_gap_x

        else:
            # Top to Bottom (Vertical):
            # Ranks advance along Y; branches are placed to the left (negative X).
            cur_y = 0.0
            intra_gap_y = 40.0
            intra_gap_x = 20.0
            rank_gap_y = 80.0
            tier_width = 500.0  # Cluster width spacing for side branches

            # Place import CSV nodes first
            if import_nodes:
                max_imp_h = 0.0
                for inode in import_nodes:
                    in_box = nodes_by_name.get(f"{inode.name()} [In]")
                    out_box = nodes_by_name.get(f"{inode.name()} [Out]")

                    w_in, h_in = get_node_dims(in_box, 200.0, 60.0)
                    w_inode, h_inode = get_node_dims(inode, 180.0, 70.0)
                    w_out, h_out = get_node_dims(out_box, 200.0, 60.0)

                    cluster_w = max(w_in, w_inode, w_out)
                    b_level = branch_levels.get(inode.name(), 0)
                    branch_x_offset = -b_level * tier_width
                    cluster_x = branch_x_offset
                    node_y = cur_y

                    # 1. Input table at Top
                    if in_box:
                        new_positions[in_box.name()] = (cluster_x + max(0.0, (cluster_w - w_in) / 2.0), node_y)
                        node_y += h_in + intra_gap_y

                    # 2. Import CSV node in Middle
                    new_positions[inode.name()] = (cluster_x + max(0.0, (cluster_w - w_inode) / 2.0), node_y)
                    node_y += h_inode + intra_gap_y

                    # 3. Output table at Bottom
                    if out_box:
                        new_positions[out_box.name()] = (cluster_x + max(0.0, (cluster_w - w_out) / 2.0), node_y)
                        node_y += h_out + intra_gap_y

                    total_h = node_y - cur_y
                    max_imp_h = max(max_imp_h, total_h)

                cur_y += max_imp_h + rank_gap_y

            for r in sorted_ranks:
                queries_in_rank = rank_groups[r]
                max_rank_h = 0.0
                rank_start_y = cur_y

                for q in queries_in_rank:
                    in_box = nodes_by_name.get(f"{q.name} [In]")
                    conv_box = nodes_by_name.get(f"{q.name} [Convergence]")
                    q_node = nodes_by_name.get(q.name)
                    out_box = nodes_by_name.get(f"{q.name} [Out]")
                    csv_box = nodes_by_name.get(f"{q.name} [CSV]")

                    w_in, h_in = get_node_dims(in_box, 200.0, 60.0)
                    w_conv, h_conv = get_node_dims(conv_box, 200.0, 60.0)
                    w_q, h_q = get_node_dims(q_node, 180.0, 70.0)
                    w_out, h_out = get_node_dims(out_box, 200.0, 60.0)
                    w_csv, h_csv = get_node_dims(csv_box, 180.0, 60.0)

                    # Input section
                    has_in = bool(in_box)
                    has_conv = bool(conv_box)
                    if has_in and has_conv:
                        w_in_sec = w_in + intra_gap_x + w_conv
                        h_in_sec = max(h_in, h_conv)
                    elif has_in:
                        w_in_sec, h_in_sec = w_in, h_in
                    elif has_conv:
                        w_in_sec, h_in_sec = w_conv, h_conv
                    else:
                        w_in_sec, h_in_sec = 0.0, 0.0

                    # Output section
                    has_out = bool(out_box)
                    has_csv = bool(csv_box)
                    if has_out and has_csv:
                        w_out_sec = w_out + intra_gap_x + w_csv
                        h_out_sec = max(h_out, h_csv)
                    elif has_out:
                        w_out_sec, h_out_sec = w_out, h_out
                    elif has_csv:
                        w_out_sec, h_out_sec = w_csv, h_csv
                    else:
                        w_out_sec, h_out_sec = 0.0, 0.0

                    cluster_w = max(w_in_sec, w_q, w_out_sec)
                    b_level = branch_levels.get(q.name, 0)
                    branch_x_offset = -b_level * tier_width
                    cluster_x = branch_x_offset
                    node_y = rank_start_y

                    # 1. Top: Input section
                    if has_in and has_conv:
                        ix_start = cluster_x + max(0.0, (cluster_w - w_in_sec) / 2.0)
                        new_positions[in_box.name()] = (ix_start, node_y)
                        new_positions[conv_box.name()] = (ix_start + w_in + intra_gap_x, node_y)
                        node_y += h_in_sec + intra_gap_y
                    elif has_in:
                        new_positions[in_box.name()] = (cluster_x + max(0.0, (cluster_w - w_in) / 2.0), node_y)
                        node_y += h_in_sec + intra_gap_y
                    elif has_conv:
                        new_positions[conv_box.name()] = (cluster_x + max(0.0, (cluster_w - w_conv) / 2.0), node_y)
                        node_y += h_in_sec + intra_gap_y

                    # 2. Middle: Query node
                    if q_node:
                        new_positions[q.name] = (cluster_x + max(0.0, (cluster_w - w_q) / 2.0), node_y)
                        node_y += h_q + intra_gap_y

                    # 3. Bottom: Output section
                    if has_out and has_csv:
                        ox_start = cluster_x + max(0.0, (cluster_w - w_out_sec) / 2.0)
                        new_positions[out_box.name()] = (ox_start, node_y)
                        new_positions[csv_box.name()] = (ox_start + w_out + intra_gap_x, node_y)
                        node_y += h_out_sec + intra_gap_y
                    elif has_out:
                        new_positions[out_box.name()] = (cluster_x + max(0.0, (cluster_w - w_out) / 2.0), node_y)
                        node_y += h_out_sec + intra_gap_y
                    elif has_csv:
                        new_positions[csv_box.name()] = (cluster_x + max(0.0, (cluster_w - w_csv) / 2.0), node_y)
                        node_y += h_out_sec + intra_gap_y

                    total_h = node_y - rank_start_y
                    max_rank_h = max(max_rank_h, total_h)

                cur_y = rank_start_y + max_rank_h + rank_gap_y

        # 4. Apply new positions to all matching nodes in the graph
        for node_name, (nx, ny) in new_positions.items():
            node = nodes_by_name.get(node_name)
            if node:
                node.set_pos(nx, ny)

        # Ensure all existing nodes are tracked in new_positions
        for node in all_nodes:
            if node.name() not in new_positions:
                new_positions[node.name()] = (node.pos()[0], node.pos()[1])

        # 5. Redraw pipes
        for item in graph.viewer().scene().items():
            if hasattr(item, "reset"):
                try:
                    item.reset()
                except Exception:
                    pass

        return new_positions

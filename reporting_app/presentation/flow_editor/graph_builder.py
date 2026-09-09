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
from reporting_app.presentation.flow_editor.nodes import QueryNode, TableBoxNode

logger = logging.getLogger(__name__)


class ProcessFlowGraphBuilder:
    """Builds and updates the NodeGraph according to Process Flow Graph Logic."""

    @staticmethod
    def rebuild_graph(
        graph: NodeGraph,
        queries: List[QueryInfo],
        existing_positions: Optional[Dict[str, Tuple[float, float]]] = None,
        show_full_table_names: bool = True,
    ) -> Dict[str, Tuple[float, float]]:
        """Reconstruct the entire graph topology according to the architectural rules

        while preserving node positions where possible.
        """
        existing_positions = dict(existing_positions or {})

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
        keys_to_remove = [
            k for k in list(existing_positions.keys())
            if not any(k == qn or k.startswith(f"{qn} [") for qn in active_query_names)
        ]
        for k in keys_to_remove:
            existing_positions.pop(k, None)

        # Clear existing nodes
        graph.delete_nodes(graph.all_nodes())

        if not queries:
            return existing_positions

        # 2. Build table provenance index across active queries on canvas
        # Single-Writer Provenance: table -> creating query name
        table_producers: Dict[str, str] = {}
        for q in queries:
            for out_t in q.output_tables:
                table_producers[out_t] = q.name

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
            qnode.set_property("parameters", ", ".join(q.parameter_names))
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
                csv_file_name = f"{q.name}.csv"
                csv_box.setup_as_csv_output(csv_file_name)
                csv_box.set_display_mode(show_full_table_names)
                try:
                    csv_port = qnode.get_output("csv_out") or qnode.get_output("tables_out")
                    csv_port.connect_to(csv_box.get_input("in_tables"))
                except Exception as e:
                    logger.debug(f"Could not connect {q.name} to CSV Box: {e}")
                csv_boxes[q.name] = csv_box

            col_x += 650

        # 4. Resolve Input Requirements for each Query Node
        for q in queries:
            qnode = query_nodes[q.name]
            qx, qy = qnode.pos()

            # Partition required input tables into:
            # - external_tables: not produced by any active query in this graph
            # - derived_tables: produced by upstream queries active in this graph
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
            if single_producer:
                upstream_qinfo = next((uq for uq in queries if uq.name == single_producer), None)
                if upstream_qinfo:
                    is_complete_output = set(derived_tables_by_producer[single_producer]) == set(upstream_qinfo.output_tables)

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
    ) -> Dict[str, Tuple[float, float]]:
        """Format the process flow layout horizontally (left to right) or vertically (top to bottom).

        Ensures boxes are spaced apart cleanly according to dependencies without any overlapping.
        """
        if not queries:
            return {}

        # 1. Map nodes by identifier
        all_nodes = graph.all_nodes()
        nodes_by_name = {n.name(): n for n in all_nodes}

        # 2. Build dependency graph (data provenance + execution flow)
        table_producers: Dict[str, str] = {}
        for q in queries:
            for out_t in q.output_tables:
                table_producers[out_t] = q.name

        query_names = {q.name for q in queries}
        upstreams: Dict[str, Set[str]] = {q.name: set() for q in queries}

        for q in queries:
            # Data dependencies
            for in_t in q.input_tables:
                prod = table_producers.get(in_t)
                if prod and prod in query_names and prod != q.name:
                    upstreams[q.name].add(prod)

            # Direct run connection dependencies
            qnode = nodes_by_name.get(q.name)
            if qnode:
                run_in = qnode.get_input("run_in")
                if run_in:
                    for port in run_in.connected_ports():
                        up_node = port.node()
                        up_name = up_node.get_property("query_name") if hasattr(up_node, "get_property") else up_node.name()
                        if up_name in query_names and up_name != q.name:
                            upstreams[q.name].add(up_name)

        # 3. Compute topological ranks for each query (0, 1, 2...)
        ranks: Dict[str, int] = {q.name: 0 for q in queries}
        for _ in range(len(queries)):
            changed = False
            for q in queries:
                ups = upstreams[q.name]
                if ups:
                    max_up = max(ranks[u] for u in ups)
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

        # Helper to get node dimensions safely
        def get_node_dims(node: Optional[BaseNode], default_w: float = 200.0, default_h: float = 60.0) -> Tuple[float, float]:
            if not node:
                return (0.0, 0.0)
            try:
                rect = node.view.boundingRect()
                w = max(rect.width(), default_w)
                h = max(rect.height(), default_h)
                return (w, h)
            except Exception:
                return (default_w, default_h)

        new_positions: Dict[str, Tuple[float, float]] = {}

        if direction == "horizontal":
            # Left to Right:
            # Ranks advance along X; within a rank, queries stack along Y.
            cur_x = 0.0
            col_gap_x = 100.0
            intra_gap_x = 70.0
            intra_gap_y = 20.0
            query_gap_y = 60.0

            for r in sorted_ranks:
                queries_in_rank = rank_groups[r]
                max_rank_w = 0.0
                cur_y = 0.0

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
                        w_in_sec = max(w_in, w_conv)
                        h_in_sec = h_in + intra_gap_y + h_conv
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
                        w_out_sec = max(w_out, w_csv)
                        h_out_sec = h_out + intra_gap_y + h_csv
                    elif has_out:
                        w_out_sec, h_out_sec = w_out, h_out
                    elif has_csv:
                        w_out_sec, h_out_sec = w_csv, h_csv
                    else:
                        w_out_sec, h_out_sec = 0.0, 0.0

                    cluster_h = max(h_in_sec, h_q, h_out_sec)
                    cluster_w = (
                        (w_in_sec + intra_gap_x if w_in_sec > 0 else 0.0)
                        + w_q
                        + (intra_gap_x + w_out_sec if w_out_sec > 0 else 0.0)
                    )
                    max_rank_w = max(max_rank_w, cluster_w)

                    # Place input box(es)
                    if has_in and has_conv:
                        new_positions[in_box.name()] = (cur_x, cur_y)
                        new_positions[conv_box.name()] = (cur_x, cur_y + h_in + intra_gap_y)
                    elif has_in:
                        new_positions[in_box.name()] = (cur_x, cur_y)
                    elif has_conv:
                        new_positions[conv_box.name()] = (cur_x, cur_y)

                    # Place query node
                    qx = cur_x + (w_in_sec + intra_gap_x if w_in_sec > 0 else 0.0)
                    qy = cur_y + max(0.0, (cluster_h - h_q) / 2.0)
                    if q_node:
                        new_positions[q.name] = (qx, qy)

                    # Place output box(es)
                    ox = qx + w_q + intra_gap_x
                    if has_out and has_csv:
                        new_positions[out_box.name()] = (ox, cur_y)
                        new_positions[csv_box.name()] = (ox, cur_y + h_out + intra_gap_y)
                    elif has_out:
                        new_positions[out_box.name()] = (ox, cur_y)
                    elif has_csv:
                        new_positions[csv_box.name()] = (ox, cur_y)

                    cur_y += cluster_h + query_gap_y

                cur_x += max_rank_w + col_gap_x

        else:
            # Top to Bottom (Vertical):
            # Ranks advance along Y; within a rank, queries advance along X.
            cur_y = 0.0
            row_gap_y = 120.0
            intra_gap_x = 20.0
            intra_gap_y = 40.0
            query_gap_x = 70.0

            for r in sorted_ranks:
                queries_in_rank = rank_groups[r]
                max_rank_h = 0.0
                cur_x = 0.0

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

                    # Top: Input section
                    has_in = bool(in_box)
                    has_conv = bool(conv_box)
                    if has_in and has_conv:
                        w_in_sec = max(w_in, w_conv)
                        h_in_sec = h_in + intra_gap_y + h_conv
                    elif has_in:
                        w_in_sec, h_in_sec = w_in, h_in
                    elif has_conv:
                        w_in_sec, h_in_sec = w_conv, h_conv
                    else:
                        w_in_sec, h_in_sec = 0.0, 0.0

                    # Bottom: Output section (placed side-by-side if both exist to balance width)
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
                    cluster_h = (
                        (h_in_sec + intra_gap_y if h_in_sec > 0 else 0.0)
                        + h_q
                        + (intra_gap_y + h_out_sec if h_out_sec > 0 else 0.0)
                    )
                    max_rank_h = max(max_rank_h, cluster_h)

                    # Place top input box(es)
                    if has_in and has_conv:
                        new_positions[in_box.name()] = (cur_x + max(0.0, (cluster_w - w_in) / 2.0), cur_y)
                        new_positions[conv_box.name()] = (cur_x + max(0.0, (cluster_w - w_conv) / 2.0), cur_y + h_in + intra_gap_y)
                    elif has_in:
                        new_positions[in_box.name()] = (cur_x + max(0.0, (cluster_w - w_in) / 2.0), cur_y)
                    elif has_conv:
                        new_positions[conv_box.name()] = (cur_x + max(0.0, (cluster_w - w_conv) / 2.0), cur_y)

                    # Place middle query node
                    qy = cur_y + (h_in_sec + intra_gap_y if h_in_sec > 0 else 0.0)
                    qx = cur_x + max(0.0, (cluster_w - w_q) / 2.0)
                    if q_node:
                        new_positions[q.name] = (qx, qy)

                    # Place bottom output box(es)
                    oy = qy + h_q + intra_gap_y
                    if has_out and has_csv:
                        ox_start = cur_x + max(0.0, (cluster_w - w_out_sec) / 2.0)
                        new_positions[out_box.name()] = (ox_start, oy)
                        new_positions[csv_box.name()] = (ox_start + w_out + intra_gap_x, oy)
                    elif has_out:
                        new_positions[out_box.name()] = (cur_x + max(0.0, (cluster_w - w_out) / 2.0), oy)
                    elif has_csv:
                        new_positions[csv_box.name()] = (cur_x + max(0.0, (cluster_w - w_csv) / 2.0), oy)

                    cur_x += cluster_w + query_gap_x

                cur_y += max_rank_h + row_gap_y

        # 4. Apply new positions to all matching nodes in the graph
        for node_name, (nx, ny) in new_positions.items():
            node = nodes_by_name.get(node_name)
            if node:
                node.set_pos(nx, ny)

        # 5. Redraw pipes
        for item in graph.viewer().scene().items():
            if hasattr(item, "reset"):
                try:
                    item.reset()
                except Exception:
                    pass

        return new_positions

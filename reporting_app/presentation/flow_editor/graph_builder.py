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

from NodeGraphQt import NodeGraph
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
        existing_positions = existing_positions or {}

        # 1. Capture current positions of existing nodes before clearing
        for node in graph.all_nodes():
            qname = node.get_property("query_name") if hasattr(node, "get_property") else None
            key = qname or node.name()
            if key not in existing_positions:
                pos = node.pos()
                existing_positions[key] = (pos[0], pos[1])

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
                in_pos = existing_positions.get(in_box_key, (qx - 280, qy - 40))
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
                j_pos = existing_positions.get(junction_key, (qx - 280, qy + 60))
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

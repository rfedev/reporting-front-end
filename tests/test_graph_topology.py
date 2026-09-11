"""Unit tests for Process Flow Graph Logic and Topology Builder:

- Single-Writer Provenance
- Selective Aggregation and Pruning (Convergence Pattern)
- Mixed Input Resolution (Heterogeneous Inputs)
- Direct Linear Chaining
- Color Invariants (Orange for Query->Output, Green for intake into Query)
- Dynamic Synchronization on query addition, modification, and removal.
"""

from pathlib import Path
import tempfile
import unittest
from PySide6.QtWidgets import QApplication

from reporting_app.controllers.app_controller import AppController
from reporting_app.core.models import QueryInfo, Report
from reporting_app.persistence.database import DatabaseManager
from reporting_app.presentation.flow_editor.editor_window import ProcessFlowEditorWindow
from reporting_app.presentation.flow_editor.graph_builder import ProcessFlowGraphBuilder
from reporting_app.presentation.flow_editor.nodes import (
    COLOR_BLUE,
    COLOR_DARK_ORANGE,
    COLOR_GREEN,
    COLOR_DARK_PURPLE,
    QueryNode,
    TableBoxNode,
)


class TestGraphTopology(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "test.db"
        self.db_manager = DatabaseManager(self.db_path)

        self.rep_dir = self.root / "flow_rep"
        self.queries_dir = self.rep_dir / "queries"
        self.queries_dir.mkdir(parents=True, exist_ok=True)

        # Setup queries matching the exact user specification / Mermaid diagram:
        # query1: inputs=[Table1, Table2], outputs=[Table3, Table4, Table5, Table6]
        (self.queries_dir / "query1.sql").write_text(
            "CREATE OR REPLACE TABLE Table3 AS SELECT * FROM Table1;\n"
            "CREATE OR REPLACE TABLE Table4 AS SELECT * FROM Table2;\n"
            "CREATE OR REPLACE TABLE Table5 AS SELECT * FROM Table1;\n"
            "CREATE OR REPLACE TABLE Table6 AS SELECT * FROM Table2;\n",
            encoding="utf-8",
        )
        # query2: inputs=[Table11], outputs=[Table7, Table8]
        (self.queries_dir / "query2.sql").write_text(
            "CREATE OR REPLACE TABLE Table7 AS SELECT * FROM Table11;\n"
            "CREATE OR REPLACE TABLE Table8 AS SELECT * FROM Table11;\n",
            encoding="utf-8",
        )
        # query3: inputs=[Table5, Table6, Table7, Table10], outputs=[Table9]
        # (Table5, Table6 from query1; Table7 from query2; Table10 is root external input)
        (self.queries_dir / "query3.sql").write_text(
            "CREATE OR REPLACE TABLE Table9 AS SELECT * FROM Table5 JOIN Table6 JOIN Table7 JOIN Table10;\n",
            encoding="utf-8",
        )
        # query4: inputs=[Table9] (linear chaining from query3 output Table9)
        (self.queries_dir / "query4.sql").write_text(
            "SELECT * FROM Table9;\n",
            encoding="utf-8",
        )

        self.controller = AppController(db_manager=self.db_manager)
        self.controller.set_working_directory(self.root)
        self.controller.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_convergence_and_linear_chaining_topology(self):
        """Verify the exact topology matching the architectural specification:

        - query1 -> Output box (Table3, Table4, Table5, Table6) [Orange edge]
        - query2 -> Output box (Table7, Table8) [Orange edge]
        - Consolidated Output box (Table5, Table6, Table7) [Convergence of query1 and query2]
        - Upstream output boxes -> Consolidated box [Orange edges]
        - Consolidated box -> query3 [Green edge]
        - Root external input (Table10) -> query3 [Green edge]
        - query3 -> Output box (Table9) [Orange edge]
        - query3 Output box -> query4 [Green edge (linear chaining)]
        """
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="SpecFlow",
            app_controller=self.controller,
        )

        # Add all 4 queries to canvas
        editor.add_query_to_canvas("query1")
        editor.add_query_to_canvas("query2")
        editor.add_query_to_canvas("query3")
        editor.add_query_to_canvas("query4")

        nodes = editor.graph.all_nodes()
        query_nodes = {n.name(): n for n in nodes if isinstance(n, QueryNode)}
        table_boxes = [n for n in nodes if isinstance(n, TableBoxNode)]

        self.assertIn("query1", query_nodes)
        self.assertIn("query2", query_nodes)
        self.assertIn("query3", query_nodes)
        self.assertIn("query4", query_nodes)

        # Verify Consolidated convergence box exists for query3
        q3_node = query_nodes["query3"]
        q3_intake_port = q3_node.get_input("tables_in")
        connected_tables_boxes = [p.node() for p in q3_intake_port.connected_ports() if isinstance(p.node(), TableBoxNode)]
        
        # In query3, there should be an external input box (Table10) and a convergence box (Table5, Table6, Table7)
        convergence_boxes = [b for b in connected_tables_boxes if b.get_property("box_type") == "Output Tables"]
        self.assertEqual(len(convergence_boxes), 1)
        conv_box = convergence_boxes[0]
        # Should contain Table5, Table6, Table7 (selected required subset from query1 and query2)
        conv_lines = [line.strip("• ") for line in conv_box.view.table_lines]
        self.assertIn("Table5", conv_lines)
        self.assertIn("Table6", conv_lines)
        self.assertIn("Table7", conv_lines)
        self.assertNotIn("Table3", conv_lines)
        self.assertNotIn("Table4", conv_lines)
        self.assertNotIn("Table8", conv_lines)

        # Verify root external input box for query3 contains Table10
        q3_input_boxes = [b for b in table_boxes if b.get_property("box_type") == "Input Tables" and "query3" in b.name()]
        self.assertEqual(len(q3_input_boxes), 1)
        q3_in_box = q3_input_boxes[0]
        self.assertIn("• Table10", q3_in_box.view.table_lines)

        # Verify linear chaining: query3's output box (Table9) feeds directly into query4
        q3_out_boxes = [b for b in table_boxes if b.get_property("box_type") == "Output Tables" and "query3" in b.name() and "[Out]" in b.name()]
        self.assertEqual(len(q3_out_boxes), 1)
        q3_out_box = q3_out_boxes[0]
        self.assertIn("• Table9", q3_out_box.view.table_lines)

        # Check connection from q3_out_box to query4
        q4_in_port = query_nodes["query4"].get_input("tables_in")
        connected_to_q4 = [p.node() for p in q4_in_port.connected_ports()]
        self.assertIn(q3_out_box, connected_to_q4)

        # Check color invariants on pipes:
        pipes = [p for p in editor.graph.viewer().scene().items() if hasattr(p, "color")]
        
        # 1. Edge from query3 to its output box should be ORANGE
        q3_to_out_pipes = [p for p in pipes if hasattr(p, "output_port") and p.output_port and p.output_port.node == query_nodes["query3"].view]
        out_table_pipes = [p for p in q3_to_out_pipes if p.input_port and p.input_port.node == q3_out_box.view]
        self.assertTrue(len(out_table_pipes) > 0)
        self.assertEqual(out_table_pipes[0].color, COLOR_DARK_ORANGE)

        # 2. Intake edge into query4 (from q3_out_box) should be GREEN
        q4_intake_pipes = [p for p in pipes if hasattr(p, "input_port") and p.input_port and p.input_port.node == query_nodes["query4"].view]
        self.assertTrue(len(q4_intake_pipes) > 0)
        self.assertEqual(q4_intake_pipes[0].color, COLOR_GREEN)

        # 3. Intake edge into query3 (from convergence box) should be GREEN
        q3_intake_pipes = [p for p in pipes if hasattr(p, "input_port") and p.input_port and p.input_port.node == query_nodes["query3"].view and p.output_port.node == conv_box.view]
        self.assertTrue(len(q3_intake_pipes) > 0)
        self.assertEqual(q3_intake_pipes[0].color, COLOR_GREEN)

        # 4. Inflow edges into convergence box from upstream output boxes should be ORANGE
        conv_incoming_pipes = [p for p in pipes if hasattr(p, "input_port") and p.input_port and p.input_port.node == conv_box.view]
        self.assertTrue(len(conv_incoming_pipes) >= 2)
        for cp in conv_incoming_pipes:
            self.assertEqual(cp.color, COLOR_DARK_ORANGE)

        editor.close()

    def test_dynamic_reconfiguration_when_producer_query_removed(self):
        """When a producing query is deleted from canvas or report:

        Any downstream query consuming its output tables should automatically
        have those tables transition into an external root input box (Green).
        """
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="DeleteFlow",
            app_controller=self.controller,
        )

        # Add query3 and query4 (linear chain)
        editor.add_query_to_canvas("query3")
        editor.add_query_to_canvas("query4")

        # Initially, Table9 is produced by query3 on canvas
        # Remove query3 from canvas
        q3_node = [n for n in editor.graph.all_nodes() if n.name() == "query3"][0]
        editor.graph.delete_nodes([q3_node])
        editor._sync_graph_topology()

        # query3 output box should be pruned, and query4 should now have Table9 as an external input (Green box)
        remaining_nodes = editor.graph.all_nodes()
        self.assertNotIn("query3", [n.name() for n in remaining_nodes])
        self.assertNotIn("query3 [Out]", [n.name() for n in remaining_nodes])

        q4_in_boxes = [b for b in remaining_nodes if isinstance(b, TableBoxNode) and b.get_property("box_type") == "Input Tables"]
        self.assertTrue(len(q4_in_boxes) > 0)
        self.assertIn("• Table9", q4_in_boxes[0].view.table_lines)

        editor.close()

    def test_delete_selected_only_deletes_query_nodes(self):
        """Verify that 'delete selected' and Delete key only delete QueryNodes,

        never directly deleting TableBoxNodes.
        """
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="DeleteRuleFlow",
            app_controller=self.controller,
        )
        editor.add_query_to_canvas("query1")
        editor.add_query_to_canvas("query2")

        # 1. Select ONLY TableBoxNode (e.g. query1 [Out])
        all_nodes = editor.graph.all_nodes()
        table_boxes = [n for n in all_nodes if isinstance(n, TableBoxNode)]
        self.assertTrue(len(table_boxes) > 0)
        target_tb = table_boxes[0]

        editor.graph.clear_selection()
        target_tb.set_selected(True)
        self.assertTrue(target_tb.selected())

        # Trigger delete selected
        editor._on_delete_selected()

        # The TableBoxNode must NOT be deleted
        remaining_ids = [n.id for n in editor.graph.all_nodes()]
        self.assertIn(target_tb.id, remaining_ids)

        # 2. Select QueryNode query2
        q2_node = [n for n in editor.graph.all_nodes() if n.name() == "query2"][0]
        editor.graph.clear_selection()
        q2_node.set_selected(True)
        # Also select another table box along with it
        target_tb.set_selected(True)

        editor._on_delete_selected()

        # query2 was deleted, query1 remains
        remaining_names = [n.name() for n in editor.graph.all_nodes()]
        self.assertNotIn("query2", remaining_names)
        self.assertIn("query1", remaining_names)

        editor.close()

    def test_boxes_not_rearranged_when_node_added_or_deleted(self):
        """Verify existing node positions are strictly preserved when nodes are added or deleted."""
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="PosPreserveFlow",
            app_controller=self.controller,
        )
        editor.add_query_to_canvas("query1", pos=(150.0, 250.0))

        q1_node = [n for n in editor.graph.all_nodes() if n.name() == "query1"][0]
        q1_out = [n for n in editor.graph.all_nodes() if n.name() == "query1 [Out]"][0]

        # Manually move query1 and its output box to custom positions
        q1_node.set_pos(320.0, 480.0)
        q1_out.set_pos(650.0, 480.0)

        # Add query2 at a different position
        editor.add_query_to_canvas("query2", pos=(900.0, 100.0))

        # Query1 and its output box must remain at the exact positions set by the user
        q1_node_now = [n for n in editor.graph.all_nodes() if n.name() == "query1"][0]
        q1_out_now = [n for n in editor.graph.all_nodes() if n.name() == "query1 [Out]"][0]

        self.assertEqual(q1_node_now.pos(), [320.0, 480.0])
        self.assertEqual(q1_out_now.pos(), [650.0, 480.0])

        # Delete query2
        q2_node = [n for n in editor.graph.all_nodes() if n.name() == "query2"][0]
        editor.graph.clear_selection()
        q2_node.set_selected(True)
        editor._on_delete_selected()

        # Query1 and its output box must still remain at the exact same positions
        q1_node_after_del = [n for n in editor.graph.all_nodes() if n.name() == "query1"][0]
        q1_out_after_del = [n for n in editor.graph.all_nodes() if n.name() == "query1 [Out]"][0]

        self.assertEqual(q1_node_after_del.pos(), [320.0, 480.0])
        self.assertEqual(q1_out_after_del.pos(), [650.0, 480.0])

        editor.close()

    def test_auto_format_horizontal_and_vertical(self):
        """Verify horizontal and vertical auto-format layouts:

        - Correct topological progression (L-to-R or T-to-B).
        - No overlapping bounding boxes.
        """
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="AutoLayoutFlow",
            app_controller=self.controller,
        )
        editor.add_query_to_canvas("query1")
        editor.add_query_to_canvas("query2")
        editor.add_query_to_canvas("query3")
        editor.add_query_to_canvas("query4")

        # 1. Test Horizontal Layout (Left to Right)
        editor._auto_layout("horizontal")

        q1_pos = [n for n in editor.graph.all_nodes() if n.name() == "query1"][0].pos()
        q3_pos = [n for n in editor.graph.all_nodes() if n.name() == "query3"][0].pos()
        q4_pos = [n for n in editor.graph.all_nodes() if n.name() == "query4"][0].pos()

        # query3 depends on query1; query4 depends on query3
        # In horizontal mode, X must strictly increase along dependencies
        self.assertLess(q1_pos[0], q3_pos[0])
        self.assertLess(q3_pos[0], q4_pos[0])

        # Check that no nodes overlap in horizontal layout
        nodes = editor.graph.all_nodes()
        rects = []
        for n in nodes:
            pos = n.pos()
            w = n.view.boundingRect().width()
            h = n.view.boundingRect().height()
            rects.append((n.name(), pos[0], pos[1], pos[0] + w, pos[1] + h))

        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                name_i, l1, t1, r1, b1 = rects[i]
                name_j, l2, t2, r2, b2 = rects[j]
                # Check for rectangle intersection (with 1px tolerance for touching edges)
                overlaps = not (r1 <= l2 or r2 <= l1 or b1 <= t2 or b2 <= t1)
                self.assertFalse(overlaps, f"Nodes '{name_i}' and '{name_j}' overlap in horizontal layout!")

        # 2. Test Vertical Layout (Top to Bottom)
        editor._auto_layout("vertical")

        q1_pos_v = [n for n in editor.graph.all_nodes() if n.name() == "query1"][0].pos()
        q3_pos_v = [n for n in editor.graph.all_nodes() if n.name() == "query3"][0].pos()
        q4_pos_v = [n for n in editor.graph.all_nodes() if n.name() == "query4"][0].pos()

        # In vertical mode, Y must strictly increase along dependencies
        self.assertLess(q1_pos_v[1], q3_pos_v[1])
        self.assertLess(q3_pos_v[1], q4_pos_v[1])

        # Check that no nodes overlap in vertical layout
        rects_v = []
        for n in nodes:
            pos = n.pos()
            w = n.view.boundingRect().width()
            h = n.view.boundingRect().height()
            rects_v.append((n.name(), pos[0], pos[1], pos[0] + w, pos[1] + h))

        for i in range(len(rects_v)):
            for j in range(i + 1, len(rects_v)):
                name_i, l1, t1, r1, b1 = rects_v[i]
                name_j, l2, t2, r2, b2 = rects_v[j]
                overlaps = not (r1 <= l2 or r2 <= l1 or b1 <= t2 or b2 <= t1)
                self.assertFalse(overlaps, f"Nodes '{name_i}' and '{name_j}' overlap in vertical layout!")

        editor.close()

    def test_import_csv_node_and_dialog(self):
        """Verify ImportCsvNode and ImportCsvDialog creation, row addition/deletion, and graph reconstruction."""
        from reporting_app.presentation.flow_editor.editor_window import ImportCsvDialog
        from reporting_app.presentation.flow_editor.nodes import ImportCsvNode

        # 1. Test ImportCsvDialog
        dlg = ImportCsvDialog(initial_imports=[
            {"csv_path": "/path/to/users.csv", "has_headers": True, "output_table": "myproj.raw.users"}
        ])
        self.assertEqual(len(dlg.rows), 1)
        self.assertEqual(dlg.rows[0]["path_edit"].text(), "/path/to/users.csv")
        self.assertTrue(dlg.rows[0]["headers_cb"].isChecked())
        self.assertEqual(dlg.rows[0]["table_edit"].text(), "myproj.raw.users")

        # Add second row
        dlg._add_row(csv_path="/path/to/orders.csv", has_headers=False, output_table="myproj.raw.orders")
        self.assertEqual(len(dlg.rows), 2)
        imports = dlg.get_imports()
        self.assertEqual(len(imports), 2)
        self.assertEqual(imports[1]["output_table"], "myproj.raw.orders")
        self.assertFalse(imports[1]["has_headers"])

        # 2. Test rebuild_graph with import_csv_data
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="ImportFlow",
            app_controller=self.controller,
        )

        import_csv_data = [
            {
                "node_name": "Import csv",
                "items": [
                    {"csv_path": "/path/to/test.csv", "has_headers": True, "output_table": "Table1"}
                ]
            }
        ]

        # query1 consumes Table1, which is produced by "Import csv"!
        q1 = self.controller.active_report.get_query("query1")
        ProcessFlowGraphBuilder.rebuild_graph(
            editor.graph,
            queries=[q1],
            existing_positions={},
            show_full_table_names=True,
            csv_filenames={},
            import_csv_data=import_csv_data,
        )

        all_nodes = {n.name(): n for n in editor.graph.all_nodes()}
        self.assertIn("Import csv", all_nodes)
        self.assertIn("Import csv [Out]", all_nodes)
        self.assertIn("query1", all_nodes)

        imp_node = all_nodes["Import csv"]
        self.assertIsInstance(imp_node, ImportCsvNode)
        imp_out_box = all_nodes["Import csv [Out]"]
        self.assertEqual(imp_out_box.raw_tables, ["Table1"])

        # query1 takes Table1 and Table2; since Table1 is produced by Import csv,
        # it should connect via linear chaining or convergence to query1!
        connected_to_q1_in = [p.node().name() for p in all_nodes["query1"].get_input("tables_in").connected_ports()]
        # Either the convergence box or Import csv [Out] is connected to query1
        self.assertTrue(any("Convergence" in c or c == "Import csv [Out]" for c in connected_to_q1_in))

        editor.close()

    def test_empty_space_deselection(self):
        """Verify empty space deselection clears all selected nodes."""
        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="SelectFlow",
            app_controller=self.controller,
        )
        editor.add_query_to_canvas("query1")
        qnode = [n for n in editor.graph.all_nodes() if n.name() == "query1"][0]
        qnode.set_selected(True)
        self.assertIn(qnode, editor.graph.selected_nodes())

        # Clear selection (simulating empty space click)
        editor.graph.clear_selection()
        if editor.graph.viewer().scene():
            editor.graph.viewer().scene().clearSelection()

        self.assertEqual(len(editor.graph.selected_nodes()), 0)
        editor.close()


if __name__ == "__main__":
    unittest.main()


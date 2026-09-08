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


if __name__ == "__main__":
    unittest.main()

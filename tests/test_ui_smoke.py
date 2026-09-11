"""Smoke tests for PySide6 and NodeGraphQt UI components."""

import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication

from reporting_app.controllers.app_controller import AppController
from reporting_app.core.models import QueryInfo, QueryParameter, Report
from reporting_app.persistence.database import DatabaseManager
from reporting_app.presentation.flow_editor.editor_window import ProcessFlowEditorWindow
from reporting_app.presentation.main_window import MainWindow
from reporting_app.presentation.query_dialog import QueryRunDialog
from reporting_app.presentation.settings_dialog import SettingsDialog


class TestUISmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "test.db"
        self.db_manager = DatabaseManager(self.db_path)

        self.rep_dir = self.root / "rep_ui"
        self.queries_dir = self.rep_dir / "queries"
        self.queries_dir.mkdir(parents=True, exist_ok=True)
        (self.queries_dir / "test_q.sql").write_text(
            "SELECT 1 FROM `mytbl` WHERE dt = '{repDate}';", encoding="utf-8"
        )

        self.controller = AppController(db_manager=self.db_manager)
        self.controller.set_working_directory(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_main_window_init(self):
        win = MainWindow(controller=self.controller)
        self.assertIsNotNone(win)
        self.assertTrue(win.reports_combo.count() >= 1)
        win.close()

    def test_settings_dialog_init(self):
        dialog = SettingsDialog(current_dir=self.root, auto_scan=True)
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.get_working_directory(), self.root.resolve())
        self.assertTrue(dialog.get_auto_scan())
        dialog.close()

    def test_query_run_dialog_init(self):
        qinfo = QueryInfo(
            name="test_q",
            file_path=self.queries_dir / "test_q.sql",
            report_name="rep_ui",
            parameters=[QueryParameter(name="repDate", default_value="2026-09-08")],
        )
        dialog = QueryRunDialog(query_info=qinfo)
        self.assertIsNotNone(dialog)
        self.assertIn("repDate", dialog.param_edits)
        dialog.close()

    def test_flow_editor_window_init(self):
        rep = self.controller.active_report
        editor = ProcessFlowEditorWindow(
            report=rep,
            flow_name="SmokeFlow",
            app_controller=self.controller,
        )
        self.assertIsNotNone(editor)

        # Test adding query node to canvas
        node = editor.add_query_to_canvas("test_q")
        self.assertIsNotNone(node)
        self.assertEqual(node.name(), "test_q")

        # Test serializing graph
        session = editor.graph.serialize_session()
        self.assertIn("nodes", session)

        editor.close()

    def test_output_csv_box_and_noodle_colors(self):
        rep = self.controller.active_report
        queries_dir = self.rep_dir / "queries"
        queries_dir.mkdir(parents=True, exist_ok=True)
        csv_q_file = queries_dir / "csv_query.sql"
        csv_q_file.write_text(
            "SELECT id, val FROM `raw.metrics_summary`;",
            encoding="utf-8",
        )
        self.controller.initialize()

        editor = ProcessFlowEditorWindow(
            report=self.controller.active_report,
            flow_name="CSVFlow",
            app_controller=self.controller,
        )

        qnode = editor.add_query_to_canvas("csv_query")
        self.assertIsNotNone(qnode)

        # Check nodes on canvas
        nodes = editor.graph.all_nodes()
        csv_nodes = [n for n in nodes if n.get_property("box_type") == "Output CSV"]
        self.assertEqual(len(csv_nodes), 1)
        csv_node = csv_nodes[0]
        self.assertEqual(csv_node.get_property("box_type"), "Output CSV")
        # Header title rendered has no numeric suffix
        self.assertEqual(csv_node.view.custom_title, "Output CSV")
        # Displays CSV filename
        self.assertIn("• csv_query.csv", csv_node.view.table_lines)
        # Dark purple color
        self.assertEqual(csv_node.color(), (110, 45, 130))

        # Check connected pipe color to Output CSV box
        pipes = [p for p in editor.graph.viewer().scene().items() if hasattr(p, "color")]
        csv_pipes = [p for p in pipes if hasattr(p, "input_port") and p.input_port and p.input_port.node == csv_node.view]
        self.assertTrue(len(csv_pipes) > 0)
        self.assertEqual(csv_pipes[0].color, (110, 45, 130, 255))

        # Verify live drag dashed noodle adopts port color
        viewer = editor.graph.viewer()
        csv_port = qnode.get_output("csv_out")
        viewer.start_live_connection(csv_port.view)
        live_pipe_color = viewer._LIVE_PIPE.pen().color()
        self.assertEqual((live_pipe_color.red(), live_pipe_color.green(), live_pipe_color.blue()), (110, 45, 130))
        viewer.end_live_connection()

        editor.close()

    def test_canvas_resize_no_zoom(self):
        rep = self.controller.active_report
        editor = ProcessFlowEditorWindow(
            report=rep,
            flow_name="ResizeFlow",
            app_controller=self.controller,
        )
        viewer = editor.graph.viewer()
        viewer.resize(800, 600)
        initial_transform_m11 = viewer.transform().m11()

        # Expand viewer
        from PySide6.QtGui import QResizeEvent
        from PySide6.QtCore import QSize
        resize_ev = QResizeEvent(QSize(1600, 1000), QSize(800, 600))
        viewer.resize(1600, 1000)
        viewer.resizeEvent(resize_ev)

        # Transformation zoom scale should NOT change (no stretching)
        expanded_transform_m11 = viewer.transform().m11()
        self.assertAlmostEqual(initial_transform_m11, expanded_transform_m11, places=3)

        editor.close()

    def test_drag_and_drop_query_onto_canvas(self):
        rep = self.controller.active_report
        editor = ProcessFlowEditorWindow(
            report=rep,
            flow_name="DropFlow",
            app_controller=self.controller,
        )

        from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from PySide6.QtWidgets import QApplication

        mime = QMimeData()
        mime.setText("query:test_q")
        mime.setData("application/x-query-name", b"test_q")

        drop_pos = QPoint(150, 150)
        drop_event = QDropEvent(
            QPointF(drop_pos),
            Qt.CopyAction,
            mime,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        viewport = editor.graph.viewer().viewport()
        viewer = editor.graph.viewer()
        viewer.dropEvent(drop_event)

        nodes = editor.graph.all_nodes()
        query_node_names = [n.name() for n in nodes if n.name() == "test_q"]
        self.assertEqual(len(query_node_names), 1)

        editor.close()

    def test_main_window_edit_process_flow(self):
        win = MainWindow(controller=self.controller)
        self.controller.initialize()
        # Verify opening process flow editor does not throw NameError
        win._on_edit_process_flow()
        self.assertTrue(len(win.editor_windows) >= 1)
        for ed in win.editor_windows:
            ed.close()
        win.close()


if __name__ == "__main__":
    unittest.main()

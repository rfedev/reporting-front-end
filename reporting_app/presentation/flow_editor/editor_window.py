"""Process Flow Editor window with Left Panel, Main NodeGraphQt Canvas, and Right Panel."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QUrl, Qt
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from NodeGraphQt import NodeGraph
from NodeGraphQt.constants import PipeEnum
from NodeGraphQt.qgraphics.pipe import PipeItem

from reporting_app.controllers.app_controller import AppController
from reporting_app.controllers.flow_controller import ProcessFlowController
from reporting_app.core.bigquery_run import run_bigquery_script
from reporting_app.core.models import ProcessFlowInfo, QueryInfo, QueryParameter, Report
from reporting_app.presentation.flow_editor.nodes import QueryNode, TableBoxNode
from reporting_app.presentation.flow_editor.query_tree_widget import QueryManagementPanel
from reporting_app.presentation.query_dialog import QueryRunDialog

logger = logging.getLogger(__name__)


class ProcessFlowEditorWindow(QMainWindow):
    """Visual Process Flow Editor built on NodeGraphQt."""

    def __init__(
        self,
        report: Report,
        flow_info: Optional[ProcessFlowInfo] = None,
        flow_name: Optional[str] = None,
        app_controller: Optional[AppController] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.report = report
        self.app_controller = app_controller

        # Controller for this process flow
        repo = app_controller.repo if app_controller else None
        self.flow_controller = ProcessFlowController(
            report=report,
            flow_info=flow_info,
            flow_name=flow_name,
            repo=repo,
            parent=self,
        )

        self.setWindowTitle(f"Process Flow Editor - {self.flow_controller.flow_name} [{report.name}]")
        self.resize(1200, 750)

        self._node_counter = 0
        # Load persisted show_full_table_names state
        self.show_full_table_names = self.flow_controller.get_show_full_table_names()

        self._init_graph()
        self._build_ui()
        self._setup_canvas_pan_and_select()
        self._load_initial_graph()

    def _init_graph(self) -> None:
        """Initialize NodeGraphQt instance and register custom nodes."""
        self.graph = NodeGraph()
        self.graph.register_node(QueryNode)
        self.graph.register_node(TableBoxNode)

        # Wire node double click
        self.graph.node_double_clicked.connect(self._on_node_double_clicked)

    def _setup_canvas_pan_and_select(self) -> None:
        """Configure left-click empty space panning, smooth resize, and Ctrl+drag multi-select on NodeViewer."""
        viewer = self.graph.viewer()
        orig_mouse_press = viewer.mousePressEvent
        orig_mouse_move = viewer.mouseMoveEvent
        orig_mouse_release = viewer.mouseReleaseEvent

        def custom_viewer_resize(event):
            # Reveal more canvas area when expanded, rather than zooming/stretching the scene
            viewer._last_size = viewer.size()
            QtWidgets.QGraphicsView.resizeEvent(viewer, event)

        viewer.resizeEvent = custom_viewer_resize

        def custom_mouse_press(event):
            if event.button() == Qt.LeftButton:
                mods = event.modifiers()
                # If Ctrl held: multi-select marquee mode
                if mods & Qt.ControlModifier:
                    viewer.MMB_state = False
                    viewer.SHIFT_state = True
                    viewer.CTRL_state = False
                    return orig_mouse_press(event)

                # Check if click lands on empty canvas (no node / pipe)
                map_pos = viewer.mapToScene(event.pos())
                items = viewer._items_near(map_pos, None, 15, 15)
                if not items:
                    # Engage smooth pan mode using MMB engine
                    viewer.MMB_state = True
                    viewer._origin_pos = event.pos()
                    viewer._previous_pos = event.pos()
                    viewer.viewport().setCursor(Qt.ClosedHandCursor)
                    event.accept()
                    return

            return orig_mouse_press(event)

        def custom_mouse_release(event):
            if event.button() == Qt.LeftButton and viewer.MMB_state:
                viewer.MMB_state = False
                viewer.viewport().unsetCursor()
                event.accept()
                return

            return orig_mouse_release(event)

        viewer.mousePressEvent = custom_mouse_press
        viewer.mouseMoveEvent = orig_mouse_move
        viewer.mouseReleaseEvent = custom_mouse_release

        # Color the live dashed noodle according to the connector being dragged
        orig_start_live_connection = viewer.start_live_connection

        def custom_start_live_connection(selected_port):
            if viewer._origin_pos is None and selected_port:
                viewer._origin_pos = viewer.mapFromScene(selected_port.scenePos())
            orig_start_live_connection(selected_port)
            if selected_port and hasattr(viewer, "_LIVE_PIPE") and viewer._LIVE_PIPE:
                port_color = getattr(selected_port, "color", None)
                if port_color:
                    viewer._LIVE_PIPE.set_pipe_styling(
                        color=port_color,
                        width=3,
                        style=PipeEnum.DRAW_TYPE_DASHED.value,
                    )

        viewer.start_live_connection = custom_start_live_connection

        # Direct Drag & Drop event handling on the viewer and its viewport
        def custom_drag_enter(event):
            mime = event.mimeData()
            if (
                mime.hasFormat("application/x-query-name")
                or (mime.hasText() and mime.text().startswith("query:"))
                or (mime.hasText() and self.report.get_query(mime.text().strip()) is not None)
            ):
                event.acceptProposedAction()
            else:
                event.ignore()

        def custom_drag_move(event):
            mime = event.mimeData()
            if (
                mime.hasFormat("application/x-query-name")
                or (mime.hasText() and mime.text().startswith("query:"))
                or (mime.hasText() and self.report.get_query(mime.text().strip()) is not None)
            ):
                event.acceptProposedAction()
            else:
                event.ignore()

        def custom_drop(event):
            mime = event.mimeData()
            query_name = ""
            if mime.hasFormat("application/x-query-name"):
                query_name = bytes(mime.data("application/x-query-name")).decode("utf-8")
            elif mime.hasText():
                text = mime.text().strip()
                if text.startswith("query:"):
                    query_name = text[len("query:"):].strip()
                elif self.report.get_query(text):
                    query_name = text

            if query_name:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                scene_pos = viewer.mapToScene(pos)
                self._handle_query_drop(query_name, scene_pos.x(), scene_pos.y())
                event.acceptProposedAction()
            else:
                event.ignore()

        viewer.setAcceptDrops(True)
        viewer.dragEnterEvent = custom_drag_enter
        viewer.dragMoveEvent = custom_drag_move
        viewer.dropEvent = custom_drop

        if hasattr(viewer, "viewport") and viewer.viewport():
            viewport = viewer.viewport()
            viewport.setAcceptDrops(True)
            viewport.dragEnterEvent = custom_drag_enter
            viewport.dragMoveEvent = custom_drag_move
            viewport.dropEvent = custom_drop

    def _build_ui(self) -> None:
        # Toolbar
        toolbar = QToolBar("Flow Actions", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Far left: Left panel collapse/expand arrow button
        self.toggle_left_btn = QPushButton("◀")
        self.toggle_left_btn.setFixedWidth(36)
        self.toggle_left_btn.setToolTip("Toggle Queries Panel")
        self.toggle_left_btn.clicked.connect(self._toggle_left_panel)
        toolbar.addWidget(self.toggle_left_btn)

        toolbar.addSeparator()

        save_btn = QPushButton("💾 Save")
        save_btn.setToolTip("Save Process Flow")
        save_btn.clicked.connect(self._on_save)
        toolbar.addWidget(save_btn)

        toolbar.addSeparator()

        self.run_flow_btn = QPushButton("▶ Run Flow")
        self.run_flow_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        self.run_flow_btn.setToolTip("Execute process flow queries in dependency order")
        self.run_flow_btn.clicked.connect(self._on_run_flow)
        toolbar.addWidget(self.run_flow_btn)

        toolbar.addSeparator()

        delete_btn = QPushButton("🗑 Delete Selected")
        delete_btn.setToolTip("Delete selected nodes or connection noodles (or press Delete key)")
        delete_btn.clicked.connect(self._on_delete_selected)
        toolbar.addWidget(delete_btn)

        fit_btn = QPushButton("🔍 Fit Graph")
        fit_btn.setToolTip("Fit all nodes into available canvas view")
        fit_btn.clicked.connect(self._fit_graph_to_canvas)
        toolbar.addWidget(fit_btn)

        toolbar.addSeparator()

        # Table address toggle
        self.toggle_table_names_btn = QPushButton(
            "🏷 Full Table Address" if self.show_full_table_names else "🏷 Short Table Name"
        )
        self.toggle_table_names_btn.setToolTip("Toggle between full table address and short table name")
        self.toggle_table_names_btn.setCheckable(True)
        self.toggle_table_names_btn.setChecked(self.show_full_table_names)
        self.toggle_table_names_btn.clicked.connect(self._toggle_table_names_display)
        toolbar.addWidget(self.toggle_table_names_btn)

        # Spacer to push toggle right button to far right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # Far right: Right panel collapse/expand arrow button
        self.toggle_right_btn = QPushButton("▶")
        self.toggle_right_btn.setFixedWidth(36)
        self.toggle_right_btn.setToolTip("Toggle Inspector Panel")
        self.toggle_right_btn.clicked.connect(self._toggle_right_panel)
        toolbar.addWidget(self.toggle_right_btn)

        # Main splitter layout: Left Panel | Canvas | Right Panel
        self.splitter = QSplitter(Qt.Horizontal, self)

        # Left Panel (Query management & Drag-and-Drop)
        self.left_panel = QueryManagementPanel(self)
        self._refresh_left_queries()
        self.left_panel.query_added.connect(self._on_query_added)
        self.left_panel.query_removed.connect(self._on_query_removed)
        self.left_panel.query_renamed.connect(self._on_query_renamed)
        self.left_panel.query_double_clicked.connect(self._open_query_file)
        self.splitter.addWidget(self.left_panel)

        # Center: Node Graph Canvas Widget
        self.graph_widget = self.graph.widget
        viewer = self.graph.viewer()
        viewer.setAcceptDrops(True)
        if hasattr(viewer, "viewport") and viewer.viewport():
            viewer.viewport().setAcceptDrops(True)
            viewer.viewport().installEventFilter(self)
        viewer.installEventFilter(self)
        self.graph_widget.setAcceptDrops(True)
        self.graph_widget.installEventFilter(self)
        self.graph.data_dropped.connect(self._on_graph_data_dropped)
        self.splitter.addWidget(self.graph_widget)

        # Right Panel (Currently unused / placeholder)
        self.right_panel = QWidget(self)
        right_layout = QVBoxLayout(self.right_panel)
        right_label = QLabel("<b>Properties & Inspector</b><br><br><i>(Right panel currently unused)</i>")
        right_label.setAlignment(Qt.AlignCenter)
        right_label.setStyleSheet("color: #888888; font-size: 12px;")
        right_layout.addWidget(right_label)
        self.splitter.addWidget(self.right_panel)

        # Set initial splitter sizes
        self.splitter.setSizes([240, 800, 160])
        self.setCentralWidget(self.splitter)

        # Status bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Pan: Drag empty space | Multi-select: Ctrl+Drag | Double-click node to edit SQL.")

    def _toggle_left_panel(self) -> None:
        """Collapse or expand left queries panel."""
        is_visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not is_visible)
        self.toggle_left_btn.setText("▶" if is_visible else "◀")
        QtCore.QTimer.singleShot(50, self._fit_graph_to_canvas)

    def _toggle_right_panel(self) -> None:
        """Collapse or expand right inspector panel."""
        is_visible = self.right_panel.isVisible()
        self.right_panel.setVisible(not is_visible)
        self.toggle_right_btn.setText("◀" if is_visible else "▶")
        QtCore.QTimer.singleShot(50, self._fit_graph_to_canvas)

    def _toggle_table_names_display(self) -> None:
        """Toggle full table address vs short table name across all TableBoxNodes."""
        self.show_full_table_names = self.toggle_table_names_btn.isChecked()
        self.toggle_table_names_btn.setText(
            "🏷 Full Table Address" if self.show_full_table_names else "🏷 Short Table Name"
        )
        for node in self.graph.all_nodes():
            if isinstance(node, TableBoxNode) or node.type_ == "reporting.nodes.TableBoxNode":
                node.set_display_mode(self.show_full_table_names)

    def _fit_graph_to_canvas(self) -> None:
        """Fit all nodes into the available canvas taking into account panel visibility."""
        viewer = self.graph.viewer()
        nodes = self.graph.selected_nodes() or self.graph.all_nodes()
        if not nodes:
            return

        node_items = [n.view for n in nodes]
        combined_rect = viewer._combined_rect(node_items)
        if combined_rect.isNull() or combined_rect.isEmpty():
            return

        # Add generous padding so nodes are never cut off by canvas edges
        padded_rect = combined_rect.adjusted(-60, -60, 60, 60)
        viewer._scene_range = QtCore.QRectF(padded_rect)
        viewer.setSceneRect(padded_rect)
        viewer.fitInView(padded_rect, Qt.KeepAspectRatio)

    def _refresh_left_queries(self) -> None:
        """Update the list of available queries in the left panel."""
        query_names = [q.name for q in self.report.queries]
        self.left_panel.set_queries(query_names)

    def _load_initial_graph(self) -> None:
        """Load saved session or automatically add flow queries if brand new."""
        session_data = self.flow_controller.get_graph_session()
        if session_data and "nodes" in session_data and session_data["nodes"]:
            try:
                self.graph.deserialize_session(session_data)
                # Re-apply text and display mode to restored TableBoxNodes
                for node in self.graph.all_nodes():
                    if isinstance(node, TableBoxNode) or node.type_ == "reporting.nodes.TableBoxNode":
                        btype = node.get_property("box_type") or "Table Box"
                        node.view.set_custom_title(btype)
                        node.set_display_mode(self.show_full_table_names)
                # Re-apply custom noodle colors to all pipes in the restored scene
                for item in self.graph.viewer().scene().items():
                    if isinstance(item, PipeItem):
                        item.reset()
                QtCore.QTimer.singleShot(100, self._fit_graph_to_canvas)
                return
            except Exception as e:
                logger.error(f"Error restoring node session: {e}")

        # If empty session but query names exist in flow definition, create nodes for them
        flow_query_names = self.flow_controller.get_query_names()
        x_offset = 0
        for qname in flow_query_names:
            self.add_query_to_canvas(qname, pos=(x_offset, 0))
            x_offset += 650

        QtCore.QTimer.singleShot(100, self._fit_graph_to_canvas)

    def add_query_to_canvas(self, query_name: str, pos: Optional[tuple] = None) -> Optional[QueryNode]:
        """Add a QueryNode and automatically create and connect its Input/Output TableBoxNodes."""
        qinfo = self.report.get_query(query_name)
        if not qinfo:
            logger.warning(f"Query {query_name} not found in report.")
            return None

        if pos is None:
            self._node_counter += 1
            pos = (200 * (self._node_counter % 4), 120 * (self._node_counter % 4))

        x, y = pos

        # Create Query Node
        qnode: QueryNode = self.graph.create_node(
            "reporting.nodes.QueryNode",
            name=qinfo.name,
            pos=[x, y],
        )
        qnode.set_property("query_name", qinfo.name)
        qnode.set_property("query_path", str(qinfo.file_path.resolve()))
        qnode.set_property("report_name", self.report.name)
        qnode.set_property("parameters", ", ".join(qinfo.parameter_names))

        # Automatically create and connect input tables box if there are input tables
        if qinfo.input_tables:
            in_table_node: TableBoxNode = self.graph.create_node(
                "reporting.nodes.TableBoxNode",
                name=f"{qinfo.name} [In]",
                pos=[x - 320, y - 40],
            )
            in_table_node.setup_as_input(qinfo.input_tables)
            in_table_node.set_display_mode(self.show_full_table_names)
            try:
                in_table_node.get_output("out_tables").connect_to(qnode.get_input("tables_in"))
            except Exception as e:
                logger.debug(f"Could not connect input tables port: {e}")

        # Automatically create and connect output tables box if there are output tables (dark orange)
        if qinfo.output_tables:
            out_table_node: TableBoxNode = self.graph.create_node(
                "reporting.nodes.TableBoxNode",
                name=f"{qinfo.name} [Out]",
                pos=[x + 280, y - 40],
            )
            out_table_node.setup_as_output(qinfo.output_tables)
            out_table_node.set_display_mode(self.show_full_table_names)
            try:
                qnode.get_output("tables_out").connect_to(out_table_node.get_input("in_tables"))
            except Exception as e:
                logger.debug(f"Could not connect output tables port: {e}")

        # Automatically create and connect output CSV box if query has output CSV (dark purple)
        if getattr(qinfo, "has_output_csv", False) or getattr(qinfo, "output_csv_tables", None):
            y_offset = -40 if not qinfo.output_tables else 80
            csv_table_node: TableBoxNode = self.graph.create_node(
                "reporting.nodes.TableBoxNode",
                name=f"{qinfo.name} [CSV]",
                pos=[x + 280, y + y_offset],
            )
            # Display the output CSV file name instead of the table name
            csv_file_name = f"{qinfo.name}.csv"
            csv_table_node.setup_as_csv_output(csv_file_name)
            csv_table_node.set_display_mode(self.show_full_table_names)
            try:
                csv_port = qnode.get_output("csv_out") or qnode.get_output("tables_out")
                csv_port.connect_to(csv_table_node.get_input("in_tables"))
            except Exception as e:
                logger.debug(f"Could not connect output CSV port: {e}")

        return qnode

    def _on_node_double_clicked(self, node) -> None:
        """Handle double click on query node to open in OS default application."""
        if isinstance(node, QueryNode) or node.type_ == "reporting.nodes.QueryNode":
            query_path = node.get_property("query_path")
            if query_path and Path(query_path).exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(query_path))
            else:
                self._open_query_file(node.name())

    def _open_query_file(self, query_name: str) -> None:
        """Open query .sql file with default system application."""
        qinfo = self.report.get_query(query_name)
        if qinfo and qinfo.file_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(qinfo.file_path.resolve())))

    def _on_delete_selected(self) -> None:
        """Delete currently selected nodes from the canvas."""
        selected_nodes = self.graph.selected_nodes()
        if selected_nodes:
            self.graph.delete_nodes(selected_nodes)
            self.status_bar.showMessage(f"Deleted {len(selected_nodes)} node(s).", 3000)

    def _on_save(self) -> None:
        """Save process flow graph state, parameter defaults, and table display mode."""
        session_data = self.graph.serialize_session()

        # Extract all query names present in the graph
        active_query_names: List[str] = []
        for node in self.graph.all_nodes():
            if isinstance(node, QueryNode) or node.type_ == "reporting.nodes.QueryNode":
                qname = node.get_property("query_name") or node.name()
                if qname not in active_query_names:
                    active_query_names.append(qname)

        # Retain or initialize parameter defaults
        existing_defaults = self.flow_controller.parameter_defaults
        for param in self.flow_controller.get_unique_parameters(active_query_names):
            if param not in existing_defaults:
                existing_defaults[param] = ""

        self.flow_controller.save_flow(
            active_query_names=active_query_names,
            parameter_defaults=existing_defaults,
            graph_session=session_data,
            show_full_table_names=self.show_full_table_names,
        )

        if self.app_controller:
            self.app_controller.scan()

        self.status_bar.showMessage("Process flow saved successfully.", 4000)
        QMessageBox.information(self, "Saved", f"Process flow '{self.flow_controller.flow_name}' saved.")

    def _on_run_flow(self) -> None:
        """Execute all queries in the process flow in topological order."""
        session_data = self.graph.serialize_session()
        queries_order = self.flow_controller.get_execution_order(session_data)

        if not queries_order:
            QMessageBox.information(self, "Empty Flow", "There are no queries in this process flow to run.")
            return

        # Consolidate all unique parameters across the queries
        unique_params = self.flow_controller.get_unique_parameters(queries_order)
        param_values = dict(self.flow_controller.parameter_defaults)

        if unique_params:
            # Build mock QueryInfo to reuse the QueryRunDialog
            combined_qinfo = QueryInfo(
                name=f"Flow: {self.flow_controller.flow_name}",
                file_path=self.flow_controller.file_path,
                report_name=self.report.name,
                parameters=[QueryParameter(name=p, default_value=param_values.get(p, "")) for p in unique_params],
            )
            dialog = QueryRunDialog(
                query_info=combined_qinfo,
                initial_defaults=param_values,
                parent=self,
            )
            dialog.setWindowTitle(f"Run Process Flow - {self.flow_controller.flow_name}")
            dialog.run_btn.setText("Run Process Flow")

            if dialog.exec() != QueryRunDialog.Accepted:
                return

            param_values = dialog.get_parameter_values()
            self.flow_controller.parameter_defaults.update(param_values)

        outputs_dir = self.report.folder_path / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        results_log = []
        errors = []

        self.status_bar.showMessage(f"Running process flow ({len(queries_order)} queries)...")

        for idx, qname in enumerate(queries_order, start=1):
            qinfo = self.report.get_query(qname)
            if not qinfo or not qinfo.file_path.exists():
                err = f"Query '{qname}' SQL file not found."
                errors.append(err)
                results_log.append(f"[{idx}/{len(queries_order)}] ❌ {qname}: {err}")
                break

            self.status_bar.showMessage(f"[{idx}/{len(queries_order)}] Running {qname}...")
            QtWidgets.QApplication.processEvents()

            try:
                res = run_bigquery_script(
                    sql_script_path=qinfo.file_path,
                    report_name=self.report.name,
                    outputs_dir=outputs_dir,
                    parameters=param_values,
                )
                if res.get("is_export"):
                    results_log.append(f"[{idx}/{len(queries_order)}] ✅ {qname}: Exported {res.get('row_count', 0):,} rows to {res.get('output_file')}")
                else:
                    results_log.append(f"[{idx}/{len(queries_order)}] ✅ {qname}: Executed table creation/update in BigQuery")
            except Exception as e:
                err_msg = str(e)
                errors.append(f"{qname}: {err_msg}")
                results_log.append(f"[{idx}/{len(queries_order)}] ❌ {qname}: Failed ({err_msg})")
                break

        summary_text = "\n".join(results_log)
        self.status_bar.showMessage("Process flow execution finished.", 5000)

        if errors:
            QMessageBox.critical(
                self,
                "Process Flow Execution Error",
                f"Execution failed with errors:\n\n{summary_text}\n\nNote: If authentication failed, please run 'gcloud auth application-default login' in terminal.",
            )
        else:
            QMessageBox.information(
                self,
                "Process Flow Completed",
                f"Process flow '{self.flow_controller.flow_name}' completed successfully!\n\n{summary_text}",
            )

    # Left panel query management callbacks
    def _on_query_added(self, query_name: str) -> None:
        if self.app_controller:
            qinfo = self.app_controller.add_query(query_name)
            self.report = self.app_controller.active_report or self.report
            self._refresh_left_queries()
            if qinfo:
                self.add_query_to_canvas(qinfo.name)

    def _on_query_removed(self, query_name: str) -> None:
        if self.app_controller:
            self.app_controller.remove_query(query_name)
            self.report = self.app_controller.active_report or self.report
            self._refresh_left_queries()
            nodes_to_remove = [
                n for n in self.graph.all_nodes()
                if n.name() == query_name or n.name().startswith(f"{query_name} [")
            ]
            if nodes_to_remove:
                self.graph.delete_nodes(nodes_to_remove)

    def _on_query_renamed(self, old_name: str, new_name: str) -> None:
        if self.app_controller:
            self.app_controller.rename_query(old_name, new_name)
            self.report = self.app_controller.active_report or self.report
            self._refresh_left_queries()
            for n in self.graph.all_nodes():
                if n.name() == old_name:
                    n.set_name(new_name)
                    n.set_property("query_name", new_name)

    def _handle_query_drop(self, query_name: str, scene_x: float, scene_y: float) -> None:
        """Add dropped query node at scene position with deduplication."""
        import time
        now = time.time()
        last_info = getattr(self, "_last_drop_info", None)
        if last_info == (query_name, round(scene_x, -1), round(scene_y, -1)):
            if now - getattr(self, "_last_drop_time", 0) < 0.5:
                return
        self._last_drop_info = (query_name, round(scene_x, -1), round(scene_y, -1))
        self._last_drop_time = now

        if self.app_controller:
            self.report = self.app_controller.active_report or self.report

        self.add_query_to_canvas(query_name, pos=(scene_x, scene_y))

    def _on_graph_data_dropped(self, mimedata, pos) -> None:
        """Handle fallback drop notification emitted by NodeGraph."""
        query_name = ""
        if mimedata.hasFormat("application/x-query-name"):
            query_name = bytes(mimedata.data("application/x-query-name")).decode("utf-8")
        elif mimedata.hasText():
            text = mimedata.text().strip()
            if text.startswith("query:"):
                query_name = text[len("query:"):].strip()
            elif self.report.get_query(text):
                query_name = text

        if query_name:
            scene_x = pos.x() if hasattr(pos, "x") else 0
            scene_y = pos.y() if hasattr(pos, "y") else 0
            self._handle_query_drop(query_name, scene_x, scene_y)

    # Drag and Drop handling on the NodeGraph viewer
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        try:
            viewer = self.graph.viewer()
        except RuntimeError:
            return False

        viewport = viewer.viewport() if hasattr(viewer, "viewport") else None
        target_widgets = {viewer, viewport, getattr(self, "graph_widget", None)} - {None}

        if watched in target_widgets:
            if event.type() in (QEvent.DragEnter, QEvent.DragMove):
                mime = event.mimeData()
                if (
                    mime.hasFormat("application/x-query-name")
                    or (mime.hasText() and mime.text().startswith("query:"))
                    or (mime.hasText() and self.report.get_query(mime.text().strip()) is not None)
                ):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop:
                mime = event.mimeData()
                query_name = ""
                if mime.hasFormat("application/x-query-name"):
                    query_name = bytes(mime.data("application/x-query-name")).decode("utf-8")
                elif mime.hasText():
                    text = mime.text().strip()
                    if text.startswith("query:"):
                        query_name = text[len("query:"):].strip()
                    elif self.report.get_query(text):
                        query_name = text

                if query_name:
                    pos = event.pos()
                    if watched == viewport:
                        scene_pos = viewer.mapToScene(pos)
                    elif viewport:
                        scene_pos = viewer.mapToScene(viewport.mapFromGlobal(watched.mapToGlobal(pos)))
                    else:
                        scene_pos = viewer.mapToScene(pos)

                    self._handle_query_drop(query_name, scene_pos.x(), scene_pos.y())
                    event.acceptProposedAction()
                    return True

        return super().eventFilter(watched, event)

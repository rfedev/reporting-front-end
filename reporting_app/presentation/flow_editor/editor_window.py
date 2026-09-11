"""Process Flow Editor window with Left Panel, Main NodeGraphQt Canvas, and Right Panel."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QUrl, Qt
from PySide6.QtGui import QCursor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from NodeGraphQt import BaseNode, NodeGraph
from NodeGraphQt.constants import PipeEnum
from NodeGraphQt.qgraphics.pipe import PipeItem
from NodeGraphQt.qgraphics.port import PortItem

from reporting_app.controllers.app_controller import AppController
from reporting_app.controllers.flow_controller import ProcessFlowController
from reporting_app.core.bigquery_run import run_bigquery_script, run_bigquery_import_csv
from reporting_app.core.models import ProcessFlowInfo, QueryInfo, QueryParameter, Report
from reporting_app.core.sql_parser import sync_query_csv_comments, update_query_csv_comment
from reporting_app.presentation.flow_editor.graph_builder import ProcessFlowGraphBuilder
from reporting_app.presentation.flow_editor.nodes import ImportCsvNode, QueryNode, TableBoxNode
from reporting_app.presentation.flow_editor.parameter_overlay import CanvasParameterOverlay
from reporting_app.presentation.flow_editor.query_tree_widget import QueryManagementPanel
from reporting_app.presentation.query_dialog import QueryRunDialog

logger = logging.getLogger(__name__)


class SelectOutputTablesDialog(QDialog):
    """Dialog for selecting which tables to export to CSV when multiple tables exist in an output TableBox."""

    def __init__(self, raw_tables: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Select Tables")
        self.resize(360, 280)
        self.raw_tables = list(raw_tables)
        self.items: List[Tuple[str, QListWidgetItem]] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header = QLabel("<b>Select tables to output:</b>")
        layout.addWidget(header)

        self.list_widget = QListWidget(self)
        for t in self.raw_tables:
            clean_name = t.split(".")[-1] if "." in t else t
            item = QListWidgetItem(clean_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list_widget.addItem(item)
            self.items.append((t, item))
        layout.addWidget(self.list_widget)

        # Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        ok_btn.clicked.connect(self.accept)
        btn_bar.addWidget(cancel_btn)
        btn_bar.addWidget(ok_btn)
        layout.addLayout(btn_bar)

    def get_selected_tables(self) -> List[str]:
        return [raw_t for raw_t, item in self.items if item.checkState() == Qt.Checked]


class ImportCsvDialog(QDialog):
    """Dialog window to configure CSV file import(s) into BigQuery table(s)."""

    def __init__(
        self,
        initial_imports: Optional[List[dict]] = None,
        workbench_dataset: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Import CSV Configuration")
        self.resize(650, 480)
        self.workbench_dataset = workbench_dataset.strip()
        self.rows: List[dict] = []
        self._build_ui(initial_imports or [])

    def _compute_auto_table(self, file_path: str) -> str:
        """Derive projectid.dataset.csvtablename from the CSV file path and workbench dataset."""
        if not file_path:
            return ""
        stem = Path(file_path).stem
        clean_stem = "".join(c if c.isalnum() or c == "_" else "_" for c in stem)
        if self.workbench_dataset:
            wb_prefix = self.workbench_dataset.rstrip(".") + "."
        else:
            wb_prefix = "projectid.dataset."
        return f"{wb_prefix}{clean_stem}"

    def _build_ui(self, initial_imports: List[dict]):
        main_layout = QVBoxLayout(self)

        header_label = QLabel(
            "<b>Configure CSV Import:</b><br>"
            "<i>Specify CSV file location, whether it has headers, and destination BigQuery output table address.</i>"
        )
        header_label.setWordWrap(True)
        main_layout.addWidget(header_label)

        # Scroll area for rows
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignTop)
        self.container_layout.setSpacing(12)
        scroll.setWidget(self.container)
        main_layout.addWidget(scroll)

        # Bottom buttons
        bottom_bar = QHBoxLayout()
        add_btn = QPushButton("➕ Add csv")
        add_btn.setToolTip("Add another CSV import section")
        add_btn.clicked.connect(lambda: self._add_row())
        bottom_bar.addWidget(add_btn)
        bottom_bar.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom_bar.addWidget(cancel_btn)
        bottom_bar.addWidget(ok_btn)
        main_layout.addLayout(bottom_bar)

        # Populate rows
        if initial_imports:
            for item in initial_imports:
                self._add_row(
                    csv_path=item.get("csv_path", ""),
                    has_headers=item.get("has_headers", True),
                    output_table=item.get("output_table", ""),
                )
        else:
            self._add_row()

    def _add_row(self, csv_path: str = "", has_headers: bool = True, output_table: str = ""):
        if not isinstance(csv_path, str):
            csv_path = ""
        if not isinstance(output_table, str):
            output_table = ""
        sec_num = len(self.rows) + 1
        section_box = QGroupBox(f"CSV Import #{sec_num}", self.container)
        section_box.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #555; border-radius: 6px; margin-top: 10px; padding: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #ddd; }"
        )
        row_layout = QVBoxLayout(section_box)
        row_layout.setSpacing(10)

        # Row 1: CSV file path + Browse + Headers checkbox + Delete
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("CSV File:"))
        path_edit = QLineEdit(csv_path)
        path_edit.setPlaceholderText("Select or enter CSV file path...")
        r1.addWidget(path_edit)

        headers_cb = QCheckBox("Headers")
        headers_cb.setChecked(has_headers)

        # Row 2: Output table text box
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Output Table:"))
        table_edit = QLineEdit(output_table)
        table_edit.setPlaceholderText("projectid.dataset.tablename")
        r2.addWidget(table_edit)

        # Auto-population when CSV file is selected or changed
        def on_path_changed(new_path: str):
            curr_table = table_edit.text().strip()
            # If table is empty or matches previous auto-populated value, update it
            if not curr_table or getattr(table_edit, "_is_auto_populated", False):
                auto_val = self._compute_auto_table(new_path)
                if auto_val:
                    table_edit.setText(auto_val)
                    table_edit._is_auto_populated = True

        path_edit.textChanged.connect(on_path_changed)

        browse_btn = QPushButton("Browse...")
        def pick_file():
            selected, _ = QFileDialog.getOpenFileName(
                self, "Select CSV File", "", "CSV Files (*.csv);;All Files (*)"
            )
            if selected:
                path_edit.setText(selected)
                auto_val = self._compute_auto_table(selected)
                if auto_val:
                    table_edit.setText(auto_val)
                    table_edit._is_auto_populated = True
        browse_btn.clicked.connect(pick_file)
        r1.addWidget(browse_btn)
        r1.addWidget(headers_cb)

        del_btn = QPushButton("🗑")
        del_btn.setToolTip("Remove this CSV import section")
        del_btn.setFixedWidth(32)
        r1.addWidget(del_btn)

        row_layout.addLayout(r1)
        row_layout.addLayout(r2)

        row_data = {
            "widget": section_box,
            "path_edit": path_edit,
            "headers_cb": headers_cb,
            "table_edit": table_edit,
            "del_btn": del_btn,
        }
        self.rows.append(row_data)
        self.container_layout.addWidget(section_box)
        section_box.show()
        self.container.adjustSize()

        def remove_this_row():
            if row_data in self.rows:
                self.rows.remove(row_data)
                section_box.setParent(None)
                section_box.deleteLater()
                self._update_section_titles()
                self.container.adjustSize()

        del_btn.clicked.connect(remove_this_row)
        self._update_section_titles()

    def _update_section_titles(self):
        for idx, r in enumerate(self.rows):
            r["widget"].setTitle(f"CSV Import #{idx + 1}")
            # Only allow deleting if more than 1 section
            r["del_btn"].setEnabled(len(self.rows) > 1)

    def get_imports(self) -> List[dict]:
        results = []
        for r in self.rows:
            csv_path = r["path_edit"].text().strip()
            table = r["table_edit"].text().strip()
            headers = r["headers_cb"].isChecked()
            if csv_path or table:
                results.append({
                    "csv_path": csv_path,
                    "has_headers": headers,
                    "output_table": table,
                })
        return results


class ProcessFlowEditorWindow(QMainWindow):
    """Visual Process Flow Editor built on NodeGraphQt."""

    def __init__(
        self,
        report: Report,
        flow_info: Optional[ProcessFlowInfo] = None,
        flow_name: Optional[str] = None,
        app_controller: Optional[AppController] = None,
        parent: Optional[QWidget] = None,
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

        self.setWindowTitle(f"Process Flow - {report.name} - {self.flow_controller.flow_name}")
        self.resize(1200, 750)

        self._node_counter = 0
        self._node_positions: Dict[str, Tuple[float, float]] = {}
        # Track pending query renames (original_disk_name -> current_working_name)
        self._pending_query_renames: Dict[str, str] = {}
        # Load persisted show_full_table_names state
        self.show_full_table_names = self.flow_controller.get_show_full_table_names()

        self._init_graph()
        self._build_ui()
        self._setup_canvas_pan_and_select()
        self._load_initial_graph()

        # Connect file watcher / report change signal to detect disk edits to queries
        if self.app_controller:
            self.app_controller.active_report_changed.connect(self._on_report_updated_from_controller)

    def _init_graph(self) -> None:
        """Initialize NodeGraphQt instance and register custom nodes."""
        self.graph = NodeGraph()
        self.graph.register_node(QueryNode)
        self.graph.register_node(TableBoxNode)
        self.graph.register_node(ImportCsvNode)

        # Wire node double click
        self.graph.node_double_clicked.connect(self._on_node_double_clicked)

        # Safeguard NodeGraphQt's _on_nodes_moved against KeyError if a node view was removed or recreated
        orig_on_nodes_moved = self.graph._on_nodes_moved

        def safe_on_nodes_moved(node_data):
            safe_data = {
                nv: prev_pos for nv, prev_pos in node_data.items()
                if getattr(nv, "id", None) in self.graph._model.nodes
            }
            if safe_data:
                orig_on_nodes_moved(safe_data)

        self.graph._on_nodes_moved = safe_on_nodes_moved
        try:
            self.graph.viewer().moved_nodes.disconnect()
            self.graph.viewer().moved_nodes.connect(safe_on_nodes_moved)
        except Exception:
            pass

        # Handle in-place title editing on the node
        def handle_query_title_change(target_node, new_name):
            if getattr(self, "_is_renaming_query", False):
                return
            if not target_node or not new_name:
                return
            old_name = target_node.get_property("query_name") or target_node.name()
            new_name = new_name.strip()
            if not new_name or new_name == old_name:
                return
            if isinstance(target_node, QueryNode) or getattr(target_node, "type_", "") == "reporting.nodes.QueryNode":
                self._on_query_renamed(old_name, new_name)

        def on_graph_property_changed(node, prop_name, value):
            if prop_name == "name":
                handle_query_title_change(node, str(value))

        def on_viewer_node_name_changed(node_id, new_name):
            target_node = None
            for n in self.graph.all_nodes():
                if n.id == node_id or (hasattr(n, "view") and getattr(n.view, "id", None) == node_id):
                    target_node = n
                    break
            handle_query_title_change(target_node, str(new_name))

        self.graph.property_changed.connect(on_graph_property_changed)
        try:
            self.graph.viewer().node_name_changed.connect(on_viewer_node_name_changed)
        except Exception as e:
            logger.debug(f"Could not connect viewer node_name_changed: {e}")

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
                    # Deselect everything in the process flow editor by clicking once on empty space
                    self.graph.clear_selection()
                    if viewer.scene():
                        viewer.scene().clearSelection()

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

        # Intercept live connection release on empty space
        orig_apply_live_connection = viewer.apply_live_connection

        def custom_apply_live_connection(event):
            start_port = getattr(viewer, "_start_port", None)
            pipe_visible = getattr(viewer, "_LIVE_PIPE", None) and viewer._LIVE_PIPE.isVisible()
            if pipe_visible and start_port is not None:
                # Check if mouse release lands on any existing port
                scene_pos = event.scenePos()
                end_port = None
                for item in viewer.scene().items(scene_pos):
                    if isinstance(item, PortItem):
                        end_port = item
                        break

                if end_port is None:
                    # Connection dropped onto empty canvas space
                    p_name = getattr(start_port, "name", "")
                    p_node_item = getattr(start_port, "node", None)
                    p_base_node = next((n for n in self.graph.all_nodes() if getattr(n, "view", None) == p_node_item), None)
                    orig_apply_live_connection(event)
                    if p_base_node is not None:
                        self._handle_empty_space_port_drop(p_base_node, p_name, scene_pos.x(), scene_pos.y())
                    return

            orig_apply_live_connection(event)

        viewer.apply_live_connection = custom_apply_live_connection

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
                local_pt = event.position().toPoint() if hasattr(event, "position") else event.pos()
                vp = viewer.viewport() if hasattr(viewer, "viewport") and viewer.viewport() else viewer
                scene_pos = viewer.mapToScene(local_pt)
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
        save_btn.setToolTip("Save Process Flow (Ctrl+S)")
        save_btn.clicked.connect(self._on_save)
        toolbar.addWidget(save_btn)

        toolbar.addSeparator()

        self.run_flow_btn = QPushButton("▶ Run Flow")
        self.run_flow_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        self.run_flow_btn.setToolTip("Execute process flow queries in dependency order")
        self.run_flow_btn.clicked.connect(self._on_run_flow)
        toolbar.addWidget(self.run_flow_btn)

        toolbar.addSeparator()

        import_csv_btn = QPushButton("📥 Import csv")
        import_csv_btn.setToolTip("Add an Import Query node to load CSV files into BigQuery tables")
        import_csv_btn.clicked.connect(self._on_add_import_csv)
        toolbar.addWidget(import_csv_btn)

        toolbar.addSeparator()

        # Appearance dropdown menu button
        self.appearance_btn = QToolButton(self)
        self.appearance_btn.setText("Appearance ☰")
        self.appearance_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self.appearance_btn.setPopupMode(QToolButton.InstantPopup)
        appearance_menu = QMenu(self.appearance_btn)

        action_layout_h = appearance_menu.addAction("Auto Layout (Horizontal)")
        action_layout_h.triggered.connect(lambda: self._auto_layout("horizontal"))

        action_layout_v = appearance_menu.addAction("Auto Layout (Vertical)")
        action_layout_v.triggered.connect(lambda: self._auto_layout("vertical"))

        appearance_menu.addSeparator()

        action_fit = appearance_menu.addAction("Fit Graph")
        action_fit.triggered.connect(self._fit_graph_to_canvas)

        appearance_menu.addSeparator()

        self.action_full_table_names = appearance_menu.addAction("Full Table Address")
        self.action_full_table_names.setCheckable(True)
        self.action_full_table_names.setChecked(self.show_full_table_names)
        self.action_full_table_names.triggered.connect(self._toggle_table_names_display)

        self.appearance_btn.setMenu(appearance_menu)
        toolbar.addWidget(self.appearance_btn)

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

        # Top-left expandable parameter overlay on the canvas (parented to viewer so canvas paint events do not occlude it)
        self.param_overlay = CanvasParameterOverlay(viewer)
        self.param_overlay.move(14, 14)
        self.param_overlay.show()
        self.param_overlay.raise_()

        self.splitter.addWidget(self.graph_widget)

        # Right Panel (Currently unused / placeholder)
        self.right_panel = QWidget(self)
        right_layout = QVBoxLayout(self.right_panel)
        right_label = QLabel("<b>Properties & Inspector</b><br><br><i>(Right panel currently unused)</i>")
        right_label.setAlignment(Qt.AlignCenter)
        right_label.setStyleSheet("color: #888888; font-size: 12px;")
        right_layout.addWidget(right_label)
        self.splitter.addWidget(self.right_panel)

        # Set initial splitter sizes (load from flow_controller or repository if present)
        saved_sizes = self.flow_controller.get_splitter_sizes()
        if not saved_sizes and self.app_controller and self.app_controller.repo:
            repo_sizes = self.app_controller.repo.get_setting("flow_editor_splitter_sizes", "")
            if repo_sizes:
                try:
                    saved_sizes = [int(s) for s in repo_sizes.split(",") if s.strip()]
                except Exception:
                    saved_sizes = None
        if saved_sizes and len(saved_sizes) == 3:
            self.splitter.setSizes(saved_sizes)
        else:
            self.splitter.setSizes([240, 800, 160])
        self.setCentralWidget(self.splitter)

        # Status bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Pan: Drag empty space | Multi-select: Ctrl+Drag | Double-click node to edit SQL.")

        # Shortcuts for Save and Delete
        self.save_shortcut = QShortcut(QKeySequence.Save, self)
        self.save_shortcut.activated.connect(self._on_save)
        self.del_shortcut = QShortcut(QKeySequence.Delete, self)
        self.del_shortcut.activated.connect(self._on_delete_selected)

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

    def _toggle_table_names_display(self) -> None:
        """Toggle full table address vs short table name across all TableBoxNodes."""
        self.show_full_table_names = self.action_full_table_names.isChecked()
        for node in self.graph.all_nodes():
            if isinstance(node, TableBoxNode) or node.type_ == "reporting.nodes.TableBoxNode":
                node.set_display_mode(self.show_full_table_names)

    def _fit_graph_to_canvas(self) -> None:
        """Fit nodes into the available canvas.

        If nodes are selected, fits around selected nodes.
        If nothing is selected, fits around all nodes.
        """
        viewer = self.graph.viewer()
        selected = self.graph.selected_nodes()
        nodes = selected if selected else self.graph.all_nodes()
        if not nodes:
            return

        node_items = [n.view for n in nodes if hasattr(n, "view") and n.view]
        if not node_items:
            return

        combined_rect = viewer._combined_rect(node_items)
        if combined_rect.isNull() or combined_rect.isEmpty():
            return

        # Add generous padding so nodes are never cut off by canvas edges
        padded_rect = combined_rect.adjusted(-60, -60, 60, 60)
        viewer._scene_range = QtCore.QRectF(padded_rect)
        viewer._update_scene()

    def _get_working_query(self, query_name: str) -> Optional[QueryInfo]:
        """Find a QueryInfo by current working name or original name, respecting pending renames."""
        # Check direct lookup first
        qinfo = self.report.get_query(query_name)
        if qinfo:
            return qinfo
        # Check if query_name is a working new name mapped from an original name
        for orig_name, curr_name in self._pending_query_renames.items():
            if curr_name == query_name:
                orig_q = self.report.get_query(orig_name)
                if orig_q:
                    # Return shallow copy with working name
                    from dataclasses import replace
                    return replace(orig_q, name=curr_name)
        return None

    def _refresh_left_queries(self) -> None:
        """Update the list of available queries in the left panel, respecting pending renames."""
        query_names = []
        for q in self.report.queries:
            name = self._pending_query_renames.get(q.name, q.name)
            query_names.append(name)
        self.left_panel.set_queries(query_names)

    def _sync_all_query_csv_comments(self) -> None:
        """Ensure all query files have synchronized # ouput: comments for standalone SELECT statements."""
        if not self.report or not self.report.folder_path:
            return
        existing_csvs: Set[str] = set()
        outputs_dir = self.report.folder_path / "outputs"
        if outputs_dir.exists() and outputs_dir.is_dir():
            for cf in outputs_dir.glob("*.csv"):
                existing_csvs.add(cf.name)

        for q in self.report.queries:
            if q.file_path and q.file_path.exists():
                csv_files = sync_query_csv_comments(q.file_path, existing_csvs)
                existing_csvs.update(csv_files)
                if csv_files:
                    q.output_csv_tables = csv_files
                    self.flow_controller.set_csv_filename(q.name, ", ".join(csv_files))

        # Update any active CSV nodes on canvas immediately
        for node in self.graph.all_nodes():
            if isinstance(node, TableBoxNode) or getattr(node, "type_", "") == "reporting.nodes.TableBoxNode":
                btype = node.get_property("box_type") or getattr(getattr(node, "view", None), "table_box_type", "")
                if btype == "Output CSV":
                    qname = node.get_property("query_owner") or node.name().replace(" [CSV]", "").strip()
                    qinfo = self.report.get_query(qname)
                    if qinfo and qinfo.output_csv_tables:
                        node.setup_as_csv_output(qinfo.output_csv_tables)
                        node.set_display_mode(self.show_full_table_names)

    def _sync_query_files_and_parameters(self) -> None:
        """Sync CSV comments and reload query parameters from disk if modified, updating canvas nodes and overlay."""
        self._sync_all_query_csv_comments()
        if not self.report:
            return

        # Check for query file modifications and re-parse parameters
        from reporting_app.core.sql_parser import scan_query_parameters, scan_query_tables
        for q in self.report.queries:
            if q.file_path and q.file_path.exists():
                try:
                    mtime = q.file_path.stat().st_mtime
                    if mtime != getattr(q, "mtime", None) or not q.parameters:
                        content = q.file_path.read_text(encoding="utf-8", errors="replace")
                        param_names = scan_query_parameters(content)
                        input_tables, output_tables, _ = scan_query_tables(content)
                        q.parameters = [QueryParameter(name=p) for p in param_names]
                        q.input_tables = input_tables
                        q.output_tables = output_tables
                        q.mtime = mtime

                        # Update node on canvas if present
                        for node in self.graph.all_nodes():
                            if isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode":
                                n_qname = node.get_property("query_name") or node.name()
                                if n_qname == q.name:
                                    node.set_parameters(param_names)
                except Exception as e:
                    logger.debug(f"Error syncing query file {q.file_path}: {e}")

        self._refresh_parameter_overlay()

    def _refresh_parameter_overlay(self) -> None:
        """Refresh flow parameter overlay with all unique parameters from active canvas queries."""
        if hasattr(self, "param_overlay"):
            active_qnames = [
                n.get_property("query_name") or n.name()
                for n in self.graph.all_nodes()
                if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
            ]
            if not active_qnames:
                active_qnames = self.flow_controller.get_query_names()

            unique_params: List[str] = []
            seen: set = set()
            for qn in active_qnames:
                wq = self._get_working_query(qn)
                if wq:
                    for p in wq.parameters:
                        if p.name not in seen:
                            seen.add(p.name)
                            unique_params.append(p.name)

            date_opt_defaults = self.flow_controller.flow_data.get("parameter_date_options", {})
            self.param_overlay.set_parameters(
                unique_params,
                current_defaults=self.flow_controller.parameter_defaults,
                date_option_defaults=date_opt_defaults,
            )
            self.param_overlay.raise_()

    def changeEvent(self, event: QEvent) -> None:
        """Detect window focus/activation to sync any external query file edits immediately."""
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._sync_query_files_and_parameters()

    def _load_initial_graph(self) -> None:
        """Load saved session or automatically add flow queries if brand new."""
        self._sync_query_files_and_parameters()
        session_data = self.flow_controller.get_graph_session()
        view_state = self.flow_controller.get_view_state()
        viewer = self.graph.viewer()

        def apply_view_state():
            self._fit_graph_to_canvas()

        if session_data and "nodes" in session_data and session_data["nodes"]:
            try:
                self.graph.deserialize_session(session_data)
                # Re-apply text, display mode, and query parameters to restored nodes
                for node in self.graph.all_nodes():
                    if isinstance(node, TableBoxNode) or node.type_ == "reporting.nodes.TableBoxNode":
                        btype = node.get_property("box_type") or "Table Box"
                        node.view.set_custom_title(btype)
                        if btype == "Output CSV":
                            qname = node.get_property("query_owner") or node.name().replace(" [CSV]", "").strip()
                            qinfo = self.report.get_query(qname)
                            if qinfo and qinfo.output_csv_tables:
                                node.setup_as_csv_output(qinfo.output_csv_tables)
                        node.set_display_mode(self.show_full_table_names)
                    elif isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode":
                        qname = node.get_property("query_name") or node.name()
                        qinfo = self.report.get_query(qname)
                        if qinfo:
                            node.set_parameters(qinfo.parameter_names)
                # Re-apply custom noodle colors to all pipes in the restored scene
                for item in self.graph.viewer().scene().items():
                    if isinstance(item, PipeItem):
                        item.reset()

                # Refresh parameter overlay for active queries in the flow
                if hasattr(self, "param_overlay"):
                    active_qnames = [
                        n.get_property("query_name") or n.name()
                        for n in self.graph.all_nodes()
                        if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
                    ]
                    if not active_qnames:
                        active_qnames = self.flow_controller.get_query_names()
                    unique_params = self.flow_controller.get_unique_parameters(active_qnames)
                    date_opt_defaults = self.flow_controller.flow_data.get("parameter_date_options", {})
                    self.param_overlay.set_parameters(
                        unique_params,
                        current_defaults=self.flow_controller.parameter_defaults,
                        date_option_defaults=date_opt_defaults,
                    )
                    self.param_overlay.raise_()

                QtCore.QTimer.singleShot(100, apply_view_state)
                return
            except Exception as e:
                logger.error(f"Error restoring node session: {e}")

        # If empty session but query names exist in flow definition, create nodes for them
        flow_query_names = self.flow_controller.get_query_names()
        queries_to_add = [self.report.get_query(q) for q in flow_query_names if self.report.get_query(q)]
        csv_imports = self.flow_controller.get_csv_imports()
        if queries_to_add or csv_imports:
            ProcessFlowGraphBuilder.rebuild_graph(
                self.graph,
                queries_to_add,
                self._node_positions,
                self.show_full_table_names,
                self.flow_controller.get_csv_filenames(),
                import_csv_data=csv_imports,
            )

        # Refresh parameter overlay for active queries in the flow
        if hasattr(self, "param_overlay"):
            active_qnames = [
                n.get_property("query_name") or n.name()
                for n in self.graph.all_nodes()
                if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
            ]
            if not active_qnames:
                active_qnames = self.flow_controller.get_query_names()
            unique_params = self.flow_controller.get_unique_parameters(active_qnames)
            date_opt_defaults = self.flow_controller.flow_data.get("parameter_date_options", {})
            self.param_overlay.set_parameters(
                unique_params,
                current_defaults=self.flow_controller.parameter_defaults,
                date_option_defaults=date_opt_defaults,
            )
            self.param_overlay.raise_()

        QtCore.QTimer.singleShot(100, apply_view_state)

    def _on_add_import_csv(self, pos: Optional[Tuple[float, float]] = None) -> Optional[ImportCsvNode]:
        """Add an Import Query node to the canvas."""
        existing_names = {
            n.name() for n in self.graph.all_nodes()
            if isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
        }
        node_name = "Import csv"
        idx = 2
        while node_name in existing_names:
            node_name = f"Import csv {idx}"
            idx += 1

        if pos is None:
            all_nodes = self.graph.all_nodes()
            if all_nodes:
                max_x = max((n.pos()[0] for n in all_nodes), default=0.0)
                min_y = min((n.pos()[1] for n in all_nodes), default=0.0)
                node_pos = [max_x + 500.0, min_y]
            else:
                node_pos = [0.0, 0.0]
        else:
            node_pos = [pos[0], pos[1]]

        inode: ImportCsvNode = self.graph.create_node(
            "reporting.nodes.ImportCsvNode",
            name=node_name,
            pos=node_pos,
        )
        self._node_positions[node_name] = (node_pos[0], node_pos[1])
        accepted = self._show_import_csv_dialog(inode)
        if not accepted:
            self.graph.delete_node(inode)
            self._node_positions.pop(node_name, None)
            return None

        # Retrieve live node after topology sync
        live_inode = next(
            (
                n for n in self.graph.all_nodes()
                if (isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode")
                and n.name() == node_name
            ),
            None,
        )
        return live_inode or inode

    def _show_import_csv_dialog(self, node: ImportCsvNode) -> bool:
        """Show configuration dialog for ImportCsvNode."""
        current_imports = node.get_imports()
        wb_dataset = ""
        if self.flow_controller and getattr(self.flow_controller, "repo", None):
            wb_dataset = self.flow_controller.repo.get_workbench_dataset()
        elif self.app_controller and getattr(self.app_controller, "repo", None):
            wb_dataset = self.app_controller.repo.get_workbench_dataset()

        dialog = ImportCsvDialog(initial_imports=current_imports, workbench_dataset=wb_dataset, parent=self)
        if dialog.exec() == QDialog.Accepted:
            new_imports = dialog.get_imports()
            node.set_imports(new_imports)
            self._sync_graph_topology()
            self.status_bar.showMessage("Updated CSV import configuration.", 3000)
            return True
        return False

    def _collect_csv_import_data(self) -> List[dict]:
        """Collect all CSV import definitions from canvas nodes."""
        data = []
        for node in self.graph.all_nodes():
            if isinstance(node, ImportCsvNode) or getattr(node, "type_", "") == "reporting.nodes.ImportCsvNode":
                data.append({
                    "node_name": node.name(),
                    "items": node.get_imports(),
                })
        return data

    def _sync_graph_topology(self, additional_query: Optional[str] = None) -> None:
        """Reconcile and synchronize the graph topology for all active query nodes on the canvas."""
        self._sync_query_files_and_parameters()
        # Find all active query names currently on the canvas
        active_query_names: List[str] = []
        for node in self.graph.all_nodes():
            if isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode":
                qname = node.get_property("query_name") or node.name()
                if qname and qname not in active_query_names:
                    active_query_names.append(qname)

        if additional_query and additional_query not in active_query_names:
            active_query_names.append(additional_query)

        active_queries = [self._get_working_query(name) for name in active_query_names if self._get_working_query(name)]
        csv_imports = self._collect_csv_import_data()

        # Reconstruct graph according to Process Flow Graph Logic
        self._node_positions = ProcessFlowGraphBuilder.rebuild_graph(
            self.graph,
            active_queries,
            self._node_positions,
            self.show_full_table_names,
            self.flow_controller.get_csv_filenames(),
            import_csv_data=csv_imports,
        )

        # Refresh parameter overlay for active queries in the flow
        self._refresh_parameter_overlay()

    def add_query_to_canvas(self, query_name: str, pos: Optional[tuple] = None) -> Optional[QueryNode]:
        """Add a query to the canvas and recalculate the process flow graph topology."""
        qinfo = self._get_working_query(query_name)
        if not qinfo:
            logger.warning(f"Query {query_name} not found in report.")
            return None

        # Check if already on canvas
        for node in self.graph.all_nodes():
            if (isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode"):
                existing_name = node.get_property("query_name") or node.name()
                if existing_name == query_name:
                    if pos is not None:
                        node.set_pos(pos[0], pos[1])
                        self._node_positions[query_name] = (pos[0], pos[1])
                    return node

        if pos is None:
            all_nodes = self.graph.all_nodes()
            if all_nodes:
                max_x = max((n.pos()[0] for n in all_nodes), default=0.0)
                min_y = min((n.pos()[1] for n in all_nodes), default=0.0)
                pos = (max_x + 750.0, min_y)
            else:
                pos = (0.0, 0.0)

        self._node_positions[query_name] = (pos[0], pos[1])
        self._sync_graph_topology(additional_query=query_name)

        # Return the created query node
        for node in self.graph.all_nodes():
            if (isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode"):
                if (node.get_property("query_name") or node.name()) == query_name:
                    return node
        return None

    def _handle_empty_space_port_drop(self, source_node: BaseNode, port_name: str, drop_x: float, drop_y: float) -> None:
        """Handle dragging a connector noodle and releasing it onto empty canvas space."""
        # 1. QueryNode run_out -> Add Query dialog, create query at drop pos, wire run_out to new query run_in
        if isinstance(source_node, QueryNode) or getattr(source_node, "type_", "") == "reporting.nodes.QueryNode":
            if port_name == "run_out":
                name, ok = QInputDialog.getText(
                    self,
                    "Add Query",
                    "Enter new query name (without .sql extension):",
                )
                if ok and name.strip():
                    query_name = name.strip()
                    if self.app_controller:
                        self.app_controller.add_query(query_name)
                        self.report = self.app_controller.active_report or self.report
                        self._refresh_left_queries()
                    new_qnode = self.add_query_to_canvas(query_name, pos=(drop_x, drop_y))
                    if new_qnode:
                        # Re-fetch source node after topology sync
                        src = next((n for n in self.graph.all_nodes() if n.name() == source_node.name()), None)
                        if src and src.get_output("run_out") and new_qnode.get_input("run_in"):
                            try:
                                src.get_output("run_out").connect_to(new_qnode.get_input("run_in"))
                            except Exception as e:
                                logger.debug(f"Could not connect run_out to run_in: {e}")
                return

            # 2. QueryNode run_in -> Import csv configuration dialog, create ImportCsvNode at drop pos, wire to run_in
            elif port_name == "run_in":
                orig_node_name = source_node.name()
                inode = self._on_add_import_csv(pos=(drop_x, drop_y))
                if inode:
                    # Update SQL of the downstream query node to include queries for created tables
                    qinfo = self.report.get_query(orig_node_name)
                    if qinfo and qinfo.file_path and qinfo.file_path.exists():
                        existing_sql = qinfo.file_path.read_text(encoding="utf-8")
                        new_snippets = []
                        for item in inode.get_imports():
                            tbl = item.get("output_table", "").strip()
                            if tbl and tbl not in existing_sql:
                                snippet = f"# imported csv {tbl}\nselect * from `{tbl}`;"
                                new_snippets.append(snippet)
                        if new_snippets:
                            separator = "\n\n" if existing_sql.strip() else ""
                            updated_sql = existing_sql.rstrip() + separator + "\n\n".join(new_snippets) + "\n"
                            qinfo.file_path.write_text(updated_sql, encoding="utf-8")
                            if self.app_controller:
                                self.app_controller.scan()
                                self.report = self.app_controller.active_report or self.report
                            self._sync_graph_topology()

                    src = next((n for n in self.graph.all_nodes() if n.name() == orig_node_name), None)
                    live_inode = next(
                        (
                            n for n in self.graph.all_nodes()
                            if (isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode")
                            and n.name() == inode.name()
                        ),
                        inode,
                    )
                    if src and live_inode and live_inode.get_output("run_out") and src.get_input("run_in"):
                        try:
                            live_inode.get_output("run_out").connect_to(src.get_input("run_in"))
                        except Exception as e:
                            logger.debug(f"Could not connect import run_out to query run_in: {e}")
                return

        # 3. Output Table TableBoxNode out_tables -> create output-csv query node
        if isinstance(source_node, TableBoxNode) or getattr(source_node, "type_", "") == "reporting.nodes.TableBoxNode":
            box_type = source_node.get_property("box_type") or getattr(getattr(source_node, "view", None), "table_box_type", "")
            if port_name == "out_tables" and box_type == "Output Tables":
                raw_tables = getattr(source_node, "raw_tables", [])
                if not raw_tables:
                    # Try from property
                    raw_json = source_node.get_property("raw_tables_json") or "[]"
                    try:
                        raw_tables = json.loads(raw_json)
                    except Exception:
                        raw_tables = []

                if not raw_tables:
                    return

                selected_tables: List[str] = []
                if len(raw_tables) > 1:
                    dlg = SelectOutputTablesDialog(raw_tables, self)
                    if dlg.exec() != QDialog.Accepted:
                        return
                    selected_tables = dlg.get_selected_tables()
                    if not selected_tables:
                        return
                else:
                    selected_tables = [raw_tables[0]]

                # Create query node(s) for the selected table(s)
                created_nodes: List[QueryNode] = []
                curr_y = drop_y
                for idx, tbl in enumerate(selected_tables):
                    clean_tbl_name = tbl.split(".")[-1] if "." in tbl else tbl
                    # Determine base query name
                    if len(raw_tables) > 1:
                        target_name = f"output-csv-{clean_tbl_name}"
                    else:
                        target_name = "output-csv"

                    # Ensure unique query name across report
                    existing_qnames = {q.name for q in self.report.queries}
                    final_qname = target_name
                    c = 1
                    while final_qname in existing_qnames:
                        c += 1
                        final_qname = f"{target_name}-{c:02d}"

                    template_sql = (
                        "# Ouput table to csv\n"
                        "select * \n"
                        f"from `{tbl}`\n"
                        ";\n"
                    )

                    if self.app_controller:
                        self.app_controller.add_query(final_qname, template_sql=template_sql)
                        self.report = self.app_controller.active_report or self.report
                        self._refresh_left_queries()

                    qnode = self.add_query_to_canvas(final_qname, pos=(drop_x, curr_y))
                    if qnode:
                        created_nodes.append(qnode)
                    curr_y += 180.0
                return

    def _on_report_updated_from_controller(self, new_report: Optional[Report]) -> None:
        """Handle disk modifications detected by the application controller/file watcher."""
        if getattr(self, "_is_renaming_query", False):
            return
        if not new_report or new_report.name != self.report.name:
            return

        self.report = new_report
        self.flow_controller.report = new_report
        self._refresh_left_queries()
        self._sync_graph_topology()

    def _on_node_double_clicked(self, node) -> None:
        """Handle double click on query node to open in OS default application, or CSV box to edit names."""
        if isinstance(node, QueryNode) or node.type_ == "reporting.nodes.QueryNode":
            query_path = node.get_property("query_path")
            if query_path and Path(query_path).exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(query_path))
            else:
                self._open_query_file(node.name())
        elif isinstance(node, ImportCsvNode) or node.type_ == "reporting.nodes.ImportCsvNode":
            self._show_import_csv_dialog(node)
        elif isinstance(node, TableBoxNode) or node.type_ == "reporting.nodes.TableBoxNode":
            btype = node.get_property("box_type") or getattr(getattr(node, "view", None), "table_box_type", "")
            if btype == "Output CSV":
                self._show_csv_rename_dialog(node)

    def _show_csv_rename_dialog(self, target_node: Optional[TableBoxNode] = None) -> None:
        """Show a dialog with text inputs for output CSVs (scoped to target_node if provided, or all active CSV nodes)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Output CSV Filenames")
        dialog.resize(480, 320)
        dlg_layout = QVBoxLayout(dialog)

        desc = QLabel(
            "<b>Output CSV Filenames:</b><br>"
            "<i>Files are saved to the report's outputs/ folder:</i>"
        )
        desc.setWordWrap(True)
        dlg_layout.addWidget(desc)

        # If target_node is passed, only show CSVs for that specific Output CSV box; otherwise all active CSV nodes
        if target_node:
            active_csv_nodes = [target_node]
        else:
            active_csv_nodes = [
                n for n in self.graph.all_nodes()
                if (isinstance(n, TableBoxNode) or n.type_ == "reporting.nodes.TableBoxNode")
                and (n.get_property("box_type") == "Output CSV" or getattr(getattr(n, "view", None), "table_box_type", "") == "Output CSV")
            ]

        # Form layout of CSV inputs
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        row_edits: List[dict] = []
        row_counter = 1

        for cnode in active_csv_nodes:
            qname = cnode.get_property("query_owner")
            if not qname:
                raw_name = cnode.name()
                qname = raw_name.replace(" [CSV]", "").strip()

            tables = list(cnode.raw_tables) if cnode.raw_tables else [f"{qname}.csv"]
            for idx_in_node, cur_file in enumerate(tables):
                clean_name = Path(cur_file).name
                row = QHBoxLayout()
                lbl = QLabel(f"<b>Query{row_counter:02d}:</b>")
                lbl.setToolTip(f"{qname} (Output #{idx_in_node + 1})")
                ledit = QLineEdit(clean_name)
                row.addWidget(lbl)
                row.addWidget(ledit)
                form_layout.addLayout(row)

                row_edits.append({
                    "cnode": cnode,
                    "qname": qname,
                    "idx_in_node": idx_in_node,
                    "edit": ledit,
                })
                row_counter += 1

        dlg_layout.addWidget(form_widget)
        dlg_layout.addStretch()

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Update")
        save_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        cancel_btn = QPushButton("Cancel")
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        dlg_layout.addLayout(btn_row)

        save_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)

        if dialog.exec() == QDialog.Accepted:
            node_files_map: Dict[Any, List[str]] = {
                cnode: list(cnode.raw_tables or [f"{cnode.name()}.csv"])
                for cnode in active_csv_nodes
            }

            for item in row_edits:
                new_val = item["edit"].text().strip()
                if not new_val:
                    continue
                new_val = Path(new_val).name
                if not new_val.lower().endswith(".csv"):
                    new_val = f"{new_val}.csv"

                cnode = item["cnode"]
                idx_in_node = item["idx_in_node"]
                qname = item["qname"]

                if idx_in_node < len(node_files_map[cnode]):
                    node_files_map[cnode][idx_in_node] = new_val
                else:
                    node_files_map[cnode].append(new_val)

                # Synchronize comment in query .sql file
                qinfo = self.report.get_query(qname)
                if qinfo and qinfo.file_path and qinfo.file_path.exists():
                    update_query_csv_comment(qinfo.file_path, select_index=idx_in_node, new_filename=new_val)
                    if idx_in_node < len(qinfo.output_csv_tables):
                        qinfo.output_csv_tables[idx_in_node] = new_val

            # Update nodes on canvas and flow controller
            for cnode, new_files in node_files_map.items():
                cnode.setup_as_csv_output(new_files)
                cnode.set_display_mode(self.show_full_table_names)
                qname = cnode.get_property("query_owner") or cnode.name().replace(" [CSV]", "").strip()
                self.flow_controller.set_csv_filename(qname, ", ".join(new_files))

            self.status_bar.showMessage("Updated output CSV filenames.", 3000)

    def _open_query_file(self, query_name: str) -> None:
        """Open query .sql file with default system application."""
        qinfo = self._get_working_query(query_name)
        if qinfo and qinfo.file_path.exists():
            file_str = str(qinfo.file_path.resolve())
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(file_str))
            if not opened:
                import subprocess
                try:
                    subprocess.Popen(["xdg-open", file_str])
                except Exception as e:
                    logger.warning(f"Failed to open with xdg-open: {e}")

    def _on_delete_selected(self) -> None:
        """Delete currently selected query or CSV import nodes and resynchronize topology.

        Tables are automatically managed and cannot be directly deleted.
        """
        selected_nodes = self.graph.selected_nodes()
        deletable_nodes = [
            n for n in selected_nodes
            if isinstance(n, (QueryNode, ImportCsvNode))
            or getattr(n, "type_", "") in ("reporting.nodes.QueryNode", "reporting.nodes.ImportCsvNode")
        ]
        if deletable_nodes:
            # If any ImportCsvNode is deleted, update saved csv_imports
            deleted_import_names = {
                n.name() for n in deletable_nodes
                if isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
            }
            if deleted_import_names:
                current_imports = self.flow_controller.get_csv_imports()
                remaining_imports = [
                    imp for imp in current_imports
                    if imp.get("node_name") not in deleted_import_names
                ]
                self.flow_controller.set_csv_imports(remaining_imports)

            self.graph.delete_nodes(deletable_nodes)
            self._sync_graph_topology()
            self.status_bar.showMessage(f"Deleted {len(deletable_nodes)} node(s).", 3000)
        elif selected_nodes:
            self.status_bar.showMessage(
                "Table boxes are managed automatically; only query and CSV import nodes can be deleted.", 3000
            )

    def _auto_layout(self, direction: str = "horizontal") -> None:
        """Auto-format layout or align objects horizontally or vertically.

        If objects are selected, only aligns the selected objects.
        If nothing is selected, aligns all objects (queries, table boxes, import nodes).
        """
        selected_nodes = self.graph.selected_nodes()
        if len(selected_nodes) >= 2:
            self._align_selected_nodes(selected_nodes, direction)
            return

        active_query_names: List[str] = []
        for node in self.graph.all_nodes():
            if isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode":
                qname = node.get_property("query_name") or node.name()
                if qname and qname not in active_query_names:
                    active_query_names.append(qname)

        active_queries = [self.report.get_query(name) for name in active_query_names if self.report.get_query(name)]
        csv_imports = self._collect_csv_import_data()

        new_positions = ProcessFlowGraphBuilder.auto_layout(
            self.graph,
            active_queries,
            direction=direction,
            import_csv_data=csv_imports,
        )
        self._node_positions.update(new_positions)
        self._fit_graph_to_canvas()
        dir_name = "Horizontal (Left-to-Right)" if direction == "horizontal" else "Vertical (Top-to-Bottom)"
        self.status_bar.showMessage(f"Auto-formatted all objects: {dir_name}", 3000)

    def _align_selected_nodes(self, nodes: List[BaseNode], direction: str) -> None:
        """Align only the selected objects (tables, query boxes, import nodes)."""
        if len(nodes) < 2:
            self.status_bar.showMessage("Please select 2 or more objects to align.", 3000)
            return

        if direction == "horizontal":
            sorted_nodes = sorted(nodes, key=lambda n: (n.pos()[0], n.pos()[1]))
            center_y = sum(n.pos()[1] for n in nodes) / len(nodes)
            cur_x = min(n.pos()[0] for n in nodes)

            for node in sorted_nodes:
                w = max(node.view.boundingRect().width() if hasattr(node, "view") and node.view else 180.0, 180.0)
                actual_x = max(node.pos()[0], cur_x)
                node.set_pos(actual_x, center_y)
                self._node_positions[node.name()] = (actual_x, center_y)
                qname = node.get_property("query_name") if hasattr(node, "get_property") else None
                if qname:
                    self._node_positions[qname] = (actual_x, center_y)
                cur_x = actual_x + w + 50.0

            dir_msg = "Horizontally"
        else:
            sorted_nodes = sorted(nodes, key=lambda n: (n.pos()[1], n.pos()[0]))
            center_x = sum(n.pos()[0] for n in nodes) / len(nodes)
            cur_y = min(n.pos()[1] for n in nodes)

            for node in sorted_nodes:
                h = max(node.view.boundingRect().height() if hasattr(node, "view") and node.view else 60.0, 60.0)
                actual_y = max(node.pos()[1], cur_y)
                node.set_pos(center_x, actual_y)
                self._node_positions[node.name()] = (center_x, actual_y)
                qname = node.get_property("query_name") if hasattr(node, "get_property") else None
                if qname:
                    self._node_positions[qname] = (center_x, actual_y)
                cur_y = actual_y + h + 30.0

            dir_msg = "Vertically"

        # Redraw pipes
        for item in self.graph.viewer().scene().items():
            if hasattr(item, "reset"):
                try:
                    item.reset()
                except Exception:
                    pass

        self.status_bar.showMessage(f"Aligned {len(nodes)} selected object(s) {dir_msg}.", 3000)

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

        # 1. Commit any pending query file renames to disk
        if self.app_controller and self._pending_query_renames:
            for orig_name, final_name in list(self._pending_query_renames.items()):
                if orig_name != final_name:
                    self.app_controller.rename_query(orig_name, final_name)
            self._pending_query_renames.clear()
            self.report = self.app_controller.active_report or self.report
            self._refresh_left_queries()

        # Capture current zoom and pan center
        viewer = self.graph.viewer()
        sc = viewer.scene_center()
        center_coords = [float(sc[0]), float(sc[1])] if isinstance(sc, (list, tuple)) else [float(sc.x()), float(sc.y())]
        view_state = {
            "zoom": viewer.get_zoom(),
            "center": center_coords,
        }
        csv_imports = self._collect_csv_import_data()

        # Capture splitter sizes
        cur_splitter_sizes = self.splitter.sizes()
        if self.app_controller and self.app_controller.repo:
            self.app_controller.repo.set_setting("flow_editor_splitter_sizes", ",".join(str(s) for s in cur_splitter_sizes))

        # Collect parameters and date options from overlay if present
        date_options = {}
        if hasattr(self, "param_overlay"):
            overlay_vals = self.param_overlay.get_parameter_values()
            existing_defaults.update(overlay_vals)
            date_options = self.param_overlay.get_date_option_selections()

        self.flow_controller.save_flow(
            active_query_names=active_query_names,
            parameter_defaults=existing_defaults,
            graph_session=session_data,
            show_full_table_names=self.show_full_table_names,
            csv_filenames=self.flow_controller.get_csv_filenames(),
            csv_imports=csv_imports,
            view_state=view_state,
            splitter_sizes=cur_splitter_sizes,
            parameter_date_options=date_options,
        )

        if self.app_controller:
            self.app_controller.scan()
            self.report = self.app_controller.active_report or self.report
            self._refresh_left_queries()

        self.status_bar.showMessage("Process flow saved successfully.", 4000)
        QMessageBox.information(self, "Saved", f"Process flow '{self.flow_controller.flow_name}' saved.")

    def closeEvent(self, event) -> None:
        """Remember splitter dimensions when closing process flow editor window."""
        try:
            if hasattr(self, "splitter"):
                cur_sizes = self.splitter.sizes()
                if self.app_controller and self.app_controller.repo:
                    self.app_controller.repo.set_setting("flow_editor_splitter_sizes", ",".join(str(s) for s in cur_sizes))
        except Exception as e:
            logger.debug(f"Error saving splitter sizes on close: {e}")
        super().closeEvent(event)

    def _on_run_flow(self) -> None:
        """Execute all CSV imports and queries in the process flow."""
        session_data = self.graph.serialize_session()
        queries_order = self.flow_controller.get_execution_order(session_data)

        # Check if there are any executable elements (queries or CSV imports)
        has_imports = any(
            isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
            for n in self.graph.all_nodes()
        )

        if not queries_order and not has_imports:
            QMessageBox.information(self, "Empty Flow", "There are no queries or CSV imports in this process flow to run.")
            return

        # Consolidate parameters from parameter overlay directly
        param_values = dict(self.flow_controller.parameter_defaults)
        if hasattr(self, "param_overlay"):
            overlay_vals = self.param_overlay.get_parameter_values()
            param_values.update(overlay_vals)
            self.flow_controller.parameter_defaults.update(param_values)

        outputs_dir = self.report.folder_path / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        csv_map = self.flow_controller.get_csv_filenames()

        results_log = []
        errors = []

        # 1. Run CSV Imports if present
        for node in self.graph.all_nodes():
            if isinstance(node, ImportCsvNode) or getattr(node, "type_", "") == "reporting.nodes.ImportCsvNode":
                imp_items = node.get_imports()
                for item in imp_items:
                    c_path = item.get("csv_path", "").strip()
                    d_table = item.get("output_table", "").strip()
                    headers = item.get("has_headers", True)
                    if not c_path or not d_table:
                        continue
                    self.status_bar.showMessage(f"Importing {c_path} -> {d_table}...")
                    QtWidgets.QApplication.processEvents()
                    try:
                        imp_res = run_bigquery_import_csv(
                            csv_path=c_path,
                            destination_table=d_table,
                            has_headers=headers,
                        )
                        row_cnt = imp_res.get("row_count")
                        cnt_str = f"{row_cnt:,} rows" if row_cnt is not None else "completed"
                        results_log.append(f"📥 Imported CSV '{c_path}' into '{d_table}' ({cnt_str})")
                    except Exception as e:
                        err_msg = f"Import CSV failed for {d_table}: {e}"
                        errors.append(err_msg)
                        results_log.append(f"❌ {err_msg}")
                        break
                if errors:
                    break

        if not errors:
            self.status_bar.showMessage(f"Running process flow queries ({len(queries_order)} queries)...")

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
                custom_csv = csv_map.get(qname)
                res = run_bigquery_script(
                    sql_script_path=qinfo.file_path,
                    report_name=self.report.name,
                    outputs_dir=outputs_dir,
                    parameters=param_values,
                    output_filename=custom_csv,
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
            self._sync_graph_topology()

    def _on_query_renamed(self, old_name: str, new_name: str) -> None:
        self._is_renaming_query = True
        try:
            # Track pending rename (chain if previously renamed: orig -> intermediate -> new)
            orig_name = old_name
            for k, v in list(self._pending_query_renames.items()):
                if v == old_name:
                    orig_name = k
                    break
            if orig_name == new_name:
                self._pending_query_renames.pop(orig_name, None)
            else:
                self._pending_query_renames[orig_name] = new_name

            # Immediately update the left panel to reflect the new name
            self._refresh_left_queries()

            # Update flow_controller references in memory
            if self.flow_controller:
                self.flow_controller.rename_query(old_name, new_name)

            # Update existing positions map
            if old_name in self._node_positions:
                self._node_positions[new_name] = self._node_positions.pop(old_name)
            for suffix in ("[Out]", "[CSV]", "[In]"):
                old_key = f"{old_name} {suffix}"
                new_key = f"{new_name} {suffix}"
                if old_key in self._node_positions:
                    self._node_positions[new_key] = self._node_positions.pop(old_key)

            # Update live nodes on canvas (names and custom properties)
            for n in self.graph.all_nodes():
                curr_qname = n.get_property("query_name")
                if n.name() == old_name or curr_qname == old_name or n.name() == new_name:
                    n.set_name(new_name)
                    n.set_property("query_name", new_name)
                    qinfo = self._get_working_query(new_name)
                    if qinfo and qinfo.file_path:
                        n.set_property("query_path", str(qinfo.file_path.resolve()))
                elif n.name().startswith(f"{old_name} ["):
                    suffix = n.name()[len(old_name):]
                    n.set_name(f"{new_name}{suffix}")
                    if n.get_property("query_owner") == old_name:
                        n.set_property("query_owner", new_name)

            # Re-sync graph topology to preserve all connections and table boxes
            self._sync_graph_topology()
        finally:
            self._is_renaming_query = False

    def _handle_query_drop(self, query_name: str, scene_x: float, scene_y: float) -> None:
        """Add dropped query node at scene position with deduplication."""
        import time
        now = time.time()
        last_info = getattr(self, "_last_drop_info", None)
        if last_info and last_info[0] == query_name and (now - getattr(self, "_last_drop_time", 0) < 0.8):
            return
        self._last_drop_info = (query_name, round(scene_x, -1), round(scene_y, -1))
        self._last_drop_time = now

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
            if event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Delete:
                    self._on_delete_selected()
                    return True
            elif event.type() in (QEvent.DragEnter, QEvent.DragMove):
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
                    local_pt = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    vp = viewport if viewport else viewer
                    if watched != vp and hasattr(vp, "mapFrom"):
                        vp_pos = vp.mapFrom(watched, local_pt)
                    else:
                        vp_pos = local_pt
                    scene_pos = viewer.mapToScene(vp_pos)
                    self._handle_query_drop(query_name, scene_pos.x(), scene_pos.y())
                    event.acceptProposedAction()
                    return True

        return super().eventFilter(watched, event)

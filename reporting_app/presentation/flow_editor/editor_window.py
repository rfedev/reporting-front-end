"""Process Flow Editor window with Left Panel, Main NodeGraphQt Canvas, and Right Panel."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QEvent, QFileSystemWatcher, QObject, QPoint, QPointF, QRectF, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
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
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTextEdit,
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
from reporting_app.core.bigquery_run import run_bigquery_script, run_bigquery_import_csv, run_bigquery_import_file
from reporting_app.core.models import ProcessFlowInfo, QueryInfo, QueryParameter, Report
from reporting_app.core.sql_parser import sync_query_csv_comments, update_query_csv_comment
from reporting_app.presentation.flow_editor.graph_builder import ProcessFlowGraphBuilder
from reporting_app.utils.date_calc import format_filename_with_date
from reporting_app.presentation.flow_editor.nodes import ImportCsvNode, ImportFilesNode, QueryNode, TableBoxNode
from reporting_app.presentation.flow_editor.schema_dialog import SchemaEditorDialog
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
    """Dialog window to configure CSV / Excel file import(s) into BigQuery table(s)."""

    def __init__(
        self,
        initial_imports: Optional[List[dict]] = None,
        workbench_dataset: str = "",
        report_folder: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Import Files Configuration")
        self.resize(750, 520)
        self.workbench_dataset = workbench_dataset.strip()
        self.report_folder = Path(report_folder) if report_folder else None
        self.inputs_dir = (self.report_folder / "inputs") if self.report_folder else None
        if self.inputs_dir:
            try:
                self.inputs_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        self.rows: List[dict] = []
        self._build_ui(initial_imports or [])

    def _get_input_files(self) -> List[str]:
        """Return list of CSV and Excel filenames located in the report's inputs folder."""
        if not self.inputs_dir or not self.inputs_dir.exists():
            return []
        try:
            exts = {".csv", ".xlsx"}
            return sorted([f.name for f in self.inputs_dir.iterdir() if f.is_file() and f.suffix.lower() in exts])
        except Exception:
            return []

    def _get_input_csv_files(self) -> List[str]:
        return self._get_input_files()

    def _inspect_sheets(self, file_path: str) -> List[str]:
        """Return list of sheet names from an Excel workbook."""
        p = Path(file_path)
        if not p.is_absolute() and self.inputs_dir:
            cand = self.inputs_dir / p
            if cand.exists():
                p = cand
        if not p.exists() or p.suffix.lower() not in (".xlsx", ".xls"):
            return []
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), read_only=True)
            names = wb.sheetnames
            wb.close()
            return names
        except Exception:
            return []

    def _compute_auto_table(self, file_path: str) -> str:
        """Derive projectid.dataset.csvtablename from the CSV or Excel file path and workbench dataset.

        Strips away variable expressions enclosed in '%...%' and any trailing '-' or '_' prior to them.
        Example: test-import-%YYYYMM%.csv -> test_import
        """
        if not file_path:
            return ""
        import re
        stem = Path(file_path).stem
        # Strip away any %...% tokens and any '-' or '_' immediately preceding them
        clean_stem = re.sub(r"[-_]?%[^%]+%", "", stem)
        # Strip any trailing '-' or '_' left over
        clean_stem = clean_stem.rstrip("-_")
        # Replace remaining non-alphanumeric (except underscores and hyphens in table names)
        clean_stem = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in clean_stem)
        if not clean_stem:
            clean_stem = "imported_table"

        if self.workbench_dataset:
            wb_prefix = self.workbench_dataset.rstrip(".") + "."
        else:
            wb_prefix = "projectid.dataset."
        return f"{wb_prefix}{clean_stem}"

    def _build_ui(self, initial_imports: List[dict]):
        main_layout = QVBoxLayout(self)

        header_label = QLabel(
            "<b>Configure File Import:</b><br>"
            "<i>Specify CSV or Excel (.xlsx) file, sheet, headers, schema mode, and destination BigQuery output table.</i>"
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
        add_btn = QPushButton("➕ Add File")
        add_btn.setToolTip("Add another file import section")
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
                    file_path=item.get("file_path") or item.get("csv_path", ""),
                    has_headers=item.get("has_headers", True),
                    output_table=item.get("output_table", ""),
                    sheet_name=item.get("sheet_name"),
                    schema_mode=item.get("schema_mode", "auto"),
                    manual_schema=item.get("manual_schema"),
                )
        else:
            self._add_row()

    def _add_row(
        self,
        file_path: str = "",
        has_headers: bool = True,
        output_table: str = "",
        sheet_name: Optional[str] = None,
        schema_mode: str = "auto",
        manual_schema: Optional[List[dict]] = None,
        **kwargs,
    ):
        if not file_path and "csv_path" in kwargs:
            file_path = kwargs["csv_path"] or ""
        if not isinstance(file_path, str):
            file_path = ""
        if not isinstance(output_table, str):
            output_table = ""
        if not isinstance(manual_schema, list):
            manual_schema = []
        if schema_mode not in ("auto", "manual"):
            schema_mode = "auto"

        sec_num = len(self.rows) + 1
        section_box = QGroupBox(f"File Import #{sec_num}", self.container)
        section_box.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #555; border-radius: 6px; margin-top: 10px; padding: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #ddd; }"
        )
        row_layout = QVBoxLayout(section_box)
        row_layout.setSpacing(8)

        # Row 1: File path (editable combo) + square Browse button + Headers checkbox + square Delete button
        r1 = QHBoxLayout()
        lbl_file = QLabel("File:")
        lbl_file.setFixedWidth(85)
        r1.addWidget(lbl_file)

        path_combo = QComboBox(self.container)
        path_combo.setEditable(True)
        path_combo.setInsertPolicy(QComboBox.NoInsert)
        path_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        path_combo.lineEdit().setPlaceholderText("Select or enter CSV/XLSX filename or path...")

        # Populate dropdown with available CSV/XLSX files in inputs folder
        input_files = self._get_input_files()
        for f in input_files:
            path_combo.addItem(f)

        if file_path:
            idx = path_combo.findText(file_path)
            if idx >= 0:
                path_combo.setCurrentIndex(idx)
            else:
                path_combo.setEditText(file_path)
        else:
            path_combo.setEditText("")

        r1.addWidget(path_combo, 1)

        browse_btn = QPushButton("📁")
        browse_btn.setToolTip("Browse for CSV or Excel file...")
        browse_btn.setFixedSize(30, 30)
        r1.addWidget(browse_btn)

        headers_cb = QCheckBox("Headers")
        headers_cb.setChecked(has_headers)
        r1.addWidget(headers_cb)

        del_btn = QPushButton("🗑")
        del_btn.setToolTip("Remove this file import section")
        del_btn.setFixedSize(30, 30)
        r1.addWidget(del_btn)

        # Row 2: Output table text box
        r2 = QHBoxLayout()
        lbl_table = QLabel("Output Table:")
        lbl_table.setFixedWidth(85)
        r2.addWidget(lbl_table)
        table_edit = QLineEdit(output_table)
        table_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        table_edit.setPlaceholderText("projectid.dataset.tablename")
        r2.addWidget(table_edit, 1)

        # Row 3: Sheet selector + Schema Mode + Configure Schema Button
        r3 = QHBoxLayout()
        lbl_sheet = QLabel("Sheet:")
        lbl_sheet.setFixedWidth(85)
        r3.addWidget(lbl_sheet)

        sheet_combo = QComboBox(self.container)
        sheet_combo.setMinimumWidth(150)
        sheet_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        r3.addWidget(sheet_combo, 1)

        r3.addSpacing(15)

        lbl_schema = QLabel("Schema:")
        lbl_schema.setFixedWidth(55)
        r3.addWidget(lbl_schema)

        schema_combo = QComboBox(self.container)
        schema_combo.addItems(["Auto Detect", "Manual"])
        schema_combo.setCurrentText("Manual" if schema_mode == "manual" else "Auto Detect")
        schema_combo.setFixedWidth(110)
        r3.addWidget(schema_combo)

        schema_btn = QPushButton("⚙ Configure Schema...")
        schema_btn.setStyleSheet(
            "QPushButton:disabled { color: #6e7681; background-color: rgba(45, 49, 56, 0.4); border: 1px solid #383c44; }"
        )
        schema_btn.setToolTip("Open manual schema field definition editor")
        r3.addWidget(schema_btn)

        row_layout.addLayout(r1)
        row_layout.addLayout(r2)
        row_layout.addLayout(r3)

        path_combo.text = path_combo.currentText
        path_combo.setText = path_combo.setEditText

        row_data = {
            "widget": section_box,
            "path_combo": path_combo,
            "path_edit": path_combo,
            "headers_cb": headers_cb,
            "table_edit": table_edit,
            "sheet_combo": sheet_combo,
            "schema_mode_combo": schema_combo,
            "schema_btn": schema_btn,
            "del_btn": del_btn,
            "manual_schema": list(manual_schema),
        }

        def update_schema_btn_label():
            cnt = len(row_data["manual_schema"])
            is_manual = "manual" in schema_combo.currentText().lower()
            schema_btn.setEnabled(is_manual)
            if is_manual:
                if cnt > 0:
                    schema_btn.setText(f"⚙ Schema ({cnt} fields)...")
                else:
                    schema_btn.setText("⚙ Configure Schema...")
                schema_btn.setToolTip("Open manual schema field definition editor")
            else:
                schema_btn.setText("Configure Schema...")
                schema_btn.setToolTip("Select 'Manual' schema mode to configure fields")

        def update_sheet_combo():
            raw_path = path_combo.currentText().strip()
            sheets = self._inspect_sheets(raw_path)
            sheet_combo.blockSignals(True)
            sheet_combo.clear()
            if sheets:
                sheet_combo.setEnabled(True)
                sheet_combo.addItems(sheets)
                target_sheet = sheet_name or ""
                if target_sheet and target_sheet in sheets:
                    sheet_combo.setCurrentText(target_sheet)
                else:
                    sheet_combo.setCurrentIndex(0)
            else:
                sheet_combo.setEnabled(False)
                if Path(raw_path).suffix.lower() in (".xlsx", ".xls"):
                    sheet_combo.addItem("(No sheets found)")
                else:
                    sheet_combo.addItem("(CSV - No sheets)")
            sheet_combo.blockSignals(False)

        def pick_file():
            start_dir = str(self.inputs_dir) if self.inputs_dir and self.inputs_dir.exists() else ""
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "Select Data File",
                start_dir,
                "Data Files (*.csv *.xlsx);;CSV Files (*.csv);;Excel Files (*.xlsx);;All Files (*)",
            )
            if selected:
                sel_path = Path(selected)
                if self.inputs_dir and sel_path.parent.resolve() == self.inputs_dir.resolve():
                    display_val = sel_path.name
                else:
                    display_val = selected
                path_combo.setEditText(display_val)
                auto_val = self._compute_auto_table(display_val)
                if auto_val:
                    table_edit.setText(auto_val)
                    table_edit._is_auto_populated = True
                update_sheet_combo()

        browse_btn.clicked.connect(pick_file)

        def on_path_changed(new_path: str):
            curr_table = table_edit.text().strip()
            if not curr_table or getattr(table_edit, "_is_auto_populated", False):
                auto_val = self._compute_auto_table(new_path)
                if auto_val:
                    table_edit.setText(auto_val)
                    table_edit._is_auto_populated = True
            update_sheet_combo()

        path_combo.currentTextChanged.connect(on_path_changed)

        def open_schema_editor():
            f_text = path_combo.currentText().strip()
            resolved_p = Path(f_text)
            if not resolved_p.is_absolute() and self.inputs_dir:
                cand = self.inputs_dir / resolved_p
                if cand.exists():
                    resolved_p = cand
            sh_text = sheet_combo.currentText().strip()
            if sh_text.startswith("(") or not sheet_combo.isEnabled():
                sh_text = None
            tbl_text = table_edit.text().strip()
            dlg = SchemaEditorDialog(
                parent=self,
                current_schema=row_data["manual_schema"],
                file_path=str(resolved_p) if resolved_p.exists() else None,
                sheet_name=sh_text,
                has_headers=headers_cb.isChecked(),
                table_name=tbl_text,
            )
            if dlg.exec() == QDialog.Accepted:
                row_data["manual_schema"] = dlg.get_schema()
                if row_data["manual_schema"]:
                    schema_combo.setCurrentText("Manual")
                update_schema_btn_label()

        def on_schema_mode_changed(mode: str):
            update_schema_btn_label()
            if mode == "Manual" and not row_data["manual_schema"]:
                open_schema_editor()

        schema_combo.currentTextChanged.connect(on_schema_mode_changed)
        schema_btn.clicked.connect(open_schema_editor)

        # Initial updates
        update_sheet_combo()
        update_schema_btn_label()

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
            r["widget"].setTitle(f"File Import #{idx + 1}")
            # Only allow deleting if more than 1 section
            r["del_btn"].setEnabled(len(self.rows) > 1)

    def get_imports(self) -> List[dict]:
        results = []
        for r in self.rows:
            f_path = r["path_combo"].currentText().strip()
            table = r["table_edit"].text().strip()
            headers = r["headers_cb"].isChecked()
            sh = r["sheet_combo"].currentText().strip() if r.get("sheet_combo") else ""
            if not r["sheet_combo"].isEnabled() or sh.startswith("(") or not sh:
                sh = None
            schema_mode = "manual" if r["schema_mode_combo"].currentText() == "Manual" else "auto"
            manual_schema = r.get("manual_schema") or []
            if f_path or table:
                results.append({
                    "file_path": f_path,
                    "csv_path": f_path,
                    "has_headers": headers,
                    "output_table": table,
                    "sheet_name": sh,
                    "schema_mode": schema_mode,
                    "manual_schema": manual_schema if schema_mode == "manual" else None,
                })
        return results


ImportFilesDialog = ImportCsvDialog


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

        try:
            if self.graph.viewer().scene():
                self.graph.viewer().scene().selectionChanged.connect(self._update_run_button_state)
        except Exception as e:
            logger.debug(f"Could not connect scene selectionChanged: {e}")

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
            elif isinstance(target_node, ImportCsvNode) or getattr(target_node, "type_", "") == "reporting.nodes.ImportCsvNode":
                target_node.set_name(new_name)
                self._sync_graph_topology()

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

        # Wire node (i) info button click to open log viewer pre-filtered to that node
        viewer = self.graph.viewer()
        if not hasattr(viewer, "node_info_clicked"):
            class _ViewerSignalEmitter(QtCore.QObject):
                node_info_clicked = QtCore.Signal(str)
            viewer._info_emitter = _ViewerSignalEmitter()
            viewer.node_info_clicked = viewer._info_emitter.node_info_clicked
        viewer.node_info_clicked.connect(lambda node_name: self._on_open_logs(filter_node=node_name))

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

        # Disable default context menu policy on viewer so our custom right click menu takes precedence
        viewer.setContextMenuPolicy(Qt.CustomContextMenu)
        if hasattr(viewer, "viewport") and viewer.viewport():
            viewer.viewport().setContextMenuPolicy(Qt.CustomContextMenu)

        def custom_mouse_press(event):
            # If user right-clicks while dragging a live connection noodle, cancel the action
            if getattr(viewer, "_LIVE_PIPE", None) and viewer._LIVE_PIPE.isVisible():
                if event.button() in (Qt.RightButton, Qt.LeftButton):
                    viewer.end_live_connection()
                    event.accept()
                    return

            if event.button() == Qt.RightButton:
                map_pos = viewer.mapToScene(event.pos())
                self._show_canvas_context_menu(event.pos(), map_pos)
                event.accept()
                return

            if event.button() == Qt.LeftButton:
                mods = event.modifiers()
                # If Ctrl held: multi-select marquee mode
                if mods & Qt.ControlModifier:
                    viewer.MMB_state = False
                    viewer.SHIFT_state = True
                    viewer.CTRL_state = False
                    return orig_mouse_press(event)

                # Check if click lands on or near a pipe item: select only the pipe
                map_pos = viewer.mapToScene(event.pos())
                items_exact = viewer.scene().items(map_pos) if viewer.scene() else []
                # Check if user clicked directly on a port (ports have priority over pipes)
                port_clicked = next((it for it in items_exact if isinstance(it, PortItem)), None)
                if not port_clicked:
                    # Check if click is on or close to a pipe
                    items_near_pipe = viewer._items_near(map_pos, PipeItem, 8, 8)
                    pipe_item = next((it for it in items_exact if isinstance(it, PipeItem)), None)
                    if not pipe_item and items_near_pipe:
                        pipe_item = items_near_pipe[0]
                    if pipe_item:
                        # Deselect other items and select only this pipe
                        self.graph.clear_selection()
                        if viewer.scene():
                            viewer.scene().clearSelection()
                        pipe_item.setSelected(True)
                        event.accept()
                        return

                # Check if click lands on empty canvas (no node / pipe)
                items_near = viewer._items_near(map_pos, None, 15, 15)
                if not items_near:
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
            if event.button() == Qt.RightButton:
                event.accept()
                return

            if event.button() == Qt.LeftButton and viewer.MMB_state:
                viewer.MMB_state = False
                viewer.viewport().unsetCursor()
                event.accept()
                return

            return orig_mouse_release(event)

        # Intercept sceneMousePressEvent so dragging never detaches or starts from a pipe
        orig_scene_mouse_press = viewer.sceneMousePressEvent

        def custom_scene_mouse_press(event):
            if event.button() == Qt.LeftButton:
                pos = event.scenePos()
                items = viewer._items_near(pos, None, 5, 5)
                has_port = any(isinstance(it, PortItem) for it in items)
                has_node = any(hasattr(it, "node") or hasattr(it, "type_") for it in items if not isinstance(it, PipeItem))
                pipe_item = next((it for it in items if isinstance(it, PipeItem)), None)
                # If clicking on a pipe and NOT directly on a port or node, do not allow pipe detachment
                if pipe_item and not has_port and not has_node:
                    self.graph.clear_selection()
                    if viewer.scene():
                        viewer.scene().clearSelection()
                    pipe_item.setSelected(True)
                    return
            return orig_scene_mouse_press(event)

        viewer.sceneMousePressEvent = custom_scene_mouse_press
        viewer.mousePressEvent = custom_mouse_press
        viewer.mouseMoveEvent = orig_mouse_move
        viewer.mouseReleaseEvent = custom_mouse_release

        # Color the live dashed noodle according to the connector being dragged
        orig_start_live_connection = viewer.start_live_connection

        def custom_start_live_connection(selected_port):
            # Live connections can ONLY be started from a valid PortItem, NEVER directly from a pipe!
            if not isinstance(selected_port, PortItem):
                return

            # Allow starting a manual connection from run_out, run_in, or out_tables (for output table dragging)
            port_name = getattr(selected_port, "name", "")
            if port_name not in ("run_out", "run_in", "out_tables"):
                return

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

        # Intercept live connection release on empty space or invalid ports
        orig_apply_live_connection = viewer.apply_live_connection

        def custom_apply_live_connection(event):
            start_port = getattr(viewer, "_start_port", None)
            pipe_visible = getattr(viewer, "_LIVE_PIPE", None) and viewer._LIVE_PIPE.isVisible()
            if pipe_visible and start_port is not None:
                start_name = getattr(start_port, "name", "")
                if start_name not in ("run_out", "run_in", "out_tables"):
                    viewer.end_live_connection()
                    return

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
                    viewer.end_live_connection()
                    if p_base_node is not None:
                        self._handle_empty_space_port_drop(p_base_node, p_name, scene_pos.x(), scene_pos.y())
                    return
                else:
                    # Connection dropped onto an existing port:
                    # Enforce that user can ONLY connect run_out <-> run_in (no connecting out_tables to random ports)
                    end_name = getattr(end_port, "name", "")
                    valid_connection = (
                        (start_name == "run_out" and end_name == "run_in")
                        or (start_name == "run_in" and end_name == "run_out")
                    )
                    if not valid_connection:
                        # Reject disallowed connection
                        viewer.end_live_connection()
                        self.status_bar.showMessage("Manual execution connections are only allowed between run_out and run_in.", 3000)
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

    def _find_executable_node_near(self, scene_pos: QtCore.QPointF) -> Optional[BaseNode]:
        """Find a QueryNode or ImportCsvNode directly under the given scene point."""
        viewer = self.graph.viewer()
        items = viewer.scene().items(scene_pos) if viewer.scene() else []
        for item in items:
            # Check if item corresponds to a node view
            for node in self.graph.all_nodes():
                if isinstance(node, (QueryNode, ImportCsvNode)) or getattr(node, "type_", "") in ("reporting.nodes.QueryNode", "reporting.nodes.ImportCsvNode"):
                    if getattr(node, "view", None) == item or (hasattr(item, "topLevelItem") and item.topLevelItem() == getattr(node, "view", None)):
                        return node
                    # Also check parents
                    cur = item
                    while cur:
                        if cur == getattr(node, "view", None):
                            return node
                        cur = cur.parentItem() if hasattr(cur, "parentItem") else None
        return None

    def _get_selected_pipes(self) -> List[PipeItem]:
        """Return list of currently selected PipeItems in the scene."""
        scene = self.graph.viewer().scene()
        if not scene:
            return []
        return [item for item in scene.selectedItems() if isinstance(item, PipeItem)]

    def _delete_selected_blue_pipes(self) -> None:
        """Delete selected blue execution connections (run_out -> run_in) to remove links."""
        pipes = self._get_selected_pipes()
        deleted_count = 0
        for pipe in pipes:
            out_p = getattr(pipe, "output_port", None)
            in_p = getattr(pipe, "input_port", None)
            if not out_p or not in_p:
                continue
            # Blue noodles connect run_out to run_in between executable nodes
            if out_p.name == "run_out" and in_p.name == "run_in":
                # Disconnect ports in NodeGraphQt model
                out_base_node = next((n for n in self.graph.all_nodes() if getattr(n, "view", None) == out_p.node), None)
                in_base_node = next((n for n in self.graph.all_nodes() if getattr(n, "view", None) == in_p.node), None)
                if out_base_node and in_base_node:
                    src_port = out_base_node.get_output("run_out")
                    tgt_port = in_base_node.get_input("run_in")
                    if src_port and tgt_port:
                        try:
                            src_port.disconnect_from(tgt_port)
                            deleted_count += 1
                        except Exception as e:
                            logger.debug(f"Could not disconnect {src_port} from {tgt_port}: {e}")
                # Remove pipe graphics item from scene
                try:
                    pipe.disconnect()
                except Exception:
                    pass
                try:
                    if pipe.scene():
                        pipe.scene().removeItem(pipe)
                except Exception:
                    pass

        if deleted_count > 0:
            self._on_save(show_popup=False)
            self.status_bar.showMessage(f"Deleted {deleted_count} connection(s).", 3000)
        else:
            self.status_bar.showMessage("Only blue execution connections can be deleted.", 3000)

    def _show_canvas_context_menu(self, viewport_pos: QPoint, scene_pos: QtCore.QPointF) -> None:
        """Display context menu on canvas right-click with tabulated shortcut keys."""
        viewer = self.graph.viewer()
        target_node = self._find_executable_node_near(scene_pos)
        selected_nodes = self._get_selected_query_nodes()
        selected_pipes = self._get_selected_pipes()

        # If only connecting noodles are selected, show only 'Delete connection(s)'
        if selected_pipes and not selected_nodes and not self.graph.selected_nodes():
            menu = QMenu(self)
            del_action = menu.addAction("Delete connection(s)")
            del_action.setShortcut(QKeySequence("Delete"))
            del_action.setShortcutVisibleInContextMenu(True)
            del_action.triggered.connect(self._delete_selected_blue_pipes)
            global_pt = viewer.viewport().mapToGlobal(viewport_pos) if hasattr(viewer, "viewport") and viewer.viewport() else viewer.mapToGlobal(viewport_pos)
            menu.exec(global_pt)
            return

        menu = QMenu(self)

        def add_menu_item(label: str, shortcut: str, callback) -> QAction:
            action = menu.addAction(label)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutVisibleInContextMenu(True)
            action.triggered.connect(callback)
            return action

        if target_node:
            # Right-clicking on a query/import CSV node selects it
            if target_node not in selected_nodes:
                self.graph.clear_selection()
                target_node.set_selected(True)
                selected_nodes = [target_node]
                self._update_run_button_state()

            # Query node context menu items
            add_menu_item("Run Selected Queries", "F5", self.run_selected_queries)
            run_from_act = menu.addAction("Run From Query")
            run_from_act.triggered.connect(lambda: self.run_from_query(target_node))
            add_menu_item("Edit Query", "e", lambda: self._on_node_double_clicked(target_node))

        else:
            # Empty space context menu
            has_selected = len(selected_nodes) > 0
            if not has_selected:
                add_menu_item("Run Process Flow", "F5", self._on_run_flow)
            else:
                add_menu_item("Run Selected", "F5", self.run_selected_queries)

            menu.addSeparator()
            add_menu_item("Create Query", "q", lambda: self._on_create_query_at_pos(scene_pos))
            add_menu_item("Import Files", "i", lambda: self._on_add_import_csv(pos=(scene_pos.x(), scene_pos.y())))

            menu.addSeparator()
            add_menu_item("Auto Layout H", "h", lambda: self._auto_layout("horizontal"))
            add_menu_item("Auto Layout V", "v", lambda: self._auto_layout("vertical"))
            add_menu_item("Fit Graph", "f", self._fit_graph_to_canvas)

            menu.addSeparator()
            add_menu_item("Deselect", "d", self._on_deselect_all)

        global_pt = viewer.viewport().mapToGlobal(viewport_pos) if hasattr(viewer, "viewport") and viewer.viewport() else viewer.mapToGlobal(viewport_pos)
        menu.exec(global_pt)

    def _on_create_query_at_pos(self, scene_pos: QtCore.QPointF) -> None:
        """Prompt to create a new query node at specified scene position."""
        name, ok = QInputDialog.getText(
            self,
            "Create Query",
            "Enter new query name (without .sql extension):",
        )
        if ok and name.strip():
            query_name = name.strip()
            if self.app_controller:
                self.app_controller.add_query(query_name)
                self.report = self.app_controller.active_report or self.report
                self._refresh_left_queries()
            self.add_query_to_canvas(query_name, pos=(scene_pos.x(), scene_pos.y()))

    def _on_deselect_all(self) -> None:
        """Deselect all nodes and items in the scene."""
        self.graph.clear_selection()
        if self.graph.viewer().scene():
            self.graph.viewer().scene().clearSelection()
        self._update_run_button_state()

    def _on_select_all(self) -> None:
        """Select all nodes on the canvas."""
        # Do not select all nodes if user is currently typing in an input field
        focus_w = QtWidgets.QApplication.focusWidget()
        if isinstance(focus_w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            if hasattr(focus_w, "selectAll"):
                focus_w.selectAll()
            return
        scene = self.graph.viewer().scene() if self.graph.viewer() else None
        if scene and scene.focusItem() and isinstance(scene.focusItem(), (QtWidgets.QGraphicsTextItem, QtWidgets.QGraphicsProxyWidget)):
            return

        for node in self.graph.all_nodes():
            node.set_selected(True)
        self._update_run_button_state()

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
        self.run_flow_btn.setToolTip("Execute process flow queries in dependency order (F5)")
        self.run_flow_btn.clicked.connect(self._on_run_flow_button_clicked)
        toolbar.addWidget(self.run_flow_btn)

        toolbar.addSeparator()

        import_files_btn = QPushButton("📥 Import Files")
        import_files_btn.setToolTip("Add an Import Files node to load CSV or Excel files into BigQuery tables")
        import_files_btn.clicked.connect(self._on_add_import_csv)
        toolbar.addWidget(import_files_btn)

        toolbar.addSeparator()

        self.logs_btn = QPushButton("📜 Logs")
        self.logs_btn.setToolTip("View Execution Logs for this Process Flow")
        self.logs_btn.clicked.connect(lambda: self._on_open_logs())
        toolbar.addWidget(self.logs_btn)

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

        # Top-left expandable parameter overlay on the canvas (parented to graph_widget so zoom/pan does not move it)
        self.param_overlay = CanvasParameterOverlay(self.graph_widget)
        self.param_overlay.move(14, 14)
        self.param_overlay.show()
        self.param_overlay.raise_()

        self.splitter.addWidget(self.graph_widget)

        # Right Panel (Currently unused / placeholder)
        self.right_panel = QWidget(self)
        self.right_panel.setMinimumWidth(0)
        right_layout = QVBoxLayout(self.right_panel)
        right_label = QLabel("<b>Properties & Inspector</b><br><br><i>(Right panel currently unused)</i>")
        right_label.setAlignment(Qt.AlignCenter)
        right_label.setStyleSheet("color: #888888; font-size: 12px;")
        right_layout.addWidget(right_label)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, True)

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
            self.splitter.setSizes([240, 960, 0])
        self.setCentralWidget(self.splitter)

        # Status bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Pan: Drag empty space | Multi-select: Ctrl+Drag | Double-click node to edit SQL.")

        # Shortcuts for Save, Delete, Copy, and Paste
        self.save_shortcut = QShortcut(QKeySequence.Save, self)
        self.save_shortcut.activated.connect(self._on_save)
        self.del_shortcut = QShortcut(QKeySequence.Delete, self)
        self.del_shortcut.activated.connect(self._on_delete_selected)
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self)
        self.copy_shortcut.activated.connect(self._on_copy_selected)
        self.paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self.paste_shortcut.activated.connect(self._on_paste)
        self.select_all_shortcut = QShortcut(QKeySequence.SelectAll, self)
        self.select_all_shortcut.activated.connect(self._on_select_all)
        self._clipboard_query_name: Optional[str] = None

        # File watcher and polling timer to automatically sync query edits on disk
        self._query_watcher = QFileSystemWatcher(self)
        self._query_watcher.fileChanged.connect(self._on_query_file_modified)
        self._query_watcher.directoryChanged.connect(self._on_query_file_modified)
        self._setup_query_file_watcher()
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(2000)
        self._sync_timer.timeout.connect(self._sync_query_files_and_parameters)
        self._sync_timer.start()

    def _setup_query_file_watcher(self) -> None:
        """Watch query files and folder to detect external modifications."""
        if not hasattr(self, "_query_watcher") or not self.report or not self.report.folder_path:
            return
        files_to_watch = []
        queries_dir = self.report.folder_path / "queries"
        if queries_dir.exists():
            files_to_watch.append(str(queries_dir.resolve()))
        active_qnames = {
            n.get_property("query_name") or n.name()
            for n in self.graph.all_nodes()
            if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
        }
        for qname in active_qnames:
            q = self._get_working_query(qname)
            if q and q.file_path and q.file_path.exists():
                files_to_watch.append(str(q.file_path.resolve()))
        if files_to_watch:
            existing = self._query_watcher.files() + self._query_watcher.directories()
            new_paths = [p for p in files_to_watch if p not in existing]
            if new_paths:
                self._query_watcher.addPaths(new_paths)

    def _on_query_file_modified(self, path: str) -> None:
        """Called when a query file or queries directory is modified on disk."""
        self._sync_query_files_and_parameters()
        if hasattr(self, "_query_watcher") and Path(path).exists():
            if path not in self._query_watcher.files() and path not in self._query_watcher.directories():
                self._query_watcher.addPath(path)

    def _toggle_left_panel(self) -> None:
        """Collapse or expand left queries panel."""
        is_visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not is_visible)
        self.toggle_left_btn.setText("▶" if is_visible else "◀")

    def _toggle_right_panel(self) -> None:
        """Collapse or expand right inspector panel."""
        is_visible = self.right_panel.isVisible()
        self.right_panel.setVisible(not is_visible)
        self.toggle_right_btn.setText("◀" if is_visible else "▶")

    # def _toggle_table_names_display(self) -> None:
    #     """Toggle full table address vs short table name across all TableBoxNodes."""
    #     self.show_full_table_names = self.toggle_table_names_btn.isChecked()
    #     self.toggle_table_names_btn.setText(
    #         "🏷 Full Table Address" if self.show_full_table_names else "🏷 Short Table Name"
    #     )
    #     for node in self.graph.all_nodes():
    #         if isinstance(node, TableBoxNode) or node.type_ == "reporting.nodes.TableBoxNode":
    #             node.set_display_mode(self.show_full_table_names)

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
        """Ensure active query files in the current flow have synchronized # output: comments."""
        if not self.report or not self.report.folder_path:
            return
        active_qnames = {
            n.get_property("query_name") or n.name()
            for n in self.graph.all_nodes()
            if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
        }
        for qname in active_qnames:
            q = self._get_working_query(qname)
            if q:
                q.ensure_parsed(force=True)
                if q.output_csv_tables:
                    self.flow_controller.set_csv_filename(q.name, ", ".join(q.output_csv_tables))
                else:
                    flow_csvs = self.flow_controller.get_csv_filenames()
                    flow_csvs.pop(q.name, None)

        # Update any active CSV nodes on canvas immediately
        for node in self.graph.all_nodes():
            if isinstance(node, TableBoxNode) or getattr(node, "type_", "") == "reporting.nodes.TableBoxNode":
                btype = node.get_property("box_type") or getattr(getattr(node, "view", None), "table_box_type", "")
                if btype == "Output CSV":
                    qname = node.get_property("query_owner") or node.name().replace(" [CSV]", "").strip()
                    qinfo = self._get_working_query(qname)
                    if qinfo and qinfo.output_csv_tables:
                        node.setup_as_csv_output(qinfo.output_csv_tables)
                        node.set_display_mode(self.show_full_table_names)

    def _sync_query_files_and_parameters(self) -> None:
        """Reload active query parameters and CSV comments from disk if modified, updating canvas nodes and overlay."""
        if not self.report:
            return

        active_qnames = {
            n.get_property("query_name") or n.name()
            for n in self.graph.all_nodes()
            if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
        }
        if not active_qnames:
            return

        has_table_changes = False
        has_param_changes = False
        has_csv_changes = False

        for qname in active_qnames:
            q = self._get_working_query(qname)
            if not q or not q.file_path or not q.file_path.exists():
                continue
            try:
                mtime = q.file_path.stat().st_mtime
                if mtime != getattr(q, "mtime", None):
                    old_tables = (list(q.input_tables), list(q.output_tables))
                    old_params = [p.name for p in q.parameters]
                    old_csvs = list(q.output_csv_tables)

                    q.ensure_parsed(force=True)

                    if (list(q.input_tables), list(q.output_tables)) != old_tables:
                        has_table_changes = True
                    if [p.name for p in q.parameters] != old_params:
                        has_param_changes = True
                    if q.output_csv_tables != old_csvs:
                        has_csv_changes = True
                        if q.output_csv_tables:
                            self.flow_controller.set_csv_filename(q.name, ", ".join(q.output_csv_tables))
                        else:
                            flow_csvs = self.flow_controller.get_csv_filenames()
                            flow_csvs.pop(q.name, None)

                    # Update node on canvas if present
                    for node in self.graph.all_nodes():
                        if isinstance(node, QueryNode) or getattr(node, "type_", "") == "reporting.nodes.QueryNode":
                            n_qname = node.get_property("query_name") or node.name()
                            if n_qname == q.name:
                                node.set_parameters(q.parameter_names)
            except Exception as e:
                logger.debug(f"Error syncing query file {q.file_path}: {e}")

        if has_csv_changes:
            for node in self.graph.all_nodes():
                if isinstance(node, TableBoxNode) or getattr(node, "type_", "") == "reporting.nodes.TableBoxNode":
                    btype = node.get_property("box_type") or getattr(getattr(node, "view", None), "table_box_type", "")
                    if btype == "Output CSV":
                        qname = node.get_property("query_owner") or node.name().replace(" [CSV]", "").strip()
                        qinfo = self._get_working_query(qname)
                        if qinfo and qinfo.output_csv_tables:
                            node.setup_as_csv_output(qinfo.output_csv_tables)
                            node.set_display_mode(self.show_full_table_names)

        self._setup_query_file_watcher()

        if has_table_changes or has_csv_changes:
            self._sync_graph_topology()
        elif has_param_changes:
            self._refresh_parameter_overlay()

    def _refresh_parameter_overlay(self) -> None:
        """Refresh flow parameter overlay with all unique parameters from active canvas queries."""
        if hasattr(self, "param_overlay"):
            active_qnames = [
                n.get_property("query_name") or n.name()
                for n in self.graph.all_nodes()
                if isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
            ]

            unique_params: List[str] = []
            seen: set = set()
            for qn in active_qnames:
                wq = self._get_working_query(qn)
                if wq:
                    if not wq.parameters and wq.file_path and wq.file_path.exists():
                        try:
                            from reporting_app.core.sql_parser import scan_query_parameters
                            content = wq.file_path.read_text(encoding="utf-8", errors="replace")
                            p_names = scan_query_parameters(content)
                            wq.parameters = [QueryParameter(name=p) for p in p_names]
                        except Exception:
                            pass
                    for p in wq.parameters:
                        if p.name not in seen:
                            seen.add(p.name)
                            unique_params.append(p.name)

            date_opt_defaults = self.flow_controller.flow_data.get("parameter_date_options", {})
            sel_date_param = self.flow_controller.get_selected_filename_date_param()
            has_rep_date = self.flow_controller.flow_data.get(
                "has_report_date",
                "Report Date" in self.flow_controller.parameter_defaults,
            )
            if hasattr(self.param_overlay, "_has_report_date") and self.param_overlay._has_report_date:
                has_rep_date = True

            self.param_overlay.set_parameters(
                unique_params,
                current_defaults=self.flow_controller.parameter_defaults,
                date_option_defaults=date_opt_defaults,
                selected_filename_date_param=sel_date_param,
                has_report_date=has_rep_date,
            )
            self.param_overlay.move(14, 14)
            self.param_overlay.raise_()

    def showEvent(self, event: QShowEvent) -> None:
        """Ensure parameter overlay is refreshed and positioned when editor is shown."""
        super().showEvent(event)
        if hasattr(self, "param_overlay"):
            self.param_overlay.move(14, 14)
            self.param_overlay.raise_()
        self._refresh_parameter_overlay()

    def changeEvent(self, event: QEvent) -> None:
        """Detect window focus/activation to sync any external query file edits immediately."""
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._sync_query_files_and_parameters()

    def _load_initial_graph(self) -> None:
        """Load saved session or automatically add flow queries if brand new."""
        flow_queries = set(self.flow_controller.get_query_names())
        session_data = self.flow_controller.get_graph_session()
        if session_data and "nodes" in session_data:
            for ndata in session_data["nodes"].values():
                if isinstance(ndata, dict) and ndata.get("type_") == "reporting.nodes.QueryNode":
                    qn = ndata.get("custom", {}).get("query_name") or ndata.get("name")
                    if qn:
                        flow_queries.add(qn)
        if self.report and flow_queries:
            self.report.ensure_queries_parsed(flow_queries)

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
                self._refresh_parameter_overlay()

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
        self._refresh_parameter_overlay()

        QtCore.QTimer.singleShot(100, apply_view_state)

    def _on_add_import_csv(self, pos: Optional[Tuple[float, float]] = None) -> Optional[ImportCsvNode]:
        """Add an Import Files node to the canvas."""
        existing_names = {
            n.name() for n in self.graph.all_nodes()
            if isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
        }
        node_name = "Import Files"
        idx = 2
        while node_name in existing_names:
            node_name = f"Import Files {idx}"
            idx += 1

        if not isinstance(pos, (list, tuple)) or len(pos) < 2:
            try:
                viewer = self.graph.viewer()
                # Compute scene position corresponding to top-middle of the visible canvas viewport
                vp = viewer.viewport()
                vp_w = vp.width() if vp else viewer.width()
                top_middle_viewport = QPoint(vp_w // 2, 70)
                scene_pt = viewer.mapToScene(top_middle_viewport)
                node_pos = [float(scene_pt.x()), float(scene_pt.y())]
            except Exception:
                sc = self.graph.viewer().scene_center()
                cx = float(sc[0]) if isinstance(sc, (list, tuple)) else float(sc.x())
                cy = float(sc[1]) if isinstance(sc, (list, tuple)) else float(sc.y())
                node_pos = [cx, cy - 250.0]
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

        dialog = ImportCsvDialog(
            initial_imports=current_imports,
            workbench_dataset=wb_dataset,
            report_folder=self.report.folder_path if self.report else None,
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            new_imports = dialog.get_imports()
            node.set_imports(new_imports)
            self._sync_graph_topology()
            self.status_bar.showMessage("Updated file import configuration.", 3000)
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

        if not qinfo.is_parsed:
            qinfo.ensure_parsed()
            if qinfo.output_csv_tables:
                self.flow_controller.set_csv_filename(qinfo.name, ", ".join(qinfo.output_csv_tables))

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

    def _on_copy_selected(self) -> None:
        """Copy all selected query and CSV import nodes."""
        selected_nodes = self.graph.selected_nodes()
        nodes_to_copy = [
            n for n in selected_nodes
            if isinstance(n, (QueryNode, ImportCsvNode))
            or getattr(n, "type_", "") in ("reporting.nodes.QueryNode", "reporting.nodes.ImportCsvNode")
        ]
        if not nodes_to_copy:
            return

        copied_items: List[Dict[str, Any]] = []
        for n in nodes_to_copy:
            is_query = isinstance(n, QueryNode) or getattr(n, "type_", "") == "reporting.nodes.QueryNode"
            if is_query:
                qname = n.get_property("query_name") or n.name()
                pos = n.pos()
                copied_items.append({
                    "type": "query",
                    "name": qname,
                    "pos": (pos[0], pos[1]),
                })
            else:
                pos = n.pos()
                copied_items.append({
                    "type": "import_csv",
                    "name": n.name(),
                    "imports": n.get_imports() if hasattr(n, "get_imports") else [],
                    "pos": (pos[0], pos[1]),
                })

        self._clipboard_nodes = copied_items
        count = len(copied_items)
        self.status_bar.showMessage(f"Copied {count} node(s) to clipboard.", 3000)

    def _on_paste(self) -> None:
        """Paste all copied query and CSV import nodes from clipboard."""
        if not getattr(self, "_clipboard_nodes", None) or not self.report:
            return

        import re
        viewer = self.graph.viewer()
        cursor_pos = viewer.mapFromGlobal(QCursor.pos())
        scene_pos = viewer.mapToScene(cursor_pos)
        target_center_x, target_center_y = scene_pos.x(), scene_pos.y()

        # Calculate bounding center of source copied nodes to offset them to target
        min_x = min(item["pos"][0] for item in self._clipboard_nodes)
        min_y = min(item["pos"][1] for item in self._clipboard_nodes)
        max_x = max(item["pos"][0] for item in self._clipboard_nodes)
        max_y = max(item["pos"][1] for item in self._clipboard_nodes)
        center_orig_x = (min_x + max_x) / 2.0
        center_orig_y = (min_y + max_y) / 2.0

        offset_x = target_center_x - center_orig_x
        offset_y = target_center_y - center_orig_y

        # If pasted at almost the exact same location as original, offset slightly down-right
        if abs(offset_x) < 20 and abs(offset_y) < 20:
            offset_x += 40.0
            offset_y += 40.0

        new_nodes: List[BaseNode] = []
        for item in self._clipboard_nodes:
            new_pos = (item["pos"][0] + offset_x, item["pos"][1] + offset_y)
            if item["type"] == "query":
                orig_qname = item["name"]
                src_qinfo = self._get_working_query(orig_qname)
                if not src_qinfo or not src_qinfo.file_path or not src_qinfo.file_path.exists():
                    continue

                m = re.match(r"^(.*?)(?:_copy(\d*))?$", orig_qname)
                root_name = m.group(1) if m else orig_qname
                existing_names = {q.name for q in self.report.queries}
                candidate = f"{root_name}_copy"
                idx = 2
                while candidate in existing_names:
                    candidate = f"{root_name}_copy{idx}"
                    idx += 1

                try:
                    sql_content = src_qinfo.file_path.read_text(encoding="utf-8", errors="replace")
                    # Strip existing "# output: table_XX.csv" comments so the copied query gets freshly generated unique CSV tables
                    clean_lines = [
                        line for line in sql_content.splitlines(keepends=True)
                        if not re.match(r"^\s*(?:#|--)\s*out?put\s*:\s*[^\s;]+", line, re.IGNORECASE)
                    ]
                    sql_content = "".join(clean_lines)
                except Exception:
                    sql_content = ""

                if self.app_controller:
                    self.app_controller.add_query(candidate, template_sql=sql_content)
                    self.report = self.app_controller.active_report or self.report
                    self._refresh_left_queries()

                qnode = self.add_query_to_canvas(candidate, pos=new_pos)
                if qnode:
                    new_nodes.append(qnode)

            elif item["type"] == "import_csv":
                existing_names = {
                    n.name() for n in self.graph.all_nodes()
                    if isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode"
                }
                base_name = "Import Files"
                candidate = base_name
                idx = 2
                while candidate in existing_names:
                    candidate = f"{base_name} {idx}"
                    idx += 1

                inode: ImportCsvNode = self.graph.create_node(
                    "reporting.nodes.ImportCsvNode",
                    name=candidate,
                    pos=[new_pos[0], new_pos[1]],
                )
                self._node_positions[candidate] = (new_pos[0], new_pos[1])
                inode.set_imports(item.get("imports", []))
                self._sync_graph_topology()

                live_inode = next(
                    (
                        n for n in self.graph.all_nodes()
                        if (isinstance(n, ImportCsvNode) or getattr(n, "type_", "") == "reporting.nodes.ImportCsvNode")
                        and n.name() == candidate
                    ),
                    inode,
                )
                if live_inode:
                    new_nodes.append(live_inode)

        if new_nodes:
            self.graph.clear_selection()
            for n in new_nodes:
                n.set_selected(True)
            self.status_bar.showMessage(f"Pasted {len(new_nodes)} node(s).", 3000)

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
        else:
            selected_pipes = self._get_selected_pipes()
            if selected_pipes:
                self._delete_selected_blue_pipes()
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

    def _on_save(self, show_popup: bool = True) -> None:
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
        sel_filename_date_param = None
        has_report_date = False
        if hasattr(self, "param_overlay"):
            overlay_vals = self.param_overlay.get_parameter_values()
            existing_defaults.update(overlay_vals)
            date_options = self.param_overlay.get_date_option_selections()
            sel_filename_date_param = self.param_overlay.get_selected_filename_date_param()
            has_report_date = self.param_overlay.has_report_date()
            if not has_report_date:
                existing_defaults.pop("Report Date", None)
                date_options.pop("Report Date", None)

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
            selected_filename_date_param=sel_filename_date_param,
            has_report_date=has_report_date,
        )

        if self.app_controller:
            self.app_controller.scan()
            self.report = self.app_controller.active_report or self.report
            self._refresh_left_queries()

        self.status_bar.showMessage("Process flow saved successfully.", 4000)
        if show_popup:
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

    def _get_selected_query_nodes(self) -> List[BaseNode]:
        """Return list of selected QueryNode and ImportCsvNode objects."""
        return [
            n for n in self.graph.selected_nodes()
            if isinstance(n, (QueryNode, ImportCsvNode))
            or getattr(n, "type_", "") in ("reporting.nodes.QueryNode", "reporting.nodes.ImportCsvNode")
        ]

    def _update_run_button_state(self) -> None:
        """Update Run Flow button label based on whether query/import nodes are selected."""
        if not hasattr(self, "run_flow_btn"):
            return
        selected_nodes = self._get_selected_query_nodes()
        if selected_nodes:
            self.run_flow_btn.setText("▶ Run Selected")
            self.run_flow_btn.setToolTip("Execute only the selected queries/imports in dependency order (F5)")
        else:
            self.run_flow_btn.setText("▶ Run Flow")
            self.run_flow_btn.setToolTip("Execute process flow queries in dependency order (F5)")

    def _on_run_flow_button_clicked(self) -> None:
        """Handler for toolbar Run button: runs selected if any are selected, else runs full flow."""
        selected_nodes = self._get_selected_query_nodes()
        if selected_nodes:
            self.run_selected_queries()
        else:
            self._on_run_flow()

    def run_selected_queries(self) -> None:
        """Run only the currently selected query and import CSV nodes in topological dependency order."""
        selected_nodes = self._get_selected_query_nodes()
        if not selected_nodes:
            QMessageBox.information(self, "No Selection", "Please select at least one query or CSV import node to run.")
            return

        session_data = self.graph.serialize_session()
        node_order = self.flow_controller.get_node_execution_order(session_data)

        # Filter topological order to only include selected nodes
        selected_ids = {n.id for n in selected_nodes}
        filtered_order = [item for item in node_order if item["id"] in selected_ids]
        if not filtered_order:
            # Fallback by name if id match fails
            selected_names = {n.name() for n in selected_nodes}
            filtered_order = [item for item in node_order if item["name"] in selected_names]

        self._execute_ordered_nodes(filtered_order, context_title="Selected Queries")

    def run_from_query(self, start_node: BaseNode) -> None:
        """Run the flow onwards from start_node (inclusive), skipping prior nodes in topological order."""
        session_data = self.graph.serialize_session()
        node_order = self.flow_controller.get_node_execution_order(session_data)

        # Find start index in topological order
        start_idx = -1
        for idx, item in enumerate(node_order):
            if item["id"] == start_node.id or item["name"] == start_node.name():
                start_idx = idx
                break

        if start_idx == -1:
            QMessageBox.warning(self, "Error", f"Could not determine order for '{start_node.name()}'.")
            return

        downstream_order = node_order[start_idx:]
        self._execute_ordered_nodes(downstream_order, context_title=f"Flow From '{start_node.name()}'")

    def _on_run_flow(self) -> None:
        """Execute all CSV imports and queries in the process flow."""
        session_data = self.graph.serialize_session()
        node_order = self.flow_controller.get_node_execution_order(session_data)
        self._execute_ordered_nodes(node_order, context_title=f"Process flow '{self.flow_controller.flow_name}'")

    def _execute_ordered_nodes(self, ordered_items: List[Dict[str, Any]], context_title: str = "Process Flow") -> None:
        """Execute the specified ordered list of nodes (both CSV imports and queries)."""
        if not ordered_items:
            QMessageBox.information(self, "Empty Flow", "There are no queries or CSV imports to run.")
            return

        # Consolidate parameters from parameter overlay directly
        param_values = dict(self.flow_controller.parameter_defaults)
        filename_date = ""
        if hasattr(self, "param_overlay"):
            overlay_vals = self.param_overlay.get_parameter_values()
            param_values.update(overlay_vals)
            self.flow_controller.parameter_defaults.update(param_values)
            filename_date = self.param_overlay.get_filename_date()
        if not filename_date:
            sel_param = self.flow_controller.get_selected_filename_date_param()
            if sel_param and sel_param in param_values:
                filename_date = param_values[sel_param]
            else:
                for k, v in param_values.items():
                    if any(token in k.lower() for token in ("date", "dt", "day", "month", "year")):
                        filename_date = v
                        break

        outputs_dir = self.report.folder_path / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        csv_map = self.flow_controller.get_csv_filenames()

        import uuid
        from datetime import datetime, timezone
        run_id = str(uuid.uuid4())
        flow_start_time = datetime.now(timezone.utc).isoformat()
        flow_name = self.flow_controller.flow_name or "Process Flow"
        repo = self.app_controller.repo if self.app_controller else None

        results_log = []
        errors = []

        total_steps = len(ordered_items)
        self.status_bar.showMessage(f"Running {context_title} ({total_steps} step(s))...")
        failed_node_name = None

        for idx, item in enumerate(ordered_items, start=1):
            ntype = item.get("type")
            nid = item.get("id")
            nname = item.get("name")
            step_start_time = datetime.now(timezone.utc).isoformat()
            t0 = datetime.now(timezone.utc)

            if ntype == "import_csv":
                # Find matching ImportCsvNode
                inode = next((n for n in self.graph.all_nodes() if n.id == nid or n.name() == nname), None)
                if not inode or not (isinstance(inode, ImportCsvNode) or getattr(inode, "type_", "") == "reporting.nodes.ImportCsvNode"):
                    continue

                imp_items = inode.get_imports()
                imp_records = []
                for imp in imp_items:
                    f_path = (imp.get("file_path") or imp.get("csv_path") or "").strip()
                    d_table = imp.get("output_table", "").strip()
                    headers = imp.get("has_headers", True)
                    sheet_name = imp.get("sheet_name")
                    schema_mode = imp.get("schema_mode", "auto")
                    manual_schema = imp.get("manual_schema")
                    if not f_path or not d_table:
                        continue
                    if filename_date:
                        f_path = format_filename_with_date(f_path, filename_date)

                    resolved_file = Path(f_path)
                    if not resolved_file.is_absolute() and self.report and self.report.folder_path:
                        candidate = self.report.folder_path / "inputs" / resolved_file
                        if candidate.exists() or not resolved_file.exists():
                            resolved_file = candidate

                    self.status_bar.showMessage(f"[{idx}/{total_steps}] Importing {resolved_file.name} -> {d_table}...")
                    QtWidgets.QApplication.processEvents()
                    try:
                        imp_res = run_bigquery_import_file(
                            file_path=resolved_file,
                            destination_table=d_table,
                            has_headers=headers,
                            sheet_name=sheet_name,
                            schema_mode=schema_mode,
                            manual_schema=manual_schema,
                        )
                        row_cnt = imp_res.get("row_count")
                        cnt_str = f"{row_cnt:,} rows" if row_cnt is not None else "completed"
                        results_log.append(f"[{idx}/{total_steps}] 📥 Imported '{f_path}' into '{d_table}' ({cnt_str})")
                        imp_records.append({
                            "file_path": str(f_path),
                            "destination_table": d_table,
                            "row_count": row_cnt,
                            "has_headers": headers,
                            "sheet_name": sheet_name,
                            "schema_mode": schema_mode,
                        })
                    except Exception as e:
                        failed_node_name = nname or "Import Files"
                        err_msg = f"Import failed for {d_table}: {e}"
                        errors.append(err_msg)
                        results_log.append(f"[{idx}/{total_steps}] ❌ {err_msg}")
                        if repo:
                            t1 = datetime.now(timezone.utc)
                            repo.record_execution_log({
                                "run_id": run_id,
                                "flow_start_time": flow_start_time,
                                "node_start_time": step_start_time,
                                "node_end_time": t1.isoformat(),
                                "duration_seconds": (t1 - t0).total_seconds(),
                                "report_name": self.report.name,
                                "flow_name": flow_name,
                                "node_type": "import_csv",
                                "node_name": nname or "Import Files",
                                "status": "FAILED",
                                "error_message": err_msg,
                                "import_details_json": imp_records,
                            })
                        break

                if errors:
                    break

                if repo and imp_records:
                    t1 = datetime.now(timezone.utc)
                    total_rows = sum(r.get("row_count") or 0 for r in imp_records)
                    repo.record_execution_log({
                        "run_id": run_id,
                        "flow_start_time": flow_start_time,
                        "node_start_time": step_start_time,
                        "node_end_time": t1.isoformat(),
                        "duration_seconds": (t1 - t0).total_seconds(),
                        "report_name": self.report.name,
                        "flow_name": flow_name,
                        "node_type": "import_csv",
                        "node_name": nname or "Import Files",
                        "status": "SUCCESS",
                        "output_rows": total_rows,
                        "import_details_json": imp_records,
                    })

            elif ntype == "query":
                qname = nname
                qinfo = self.report.get_query(qname)
                if not qinfo or not qinfo.file_path.exists():
                    failed_node_name = qname
                    err = f"Query '{qname}' SQL file not found."
                    errors.append(err)
                    results_log.append(f"[{idx}/{total_steps}] ❌ {qname}: {err}")
                    if repo:
                        t1 = datetime.now(timezone.utc)
                        repo.record_execution_log({
                            "run_id": run_id,
                            "flow_start_time": flow_start_time,
                            "node_start_time": step_start_time,
                            "node_end_time": t1.isoformat(),
                            "duration_seconds": (t1 - t0).total_seconds(),
                            "report_name": self.report.name,
                            "flow_name": flow_name,
                            "node_type": "query",
                            "node_name": qname,
                            "status": "FAILED",
                            "error_message": err,
                        })
                    break

                self.status_bar.showMessage(f"[{idx}/{total_steps}] Running {qname}...")
                QtWidgets.QApplication.processEvents()

                try:
                    custom_csv = csv_map.get(qname)
                    if custom_csv and filename_date:
                        custom_csv = format_filename_with_date(custom_csv, filename_date)
                    res = run_bigquery_script(
                        sql_script_path=qinfo.file_path,
                        report_name=self.report.name,
                        outputs_dir=outputs_dir,
                        parameters=param_values,
                        output_filename=custom_csv,
                    )
                    if res.get("is_export"):
                        details = res.get("export_details", [])
                        if len(details) == 1:
                            d = details[0]
                            results_log.append(
                                f"[{idx}/{total_steps}] ✅ {qname}: Exported '{d['filename']}' ({d['row_count']:,} rows)"
                            )
                        elif len(details) > 1:
                            lines = [f"[{idx}/{total_steps}] ✅ {qname}: Exported {len(details)} tables:"]
                            for d in details:
                                lines.append(f"* {d['filename']} ({d['row_count']:,} rows)")
                            results_log.append("\n".join(lines))
                        else:
                            results_log.append(f"[{idx}/{total_steps}] ✅ {qname}: Exported {res.get('row_count', 0):,} rows to {res.get('output_file')}")
                    else:
                        tbl_rows = res.get("row_count")
                        tbl_rows_str = f" ({tbl_rows:,} rows)" if tbl_rows is not None else ""
                        results_log.append(f"[{idx}/{total_steps}] ✅ {qname}: Executed table creation/update in BigQuery{tbl_rows_str}")

                    if repo:
                        t1 = datetime.now(timezone.utc)
                        repo.record_execution_log({
                            "run_id": run_id,
                            "flow_start_time": flow_start_time,
                            "node_start_time": step_start_time,
                            "node_end_time": res.get("job_ended") or t1.isoformat(),
                            "duration_seconds": res.get("duration_seconds") or (t1 - t0).total_seconds(),
                            "report_name": self.report.name,
                            "flow_name": flow_name,
                            "node_type": "query",
                            "node_name": qname,
                            "status": "SUCCESS",
                            "submitted_query": res.get("submitted_query"),
                            "output_rows": res.get("row_count"),
                            "total_bytes_processed": res.get("total_bytes_processed"),
                            "total_bytes_billed": res.get("total_bytes_billed"),
                            "slot_millis": res.get("slot_millis"),
                            "cache_hit": res.get("cache_hit"),
                            "export_details_json": res.get("export_details", []),
                        })
                except Exception as e:
                    failed_node_name = qname
                    err_msg = str(e)
                    errors.append(f"{qname}: {err_msg}")
                    results_log.append(f"[{idx}/{total_steps}] ❌ {qname}: Failed ({err_msg})")
                    if repo:
                        t1 = datetime.now(timezone.utc)
                        repo.record_execution_log({
                            "run_id": run_id,
                            "flow_start_time": flow_start_time,
                            "node_start_time": step_start_time,
                            "node_end_time": t1.isoformat(),
                            "duration_seconds": (t1 - t0).total_seconds(),
                            "report_name": self.report.name,
                            "flow_name": flow_name,
                            "node_type": "query",
                            "node_name": qname,
                            "status": "FAILED",
                            "error_message": err_msg,
                        })
                    break

        summary_text = "\n".join(results_log)
        self.status_bar.showMessage(f"{context_title} execution finished.", 5000)

        if errors:
            failed_node_header = f"Error in node: '{failed_node_name}'\n\n" if failed_node_name else ""
            QMessageBox.critical(
                self,
                f"{context_title} Execution Error",
                f"{failed_node_header}Execution failed with errors:\n\n{summary_text}\n\nNote: If authentication failed, please run 'gcloud auth application-default login' in terminal.",
            )
        else:
            QMessageBox.information(
                self,
                f"{context_title} Completed",
                f"{context_title} completed successfully!\n\n{summary_text}",
            )

    def _on_open_logs(self, filter_node: Optional[str] = None) -> None:
        """Open the rich execution log viewer window."""
        if not self.app_controller or not self.app_controller.repo:
            QMessageBox.warning(self, "Logs Unavailable", "Database repository is not available.")
            return

        from reporting_app.presentation.log_viewer_dialog import LogViewerDialog
        dlg = LogViewerDialog(
            repo=self.app_controller.repo,
            report_name=self.report.name,
            flow_name=self.flow_controller.flow_name,
            filter_node=filter_node,
            parent=self,
        )
        dlg.exec()

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

            # 1. Immediately rename the query file on disk if app_controller is available
            if self.app_controller:
                renamed_ok = self.app_controller.rename_query(old_name, new_name)
                if renamed_ok:
                    self.report = self.app_controller.active_report or self.report
                    if orig_name in self._pending_query_renames:
                        self._pending_query_renames.pop(orig_name, None)
            else:
                # If app_controller is not wired, attempt direct file rename
                try:
                    qinfo = self._get_working_query(old_name)
                    if qinfo and qinfo.file_path and qinfo.file_path.exists():
                        clean_new = new_name.strip()
                        if clean_new.endswith(".sql"):
                            clean_new = clean_new[:-4]
                        target_file = qinfo.file_path.parent / f"{clean_new}.sql"
                        if not target_file.exists():
                            qinfo.file_path.rename(target_file)
                            qinfo.name = clean_new
                            qinfo.file_path = target_file
                except Exception as e:
                    logger.error(f"Direct file rename failed: {e}")

            # 2. Immediately update the left panel to reflect the new name
            self._refresh_left_queries()

            # 3. Update flow_controller references in memory
            if self.flow_controller:
                self.flow_controller.rename_query(old_name, new_name)

            # 4. Update existing positions map
            if old_name in self._node_positions:
                self._node_positions[new_name] = self._node_positions.pop(old_name)
            for suffix in ("[Out]", "[CSV]", "[In]"):
                old_key = f"{old_name} {suffix}"
                new_key = f"{new_name} {suffix}"
                if old_key in self._node_positions:
                    self._node_positions[new_key] = self._node_positions.pop(old_key)

            # 5. Update live nodes on canvas (names and custom properties)
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

            # 6. Re-sync graph topology to preserve all connections and table boxes
            self._sync_graph_topology()

            # 7. Persist updated flow JSON immediately so there's no mismatch
            if self.flow_controller:
                try:
                    self._on_save(show_popup=False)
                except Exception as e:
                    logger.warning(f"Could not auto-save flow after query rename: {e}")
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
            if event.type() == QEvent.Resize:
                if hasattr(self, "param_overlay"):
                    self.param_overlay.move(14, 14)
                    self.param_overlay.raise_()
            elif event.type() == QEvent.KeyPress:
                key = event.key()
                # Do not intercept typing if focus is on a text editor / line edit or in-scene text editing
                focus_w = QtWidgets.QApplication.focusWidget()
                if isinstance(focus_w, (QLineEdit, QTextEdit, QPlainTextEdit)):
                    return super().eventFilter(watched, event)

                scene = viewer.scene() if hasattr(viewer, "scene") else None
                if scene:
                    focus_item = scene.focusItem()
                    if isinstance(focus_item, (QtWidgets.QGraphicsTextItem, QtWidgets.QGraphicsProxyWidget)):
                        return super().eventFilter(watched, event)
                    # Also check if any node's text item is currently in edit mode
                    for node_item in scene.items():
                        text_item = getattr(node_item, "_text_item", None)
                        if text_item and (text_item.textInteractionFlags() & Qt.TextEditable):
                            return super().eventFilter(watched, event)


                if key == Qt.Key_Escape:
                    if getattr(viewer, "_LIVE_PIPE", None) and viewer._LIVE_PIPE.isVisible():
                        viewer.end_live_connection()
                        return True
                elif key == Qt.Key_Delete:
                    self._on_delete_selected()
                    return True
                elif key == Qt.Key_F5:
                    self._on_run_flow_button_clicked()
                    return True
                elif key == Qt.Key_Q:
                    cursor_pos = viewer.mapFromGlobal(QCursor.pos())
                    scene_pos = viewer.mapToScene(cursor_pos)
                    self._on_create_query_at_pos(scene_pos)
                    return True
                elif key == Qt.Key_I:
                    cursor_pos = viewer.mapFromGlobal(QCursor.pos())
                    scene_pos = viewer.mapToScene(cursor_pos)
                    self._on_add_import_csv(pos=(scene_pos.x(), scene_pos.y()))
                    return True
                elif key == Qt.Key_H:
                    self._auto_layout("horizontal")
                    return True
                elif key == Qt.Key_V:
                    self._auto_layout("vertical")
                    return True
                elif key == Qt.Key_F:
                    self._fit_graph_to_canvas()
                    return True
                elif key == Qt.Key_D:
                    self._on_deselect_all()
                    return True
                elif key == Qt.Key_E:
                    selected = self._get_selected_query_nodes()
                    if selected:
                        self._on_node_double_clicked(selected[0])
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

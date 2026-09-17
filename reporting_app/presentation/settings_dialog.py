"""Settings dialog for the application."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from PySide6 import QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    """Dialog allowing configuration of multiple working directories, workbench dataset, and auto-scan preferences."""

    DEFAULT_WORKBENCH_DATASET = "iw-gid-prd-01-c683.gid_art_yourworkbenchID"

    def __init__(
        self,
        working_directories: List[Dict[str, str]],
        auto_scan: bool,
        workbench_dataset: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(640)

        # Deep copy the list so changes can be cancelled
        self._working_dirs: List[Dict[str, str]] = [dict(d) for d in working_directories]
        self._auto_scan = auto_scan
        self._workbench_dataset = workbench_dataset

        self._build_ui()
        self._populate_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # Working Directories Group
        self.dirs_group = QGroupBox("Working Directories")
        dirs_layout = QVBoxLayout(self.dirs_group)
        dirs_layout.setContentsMargins(8, 12, 8, 8)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Alias", "Directory Path"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setFixedHeight(26)
        dirs_layout.addWidget(self.table)
        layout.addWidget(self.dirs_group)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Directory...")
        add_btn.clicked.connect(self._on_add_directory)
        btn_row.addWidget(add_btn)

        browse_btn = QPushButton("Change Path...")
        browse_btn.clicked.connect(self._on_change_path)
        btn_row.addWidget(browse_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_remove_directory)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # General Settings
        form_layout = QFormLayout()

        initial_workbench = self._workbench_dataset or self.DEFAULT_WORKBENCH_DATASET
        self.workbench_edit = QLineEdit(initial_workbench)
        self.workbench_edit.setPlaceholderText(self.DEFAULT_WORKBENCH_DATASET)
        form_layout.addRow("Workbench Dataset:", self.workbench_edit)

        self.auto_scan_checkbox = QCheckBox("Enable auto-scanning for reports, flows, and queries")
        self.auto_scan_checkbox.setChecked(self._auto_scan)
        form_layout.addRow("Auto-Scan:", self.auto_scan_checkbox)

        layout.addLayout(form_layout)

        # Dialog buttons (OK / Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for d in self._working_dirs:
            row = self.table.rowCount()
            self.table.insertRow(row)

            alias_item = QTableWidgetItem(d.get("alias", ""))
            path_item = QTableWidgetItem(d.get("path", ""))

            self.table.setItem(row, 0, alias_item)
            self.table.setItem(row, 1, path_item)
        self._update_table_height()

    def _update_table_height(self) -> None:
        """Adjust table height and section height to fit entries vertically and refresh immediately."""
        header_h = 26
        row_count = self.table.rowCount()
        rows_h = row_count * 26
        total_h = header_h + rows_h + (self.table.frameWidth() * 2) + 2
        self.table.setFixedHeight(total_h)
        self.table.updateGeometry()

        self.dirs_group.updateGeometry()
        self.dirs_group.setFixedHeight(self.dirs_group.sizeHint().height())

        if self.layout():
            self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())


    def _on_add_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Working Directory",
            str(Path.cwd()),
        )
        if not chosen:
            return

        # Prompt for Alias with a default based on folder name
        default_alias = Path(chosen).name or "Working Dir"
        alias, ok = QInputDialog.getText(
            self,
            "Directory Alias",
            "Enter an alias for this working directory:",
            text=default_alias,
        )
        if not ok or not alias.strip():
            return

        alias = alias.strip()
        self._working_dirs.append({"alias": alias, "path": chosen})
        self._populate_table()
        self.table.selectRow(self.table.rowCount() - 1)

    def _on_change_path(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Selection Required", "Please select a directory row to change.")
            return

        row = selected_rows[0].row()
        current_path = self.table.item(row, 1).text() if self.table.item(row, 1) else str(Path.cwd())

        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Working Directory",
            current_path,
        )
        if chosen:
            self.table.item(row, 1).setText(chosen)
            if row < len(self._working_dirs):
                self._working_dirs[row]["path"] = chosen

    def _on_remove_directory(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        row = selected_rows[0].row()
        if self.table.rowCount() <= 1:
            QMessageBox.warning(self, "Cannot Remove", "You must keep at least one working directory.")
            return

        alias = self.table.item(row, 0).text() if self.table.item(row, 0) else "selected"
        confirm = QMessageBox.question(
            self,
            "Confirm Remove",
            f"Are you sure you want to remove working directory '{alias}' from settings?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.table.removeRow(row)
            if row < len(self._working_dirs):
                self._working_dirs.pop(row)
            self._update_table_height()

    def _on_accept(self) -> None:
        # Validate table entries
        dirs: List[Dict[str, str]] = []
        seen_aliases = set()

        for row in range(self.table.rowCount()):
            alias_item = self.table.item(row, 0)
            path_item = self.table.item(row, 1)

            alias = alias_item.text().strip() if alias_item else ""
            path_str = path_item.text().strip() if path_item else ""

            if not alias:
                QMessageBox.warning(self, "Invalid Alias", f"Row {row + 1} is missing an alias.")
                return

            if alias in seen_aliases:
                QMessageBox.warning(self, "Duplicate Alias", f"Alias '{alias}' is used more than once. Each alias must be unique.")
                return
            seen_aliases.add(alias)

            if not path_str:
                QMessageBox.warning(self, "Invalid Path", f"Row {row + 1} is missing a directory path.")
                return

            dirs.append({"alias": alias, "path": path_str})

        if not dirs:
            QMessageBox.warning(self, "No Directories", "Please add at least one working directory.")
            return

        self._working_dirs = dirs
        self.accept()

    def get_working_directories(self) -> List[Dict[str, str]]:
        return self._working_dirs

    def get_workbench_dataset(self) -> str:
        return self.workbench_edit.text().strip()

    def get_auto_scan(self) -> bool:
        return self.auto_scan_checkbox.isChecked()


# ---------------------------------------------------------------------------
# Rich Execution Log Viewer Window
# ---------------------------------------------------------------------------

from PySide6.QtCore import QDate
from reporting_app.presentation.flow_editor.schema_dialog import (
    format_single_node_log,
    format_run_session_logs,
)


class LogViewerDialog(QDialog):
    """Rich execution log viewer dialog with date filtering, run list, node filter, and markdown output."""

    def __init__(
        self,
        repo: Any,
        report_name: str,
        flow_name: Optional[str] = None,
        filter_node: Optional[str] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.report_name = report_name
        self.flow_name = flow_name
        self.filter_node = filter_node

        title_suffix = f" - {report_name}" + (f" ({flow_name})" if flow_name else "")
        self.setWindowTitle(f"Execution Logs{title_suffix}")
        self.resize(1050, 680)

        self._current_logs: List[Any] = []
        self._build_ui()
        self._load_available_nodes()
        self._populate_runs()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Splitter: Left Filter & Run List | Right Markdown Log Viewer
        splitter = QSplitter(Qt.Horizontal, self)

        # ---------------- Left Pane ----------------
        left_widget = QtWidgets.QWidget(self)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Top controls: Show All Checkbox + Date Picker
        top_filter_layout = QHBoxLayout()
        self.show_all_cb = QCheckBox("Show All", self)
        self.show_all_cb.setToolTip("Show all log entries across all dates")
        self.show_all_cb.toggled.connect(self._on_show_all_toggled)

        self.date_picker = QDateEdit(self)
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("yyyy-MM-dd")
        self.date_picker.setDate(QDate.currentDate())
        self.date_picker.dateChanged.connect(self._on_date_changed)

        top_filter_layout.addWidget(self.show_all_cb)
        top_filter_layout.addWidget(self.date_picker, 1)
        left_layout.addLayout(top_filter_layout)

        # Node Filter dropdown
        node_filter_layout = QHBoxLayout()
        node_label = QLabel("Node:")
        node_label.setFixedWidth(40)
        self.node_combo = QComboBox(self)
        self.node_combo.addItem("All Nodes")
        self.node_combo.currentTextChanged.connect(self._on_node_filter_changed)
        node_filter_layout.addWidget(node_label)
        node_filter_layout.addWidget(self.node_combo, 1)
        left_layout.addLayout(node_filter_layout)

        # Run List / Tree: DateRun -> TimeRun
        self.run_tree = QTreeWidget(self)
        self.run_tree.setHeaderLabels(["Execution Runs"])
        self.run_tree.itemClicked.connect(self._on_tree_item_clicked)
        left_layout.addWidget(self.run_tree, 1)

        left_widget.setMinimumWidth(280)
        left_widget.setMaximumWidth(360)
        splitter.addWidget(left_widget)

        # ---------------- Right Pane ----------------
        right_widget = QtWidgets.QWidget(self)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Top Action Bar
        action_bar = QHBoxLayout()
        self.export_btn = QPushButton("💾 Export to Markdown (.md)")
        self.export_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white; padding: 5px 12px;")
        self.export_btn.clicked.connect(self._on_export_markdown)
        action_bar.addStretch()
        action_bar.addWidget(self.export_btn)
        right_layout.addLayout(action_bar)

        # Markdown Log Browser
        self.text_browser = QTextBrowser(self)
        self.text_browser.setOpenExternalLinks(True)
        self.text_browser.setStyleSheet(
            "QTextBrowser { font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5; "
            "background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-radius: 4px; padding: 12px; }"
        )
        right_layout.addWidget(self.text_browser, 1)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter, 1)

        # Bottom Close Button Bar
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(85)
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)
        main_layout.addLayout(btn_bar)

    def _load_available_nodes(self) -> None:
        """Populate node filter combo box with unique node names."""
        runs = self.repo.get_runs(report_name=self.report_name, flow_name=self.flow_name)
        nodes = sorted(list({r.node_name for r in runs if r.node_name}))
        for n in nodes:
            self.node_combo.addItem(n)

        if self.filter_node and self.filter_node in nodes:
            self.node_combo.setCurrentText(self.filter_node)

    def _on_show_all_toggled(self, checked: bool) -> None:
        self.date_picker.setEnabled(not checked)
        self._populate_runs()

    def _on_date_changed(self, qdate: QDate) -> None:
        if self.show_all_cb.isChecked():
            self.show_all_cb.setChecked(False)
        else:
            self._populate_runs()

    def _on_node_filter_changed(self, text: str) -> None:
        self._populate_runs()

    def _populate_runs(self) -> None:
        """Populate run_tree with Date headers and child Time entries."""
        self.run_tree.clear()
        selected_node = self.node_combo.currentText()
        node_filter = selected_node if selected_node != "All Nodes" else None

        date_str = None
        if not self.show_all_cb.isChecked():
            date_str = self.date_picker.date().toString("yyyy-MM-dd")

        sessions = self.repo.get_run_sessions(
            date_str=date_str,
            report_name=self.report_name,
            flow_name=self.flow_name,
            node_name=node_filter,
        )

        if not sessions:
            empty_item = QTreeWidgetItem(["(No logs found)"])
            self.run_tree.addTopLevelItem(empty_item)
            self.text_browser.setMarkdown("# Execution Log\n\n*No execution logs match the selected filters.*")
            self._current_logs = []
            return

        # Group sessions by Date (YYYY-MM-DD)
        from collections import OrderedDict
        by_date: Dict[str, List[Dict[str, Any]]] = OrderedDict()
        for s in sessions:
            dt_key = s["flow_start_time"][:10] if len(s["flow_start_time"]) >= 10 else "Unknown Date"
            if dt_key not in by_date:
                by_date[dt_key] = []
            by_date[dt_key].append(s)

        first_top_item = None
        for dt_key, s_list in by_date.items():
            date_item = QTreeWidgetItem([f"📅 {dt_key} ({len(s_list)} run{'s' if len(s_list) != 1 else ''})"])
            font = date_item.font(0)
            font.setBold(True)
            date_item.setFont(0, font)
            date_item.setData(0, Qt.UserRole, {"type": "date", "date": dt_key, "sessions": s_list})
            self.run_tree.addTopLevelItem(date_item)
            if not first_top_item:
                first_top_item = date_item

            for s in s_list:
                status_icon = "✅" if s["status"] == "SUCCESS" else "❌"
                raw_ts = s["flow_start_time"]
                time_str = raw_ts[11:19] if len(raw_ts) >= 19 else raw_ts
                flow_label = f" — {s['flow_name']}" if s.get("flow_name") and s["flow_name"] != "Standalone" else ""
                child_item = QTreeWidgetItem([f"{status_icon} {time_str}{flow_label} ({len(s['nodes'])} step{'s' if len(s['nodes']) != 1 else ''})"])
                child_item.setData(0, Qt.UserRole, {"type": "session", "session": s})
                date_item.addChild(child_item)

            date_item.setExpanded(True)

        # Select first date or run by default
        if first_top_item:
            self.run_tree.setCurrentItem(first_top_item)
            self._on_tree_item_clicked(first_top_item, 0)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")
        selected_node = self.node_combo.currentText()
        node_filter = selected_node if selected_node != "All Nodes" else None

        if item_type == "date":
            # Combined log of all runs on this date
            sessions = data.get("sessions", [])
            all_logs: List[Any] = []
            for s in sessions:
                for n in s.get("nodes", []):
                    if not node_filter or n.node_name == node_filter:
                        all_logs.append(n)

            self._current_logs = all_logs
            dt = data.get("date", "")
            title = f"Execution Logs for {dt} ({len(sessions)} run{'s' if len(sessions) != 1 else ''})"
            md = format_run_session_logs(all_logs, title=title)
            self.text_browser.setMarkdown(md)

        elif item_type == "session":
            s = data.get("session", {})
            nodes = s.get("nodes", [])
            if node_filter:
                nodes = [n for n in nodes if n.node_name == node_filter]

            self._current_logs = nodes
            raw_ts = s.get("flow_start_time", "")
            f_name = s.get("flow_name") or "Standalone Run"
            title = f"Run: {f_name} at {raw_ts.replace('T', ' ').split('.')[0]}"
            md = format_run_session_logs(nodes, title=title)
            self.text_browser.setMarkdown(md)

    def _on_export_markdown(self) -> None:
        """Export current log view to a markdown file on disk."""
        content = self.text_browser.toMarkdown()
        if not content.strip():
            QMessageBox.information(self, "Empty Log", "There is no log content to export.")
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Log as Markdown",
            f"execution_log_{self.report_name}.md",
            "Markdown Files (*.md);;Text Files (*.txt);;All Files (*)",
        )
        if out_path:
            try:
                Path(out_path).write_text(content, encoding="utf-8")
                QMessageBox.information(self, "Export Successful", f"Log successfully exported to:\n{out_path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"Could not write log file:\n{e}")

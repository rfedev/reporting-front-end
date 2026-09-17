"""Rich Execution Log Viewer dialog for BigQuery and Process Flow runs."""

from pathlib import Path
from typing import Any, List, Optional

from PySide6 import QtWidgets
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from reporting_app.presentation.log_formatter import (
    format_day_summary_logs,
    format_run_session_logs,
    format_single_node_log,
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

        # Bottom Bar: Close Button
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
            dates = self.repo.get_distinct_log_dates(
                report_name=self.report_name,
                flow_name=self.flow_name,
                node_name=self.filter_node,
            )
            if dates:
                latest_date = QDate.fromString(dates[0], "yyyy-MM-dd")
                if latest_date.isValid():
                    self.date_picker.blockSignals(True)
                    self.date_picker.setDate(latest_date)
                    self.date_picker.blockSignals(False)

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
        if text != "All Nodes" and not self.show_all_cb.isChecked():
            curr_date_str = self.date_picker.date().toString("yyyy-MM-dd")
            dates = self.repo.get_distinct_log_dates(
                report_name=self.report_name,
                flow_name=self.flow_name,
                node_name=text,
            )
            if dates and curr_date_str not in dates:
                latest_date = QDate.fromString(dates[0], "yyyy-MM-dd")
                if latest_date.isValid():
                    self.date_picker.blockSignals(True)
                    self.date_picker.setDate(latest_date)
                    self.date_picker.blockSignals(False)
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
            return

        # Group sessions by date
        grouped_by_date = {}
        for s in sessions:
            d = s.get("date") or (s.get("flow_start_time", "")[:10] if len(s.get("flow_start_time", "")) >= 10 else "Unknown Date")
            if d not in grouped_by_date:
                grouped_by_date[d] = []
            grouped_by_date[d].append(s)

        first_top_item = None
        item_to_select = None
        for d, s_list in sorted(grouped_by_date.items(), reverse=True):
            date_item = QTreeWidgetItem([f"📅 {d} ({len(s_list)} run{'s' if len(s_list) != 1 else ''})"])
            date_item.setData(0, Qt.UserRole, {"type": "date", "date": d, "sessions": s_list})
            self.run_tree.addTopLevelItem(date_item)
            if not first_top_item:
                first_top_item = date_item

            for s in s_list:
                status_icon = "✅" if s["status"] == "SUCCESS" else "❌"
                raw_ts = s["flow_start_time"]
                time_str = raw_ts[11:19] if len(raw_ts) >= 19 else raw_ts
                flow_label = f" — {s['flow_name']}" if s.get("flow_name") and s["flow_name"] != "Standalone" and not self.flow_name else ""
                run_item = QTreeWidgetItem([f"{status_icon} {time_str}{flow_label} ({len(s['nodes'])} step{'s' if len(s['nodes']) != 1 else ''})"])
                run_item.setData(0, Qt.UserRole, {"type": "session", "session": s})
                date_item.addChild(run_item)

                # Add each node executed in this run as an expandable child item
                for n in s.get("nodes", []):
                    n_status_icon = "✅" if getattr(n, "status", "") == "SUCCESS" else "❌"
                    n_start = getattr(n, "node_start_time", "")
                    n_time = n_start[11:19] if len(n_start) >= 19 else ""
                    time_prefix = f"{n_time} - " if n_time else ""
                    node_item = QTreeWidgetItem([f"  {n_status_icon} {time_prefix}{n.node_name}"])
                    node_item.setData(0, Qt.UserRole, {"type": "node", "node": n, "session": s})
                    run_item.addChild(node_item)

                    if item_to_select is None and node_filter and n.node_name == node_filter:
                        item_to_select = node_item

                run_item.setExpanded(True)

            date_item.setExpanded(True)

        target = item_to_select or first_top_item
        if target:
            self.run_tree.setCurrentItem(target)
            self.run_tree.scrollToItem(target)
            self._on_tree_item_clicked(target, 0)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")
        selected_node = self.node_combo.currentText()
        node_filter = selected_node if selected_node != "All Nodes" else None

        if item_type == "date":
            # Show summary info at day level of combined totals and summaries of all process flow runs
            sessions = data.get("sessions", [])
            dt = data.get("date", "")
            md = format_day_summary_logs(dt, sessions, node_filter=node_filter)
            self.text_browser.setMarkdown(md)

        elif item_type == "session":
            # Show run level view: keep same metrics but without sql code and without '🔍 Submitted SQL Query'
            s = data.get("session", {})
            nodes = s.get("nodes", [])
            if node_filter:
                nodes = [n for n in nodes if n.node_name == node_filter]

            self._current_logs = nodes
            raw_ts = s.get("flow_start_time", "")
            time_part = raw_ts[11:19] if len(raw_ts) >= 19 else raw_ts
            title = f"Run Summary ({time_part})"
            # show_query=False removes SQL code and '🔍 Submitted SQL Query'
            # show_flow_name=False hides process flow name next to run
            md = format_run_session_logs(nodes, title=title, show_query=False, show_flow_name=False)
            self.text_browser.setMarkdown(md)

        elif item_type == "node":
            # In depth view of the single node run (includes full SQL query text for query nodes)
            n = data.get("node")
            if n:
                self._current_logs = [n]
                # show_query=True shows full SQL query
                # show_flow_name=False omits process flow name
                md = format_single_node_log(n, show_query=True, show_flow_name=False)
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

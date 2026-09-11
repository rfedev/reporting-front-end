"""Main application window for the Reporting Front End."""

import logging
from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from reporting_app.controllers.app_controller import AppController
from reporting_app.controllers.flow_controller import ProcessFlowController
from reporting_app.core.bigquery_run import run_bigquery_script
from reporting_app.core.models import ProcessFlowInfo, QueryInfo, QueryParameter, Report
from reporting_app.presentation.flow_editor.editor_window import ProcessFlowEditorWindow
from reporting_app.presentation.query_dialog import QueryRunDialog
from reporting_app.presentation.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Primary window presenting reports, process flows, and query controls."""

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.editor_windows: List[ProcessFlowEditorWindow] = []

        self.setWindowTitle("Reporting Front End")
        self.resize(720, 480)
        self.setMinimumSize(600, 420)

        self._build_ui()
        self._wire_signals()

        # Initial scan and load
        self.controller.initialize()

        # Requirement 4: Check Workbench Dataset prompt if not set
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._check_workbench_dataset)

    def _build_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # ---------------------------------------------------------------------
        # 1. Reports Section (Requirement 3: inline dropdown and icon buttons)
        # ---------------------------------------------------------------------
        report_layout = QHBoxLayout()
        report_label = QLabel("<b>Reports:</b>")
        report_label.setFixedWidth(100)
        self.reports_combo = QComboBox(self)
        self.reports_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.reports_combo.currentTextChanged.connect(self._on_report_selected)

        self.add_report_btn = QPushButton("➕")
        self.add_report_btn.setToolTip("Add Report")
        self.add_report_btn.setFixedWidth(36)
        self.add_report_btn.clicked.connect(self._on_add_report)

        self.remove_report_btn = QPushButton("🗑")
        self.remove_report_btn.setToolTip("Remove Report")
        self.remove_report_btn.setFixedWidth(36)
        self.remove_report_btn.clicked.connect(self._on_remove_report)

        report_layout.addWidget(report_label)
        report_layout.addWidget(self.reports_combo, 1)
        report_layout.addWidget(self.add_report_btn)
        report_layout.addWidget(self.remove_report_btn)
        main_layout.addLayout(report_layout)

        # ---------------------------------------------------------------------
        # 2. Process Flows Section (Requirement 3: inline dropdown and icon buttons)
        # ---------------------------------------------------------------------
        self.flows_group = QGroupBox("Process Flows", self)
        flows_layout = QVBoxLayout(self.flows_group)
        flows_layout.setSpacing(10)

        flow_row1 = QHBoxLayout()
        flow_label = QLabel("Process Flow:")
        flow_label.setFixedWidth(100)
        self.flows_combo = QComboBox(self)
        self.flows_combo.currentTextChanged.connect(self._on_flow_selected)

        self.edit_flow_btn = QPushButton("✏")
        self.edit_flow_btn.setToolTip("Edit Process Flow")
        self.edit_flow_btn.setFixedWidth(36)
        self.edit_flow_btn.clicked.connect(self._on_edit_process_flow)

        self.add_flow_btn = QPushButton("➕")
        self.add_flow_btn.setToolTip("Add Process Flow")
        self.add_flow_btn.setFixedWidth(36)
        self.add_flow_btn.clicked.connect(self._on_new_process_flow)

        self.remove_flow_btn = QPushButton("🗑")
        self.remove_flow_btn.setToolTip("Remove Process Flow")
        self.remove_flow_btn.setFixedWidth(36)
        self.remove_flow_btn.clicked.connect(self._on_remove_process_flow)

        self.run_flow_btn = QPushButton("▶")
        self.run_flow_btn.setToolTip("Run Process Flow")
        self.run_flow_btn.setFixedWidth(36)
        self.run_flow_btn.setStyleSheet("font-weight: bold; color: #2a82da;")
        self.run_flow_btn.clicked.connect(self._on_run_process_flow)

        flow_row1.addWidget(flow_label)
        flow_row1.addWidget(self.flows_combo, 1)
        flow_row1.addWidget(self.edit_flow_btn)
        flow_row1.addWidget(self.add_flow_btn)
        flow_row1.addWidget(self.remove_flow_btn)
        flow_row1.addWidget(self.run_flow_btn)
        flows_layout.addLayout(flow_row1)

        main_layout.addWidget(self.flows_group)

        # ---------------------------------------------------------------------
        # 3. Queries Section (Requirement 3: inline dropdown and icon buttons)
        # ---------------------------------------------------------------------
        self.queries_group = QGroupBox("Queries", self)
        queries_layout = QVBoxLayout(self.queries_group)
        queries_layout.setSpacing(10)

        query_row1 = QHBoxLayout()
        query_label = QLabel("Query:")
        query_label.setFixedWidth(100)
        self.queries_combo = QComboBox(self)
        self.queries_combo.currentTextChanged.connect(self._on_query_selected)

        self.edit_query_btn = QPushButton("✏")
        self.edit_query_btn.setToolTip("Edit Query (Open in Editor)")
        self.edit_query_btn.setFixedWidth(36)
        self.edit_query_btn.clicked.connect(self._on_edit_query)

        self.add_query_btn = QPushButton("➕")
        self.add_query_btn.setToolTip("Add Query")
        self.add_query_btn.setFixedWidth(36)
        self.add_query_btn.clicked.connect(self._on_add_query)

        self.remove_query_btn = QPushButton("🗑")
        self.remove_query_btn.setToolTip("Remove Query")
        self.remove_query_btn.setFixedWidth(36)
        self.remove_query_btn.clicked.connect(self._on_remove_query)

        self.run_query_btn = QPushButton("▶")
        self.run_query_btn.setToolTip("Run Query")
        self.run_query_btn.setFixedWidth(36)
        self.run_query_btn.setStyleSheet("font-weight: bold; color: #2a82da;")
        self.run_query_btn.clicked.connect(self._on_run_query)

        query_row1.addWidget(query_label)
        query_row1.addWidget(self.queries_combo, 1)
        query_row1.addWidget(self.edit_query_btn)
        query_row1.addWidget(self.add_query_btn)
        query_row1.addWidget(self.remove_query_btn)
        query_row1.addWidget(self.run_query_btn)
        queries_layout.addLayout(query_row1)

        main_layout.addWidget(self.queries_group)

        main_layout.addStretch()

        # ---------------------------------------------------------------------
        # 4. Bottom Controls: Settings Cog & Manual Sync
        # ---------------------------------------------------------------------
        bottom_layout = QHBoxLayout()

        self.settings_btn = QPushButton("⚙ Settings")
        self.settings_btn.setToolTip("Configure working directory and auto-scan")
        self.settings_btn.clicked.connect(self._on_open_settings)
        bottom_layout.addWidget(self.settings_btn)

        self.sync_btn = QPushButton("🔄 Sync / Refresh")
        self.sync_btn.setToolTip("Manually scan for reports, process flows, and queries")
        self.sync_btn.clicked.connect(self.controller.scan)
        bottom_layout.addWidget(self.sync_btn)

        bottom_layout.addStretch()
        main_layout.addLayout(bottom_layout)

        # Status Bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        # Update visibility of manual sync button based on auto-scan setting
        self._update_sync_button_visibility()

    def _check_workbench_dataset(self) -> None:
        """Prompt user for Workbench Dataset if not yet set (Requirement 4)."""
        current_wb = self.controller.repo.get_workbench_dataset()
        if not current_wb:
            default_val = SettingsDialog.DEFAULT_WORKBENCH_DATASET
            val, ok = QInputDialog.getText(
                self,
                "Workbench Dataset",
                "Please enter your team workbench dataset address:",
                text=default_val,
            )
            if ok and val.strip():
                self.controller.repo.set_workbench_dataset(val.strip())

    def _wire_signals(self) -> None:
        """Connect controller signals to UI slots."""
        self.controller.reports_updated.connect(self._update_reports_dropdown)
        self.controller.active_report_changed.connect(self._on_report_model_changed)
        self.controller.process_flows_updated.connect(self._update_flows_dropdown)
        self.controller.queries_updated.connect(self._update_queries_dropdown)
        self.controller.status_changed.connect(self.status_bar.showMessage)

    def _update_sync_button_visibility(self) -> None:
        """Show manual sync button if auto-scan is turned off."""
        auto_scan = self.controller.get_auto_scan()
        self.sync_btn.setVisible(not auto_scan)

    def _update_reports_dropdown(self, report_names: List[str]) -> None:
        self.reports_combo.blockSignals(True)
        self.reports_combo.clear()
        self.reports_combo.addItems(report_names)

        saved = self.controller.repo.get_selected_report()
        if saved and saved in report_names:
            self.reports_combo.setCurrentText(saved)
        elif report_names:
            self.reports_combo.setCurrentIndex(0)
        self.reports_combo.blockSignals(False)

        has_reports = len(report_names) > 0
        self.flows_group.setEnabled(has_reports)
        self.queries_group.setEnabled(has_reports)
        self.remove_report_btn.setEnabled(has_reports)

    def _on_report_model_changed(self, report: Optional[Report]) -> None:
        has_report = report is not None
        self.flows_group.setEnabled(has_report)
        self.queries_group.setEnabled(has_report)
        self.remove_report_btn.setEnabled(has_report)

    def _update_flows_dropdown(self, flow_names: List[str]) -> None:
        report_name = self.reports_combo.currentText()
        saved = self.controller.repo.get_selected_flow(report_name)
        current = self.controller.active_flow_name or saved or self.flows_combo.currentText()

        self.flows_combo.blockSignals(True)
        self.flows_combo.clear()
        self.flows_combo.addItems(flow_names)
        if current in flow_names:
            self.flows_combo.setCurrentText(current)
        elif flow_names:
            self.flows_combo.setCurrentIndex(0)
        self.flows_combo.blockSignals(False)

        has_flows = len(flow_names) > 0
        self.edit_flow_btn.setEnabled(has_flows)
        self.remove_flow_btn.setEnabled(has_flows)
        self.run_flow_btn.setEnabled(has_flows)

    def _update_queries_dropdown(self, query_names: List[str]) -> None:
        report_name = self.reports_combo.currentText()
        saved = self.controller.repo.get_selected_query(report_name)
        current = self.controller.active_query_name or saved or self.queries_combo.currentText()

        self.queries_combo.blockSignals(True)
        self.queries_combo.clear()
        self.queries_combo.addItems(query_names)
        if current in query_names:
            self.queries_combo.setCurrentText(current)
        elif query_names:
            self.queries_combo.setCurrentIndex(0)
        self.queries_combo.blockSignals(False)

        has_queries = len(query_names) > 0
        self.edit_query_btn.setEnabled(has_queries)
        self.remove_query_btn.setEnabled(has_queries)
        self.run_query_btn.setEnabled(has_queries)

    def _on_report_selected(self, report_name: str) -> None:
        if report_name:
            self.controller.select_report(report_name)

    def _on_flow_selected(self, flow_name: str) -> None:
        if flow_name:
            self.controller.select_process_flow(flow_name)
            self.run_flow_btn.setEnabled(True)

    def _on_query_selected(self, query_name: str) -> None:
        if query_name:
            self.controller.select_query(query_name)

    # --- Actions for Adding and Removing Entities (Requirement 3) ---

    def _on_add_report(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Report", "Enter new report name:")
        if ok and name.strip():
            rep = self.controller.add_report(name.strip())
            if rep:
                self.status_bar.showMessage(f"Report '{rep.name}' created.", 3000)

    def _on_remove_report(self) -> None:
        current = self.reports_combo.currentText()
        if not current:
            return
        confirm = QMessageBox.question(
            self,
            "Confirm Remove Report",
            f"Are you sure you want to remove report '{current}' and all its files?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.controller.remove_report(current)
            self.status_bar.showMessage(f"Report '{current}' removed.", 3000)

    def _on_remove_process_flow(self) -> None:
        current = self.flows_combo.currentText()
        if not current:
            return
        confirm = QMessageBox.question(
            self,
            "Confirm Remove Process Flow",
            f"Are you sure you want to remove process flow '{current}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.controller.remove_process_flow(current)
            self.status_bar.showMessage(f"Process flow '{current}' removed.", 3000)

    def _on_add_query(self) -> None:
        if not self.controller.active_report:
            QMessageBox.information(self, "No Report", "Please select a report first.")
            return
        name, ok = QInputDialog.getText(self, "Add Query", "Enter query name (without .sql):")
        if ok and name.strip():
            qinfo = self.controller.add_query(name.strip())
            if qinfo:
                self.status_bar.showMessage(f"Query '{qinfo.name}' created.", 3000)

    def _on_remove_query(self) -> None:
        current = self.queries_combo.currentText()
        if not current:
            return
        confirm = QMessageBox.question(
            self,
            "Confirm Remove Query",
            f"Are you sure you want to remove query '{current}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.controller.remove_query(current)
            self.status_bar.showMessage(f"Query '{current}' removed.", 3000)

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(
            current_dir=self.controller.get_working_directory(),
            auto_scan=self.controller.get_auto_scan(),
            workbench_dataset=self.controller.repo.get_workbench_dataset(),
            parent=self,
        )
        if dialog.exec() == SettingsDialog.Accepted:
            new_dir = dialog.get_working_directory()
            new_auto_scan = dialog.get_auto_scan()
            new_wb = dialog.get_workbench_dataset()

            self.controller.set_auto_scan(new_auto_scan)
            self.controller.repo.set_workbench_dataset(new_wb)
            self._update_sync_button_visibility()

            if new_dir != self.controller.get_working_directory():
                self.controller.set_working_directory(new_dir)

    def _on_edit_process_flow(self) -> None:
        report = self.controller.active_report
        if not report:
            QMessageBox.information(self, "No Report Selected", "Please select a report first.")
            return

        flow_name = self.flows_combo.currentText().strip()
        if not flow_name:
            QMessageBox.information(self, "No Flow Selected", "Please select a process flow to edit.")
            return

        flow_info = report.get_process_flow(flow_name)
        editor = ProcessFlowEditorWindow(
            report=report,
            flow_info=flow_info,
            flow_name=flow_name,
            app_controller=self.controller,
            parent=self,
        )
        self.editor_windows.append(editor)
        editor.show()

    def _on_new_process_flow(self) -> None:
        report = self.controller.active_report
        if not report:
            QMessageBox.information(self, "No Report Selected", "Please select a report first.")
            return

        flow_name, ok = QInputDialog.getText(
            self,
            "New Process Flow",
            "Enter name for the new process flow:",
            text=f"Process-{len(report.process_flows) + 1:02d}",
        )
        if ok and flow_name.strip():
            flow_name = flow_name.strip()
            editor = ProcessFlowEditorWindow(
                report=report,
                flow_info=None,
                flow_name=flow_name,
                app_controller=self.controller,
                parent=self,
            )
            self.editor_windows.append(editor)
            editor.show()

    def _on_edit_query(self) -> None:
        query_name = self.queries_combo.currentText()
        if not query_name:
            return
        success = self.controller.open_query_in_editor(query_name)
        if not success:
            QMessageBox.warning(
                self,
                "Error",
                f"Could not open query '{query_name}'. File not found.",
            )

    def _on_run_query(self) -> None:
        query_name = self.queries_combo.currentText()
        if not query_name:
            return

        qinfo = self.controller.get_query_info(query_name)
        if not qinfo:
            return

        # Fetch stored defaults from repository
        saved_defaults = self.controller.repo.get_all_defaults_for_scope("query", query_name)

        dialog = QueryRunDialog(
            query_info=qinfo,
            initial_defaults=saved_defaults,
            parent=self,
        )
        if dialog.exec() == QueryRunDialog.Accepted:
            values = dialog.get_parameter_values()
            # Save updated default values
            for param, val in values.items():
                self.controller.repo.set_parameter_default("query", query_name, param, val)

            active_report = self.controller.active_report
            if not active_report:
                return

            outputs_dir = active_report.folder_path / "outputs"
            outputs_dir.mkdir(parents=True, exist_ok=True)

            self.status_bar.showMessage(f"Running '{query_name}' in BigQuery...")
            QApplication.processEvents()

            try:
                res = run_bigquery_script(
                    sql_script_path=qinfo.file_path,
                    report_name=active_report.name,
                    outputs_dir=outputs_dir,
                    parameters=values,
                )
                msgs = []
                if res.get("has_output_tables"):
                    tbls = ", ".join(res.get("output_tables", []))
                    msgs.append(f"Created/updated table(s):\n{tbls}")
                if res.get("is_export"):
                    msgs.append(
                        f"Exported {res.get('row_count', 0):,} rows to:\n{res.get('output_file')}"
                    )
                if not msgs:
                    msgs.append("Query executed in BigQuery successfully.")

                msg = f"Query '{query_name}' completed successfully!\n\n" + "\n\n".join(msgs)
                self.status_bar.showMessage(f"Query '{query_name}' completed.", 5000)
                QMessageBox.information(self, "Query Completed", msg)
            except Exception as e:
                self.status_bar.showMessage(f"Query '{query_name}' failed.", 5000)
                QMessageBox.critical(
                    self,
                    "Query Execution Error",
                    f"Failed to execute query '{query_name}':\n\n{e}\n\nNote: If authentication failed, please run 'gcloud auth application-default login' in terminal.",
                )

    def _on_run_process_flow(self) -> None:
        """Execute the currently selected process flow."""
        active_report = self.controller.active_report
        flow_name = self.flows_combo.currentText()
        if not active_report or not flow_name:
            QMessageBox.information(self, "Select Flow", "Please select a valid process flow to run.")
            return

        flow_info = active_report.get_process_flow(flow_name)
        if not flow_info:
            QMessageBox.warning(self, "Flow Not Found", f"Process flow '{flow_name}' not found.")
            return

        flow_ctrl = ProcessFlowController(
            report=active_report,
            flow_info=flow_info,
            repo=self.controller.repo,
        )

        queries_order = flow_ctrl.get_execution_order()
        if not queries_order:
            QMessageBox.information(self, "Empty Flow", "There are no queries in this process flow to run.")
            return

        unique_params = flow_ctrl.get_unique_parameters(queries_order)
        param_values = dict(flow_ctrl.parameter_defaults)

        if unique_params:
            combined_qinfo = QueryInfo(
                name=f"Flow: {flow_ctrl.flow_name}",
                file_path=flow_ctrl.file_path,
                report_name=active_report.name,
                parameters=[QueryParameter(name=p, default_value=param_values.get(p, "")) for p in unique_params],
            )
            dialog = QueryRunDialog(
                query_info=combined_qinfo,
                initial_defaults=param_values,
                parent=self,
            )
            dialog.setWindowTitle(f"Run Process Flow - {flow_ctrl.flow_name}")
            dialog.run_btn.setText("Run Process Flow")

            if dialog.exec() != QueryRunDialog.Accepted:
                return

            param_values = dialog.get_parameter_values()
            flow_ctrl.parameter_defaults.update(param_values)

        outputs_dir = active_report.folder_path / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        csv_map = flow_ctrl.get_csv_filenames()

        results_log = []
        errors = []

        self.status_bar.showMessage(f"Running process flow ({len(queries_order)} queries)...")

        for idx, qname in enumerate(queries_order, start=1):
            qinfo = active_report.get_query(qname)
            if not qinfo or not qinfo.file_path.exists():
                err = f"Query '{qname}' SQL file not found."
                errors.append(err)
                results_log.append(f"[{idx}/{len(queries_order)}] ❌ {qname}: {err}")
                break

            self.status_bar.showMessage(f"[{idx}/{len(queries_order)}] Running {qname}...")
            QApplication.processEvents()

            try:
                custom_csv = csv_map.get(qname)
                res = run_bigquery_script(
                    sql_script_path=qinfo.file_path,
                    report_name=active_report.name,
                    outputs_dir=outputs_dir,
                    parameters=param_values,
                    output_filename=custom_csv,
                )
                log_parts = []
                if res.get("has_output_tables"):
                    log_parts.append(f"Table(s): {', '.join(res.get('output_tables', []))}")
                if res.get("is_export"):
                    log_parts.append(f"Exported {res.get('row_count', 0):,} rows to {res.get('output_file')}")
                if not log_parts:
                    log_parts.append("Executed successfully.")

                results_log.append(f"[{idx}/{len(queries_order)}] 📦 {qname}: {' | '.join(log_parts)}")
            except Exception as e:
                err = f"Execution failed: {e}"
                errors.append(f"{qname}: {e}")
                results_log.append(f"[{idx}/{len(queries_order)}] ❌ {qname}: {err}")
                break

        if errors:
            self.status_bar.showMessage(f"Process flow '{flow_ctrl.flow_name}' failed.", 5000)
            QMessageBox.critical(
                self,
                "Process Flow Execution Error",
                f"Process flow stopped due to an error:\n\n" + "\n".join(results_log),
            )
        else:
            self.status_bar.showMessage(f"Process flow '{flow_ctrl.flow_name}' completed.", 5000)
            QMessageBox.information(
                self,
                "Process Flow Completed",
                f"Successfully executed all {len(queries_order)} query step(s):\n\n" + "\n".join(results_log),
            )


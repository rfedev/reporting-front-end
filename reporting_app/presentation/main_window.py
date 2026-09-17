"""Main application window for the Reporting Front End."""

from pathlib import Path
import logging
from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from reporting_app.utils.date_calc import format_filename_with_date

logger = logging.getLogger(__name__)


class AddReportDialog(QDialog):
    """Dialog prompting for report name and working directory alias."""

    def __init__(self, directory_aliases: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Report")
        self.resize(380, 150)
        self._aliases = directory_aliases

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        name_layout = QHBoxLayout()
        name_label = QLabel("Report Name:")
        name_label.setFixedWidth(120)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("e.g. monthly_sales_summary")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        dir_layout = QHBoxLayout()
        dir_label = QLabel("Working Directory:")
        dir_label.setFixedWidth(120)
        self.alias_combo = QComboBox(self)
        self.alias_combo.addItems(self._aliases)
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.alias_combo, 1)
        layout.addLayout(dir_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        ok_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        self.name_edit.setFocus()

    def _on_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a report name.")
            return
        self.accept()

    def get_report_name(self) -> str:
        return self.name_edit.text().strip()

    def get_directory_alias(self) -> str:
        return self.alias_combo.currentText()


class NewProcessFlowDialog(QDialog):
    """Dialog for creating a new process flow, optionally cloning an existing flow."""

    def __init__(self, default_name: str, existing_flows: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Process Flow")
        self.resize(380, 160)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Name row
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Flow Name:"))
        self.name_edit = QLineEdit(default_name)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Clone flow checkbox
        self.clone_cb = QCheckBox("Clone flow")
        layout.addWidget(self.clone_cb)

        # Clone dropdown row
        clone_layout = QHBoxLayout()
        self.clone_label = QLabel("Source flow:")
        self.clone_combo = QComboBox()
        self.clone_combo.addItems(existing_flows)
        clone_layout.addWidget(self.clone_label)
        clone_layout.addWidget(self.clone_combo, 1)
        layout.addLayout(clone_layout)

        # Initial visibility/enablement of clone dropdown
        has_existing = bool(existing_flows)
        self.clone_cb.setEnabled(has_existing)
        self.clone_label.setVisible(False)
        self.clone_combo.setVisible(False)

        def toggle_clone(checked: bool):
            self.clone_label.setVisible(checked)
            self.clone_combo.setVisible(checked)
            self.adjustSize()

        self.clone_cb.toggled.connect(toggle_clone)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def get_flow_name(self) -> str:
        return self.name_edit.text().strip()

    def get_source_flow(self) -> Optional[str]:
        if self.clone_cb.isChecked():
            return self.clone_combo.currentText().strip() or None
        return None


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
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(20, 20, 20, 20)

        BTN_SIZE = 32

        # ---------------------------------------------------------------------
        # 1. Reports Section (Requirement 3: inline dropdown and icon buttons)
        # ---------------------------------------------------------------------
        report_layout = QHBoxLayout()
        report_label = QLabel("<b>Reports:</b>")
        report_label.setFixedWidth(100)
        self.reports_combo = QComboBox(self)
        self.reports_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.reports_combo.currentTextChanged.connect(self._on_report_selected)

        self.open_folder_btn = QPushButton("📁")
        self.open_folder_btn.setToolTip("Open Report Folder in File Browser")
        self.open_folder_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.open_folder_btn.clicked.connect(self._on_open_report_folder)

        self.rename_report_btn = QPushButton("✏️")
        self.rename_report_btn.setToolTip("Rename Report")
        self.rename_report_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.rename_report_btn.clicked.connect(self._on_rename_report)

        self.add_report_btn = QPushButton("➕")
        self.add_report_btn.setToolTip("Add Report")
        self.add_report_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.add_report_btn.clicked.connect(self._on_add_report)

        self.remove_report_btn = QPushButton("🗑")
        self.remove_report_btn.setToolTip("Remove Report")
        self.remove_report_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.remove_report_btn.clicked.connect(self._on_remove_report)

        report_layout.addWidget(report_label)
        report_layout.addWidget(self.reports_combo, 1)
        report_layout.addWidget(self.open_folder_btn)
        report_layout.addWidget(self.rename_report_btn)
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

        self.edit_flow_btn = QPushButton("📘 Open")
        self.edit_flow_btn.setToolTip("Edit Process Flow")
        self.edit_flow_btn.setFixedSize(80, BTN_SIZE)
        self.edit_flow_btn.clicked.connect(self._on_edit_process_flow)

        self.rename_flow_btn = QPushButton("✏️")
        self.rename_flow_btn.setToolTip("Rename Process Flow")
        self.rename_flow_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.rename_flow_btn.clicked.connect(self._on_rename_process_flow)

        self.add_flow_btn = QPushButton("➕")
        self.add_flow_btn.setToolTip("Add Process Flow")
        self.add_flow_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.add_flow_btn.clicked.connect(self._on_new_process_flow)

        self.run_flow_btn = QPushButton("▶")
        self.run_flow_btn.setToolTip("Run Process Flow")
        self.run_flow_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.run_flow_btn.setStyleSheet("font-weight: bold; color: #2a82da;")
        self.run_flow_btn.clicked.connect(self._on_run_process_flow)

        self.remove_flow_btn = QPushButton("🗑")
        self.remove_flow_btn.setToolTip("Remove Process Flow")
        self.remove_flow_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.remove_flow_btn.clicked.connect(self._on_remove_process_flow)

        flow_row1.addWidget(flow_label)
        flow_row1.addWidget(self.flows_combo, 1)
        flow_row1.addWidget(self.edit_flow_btn)
        flow_row1.addWidget(self.rename_flow_btn)
        flow_row1.addWidget(self.add_flow_btn)
        flow_row1.addWidget(self.run_flow_btn)
        flow_row1.addWidget(self.remove_flow_btn)

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

        self.edit_query_btn = QPushButton("📘 Open")
        self.edit_query_btn.setToolTip("Edit Query")
        self.edit_query_btn.setFixedSize(80, BTN_SIZE)
        self.edit_query_btn.clicked.connect(self._on_edit_query)

        self.rename_query_btn = QPushButton("✏️")
        self.rename_query_btn.setToolTip("Rename Query")
        self.rename_query_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.rename_query_btn.clicked.connect(self._on_rename_query)

        self.add_query_btn = QPushButton("➕")
        self.add_query_btn.setToolTip("Add Query")
        self.add_query_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.add_query_btn.clicked.connect(self._on_add_query)

        self.run_query_btn = QPushButton("▶")
        self.run_query_btn.setToolTip("Run Query")
        self.run_query_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.run_query_btn.setStyleSheet("font-weight: bold; color: #2a82da;")
        self.run_query_btn.clicked.connect(self._on_run_query)

        self.remove_query_btn = QPushButton("🗑")
        self.remove_query_btn.setToolTip("Remove Query")
        self.remove_query_btn.setFixedSize(BTN_SIZE, BTN_SIZE)
        self.remove_query_btn.clicked.connect(self._on_remove_query)

        query_row1.addWidget(query_label)
        query_row1.addWidget(self.queries_combo, 1)
        query_row1.addWidget(self.edit_query_btn)
        query_row1.addWidget(self.rename_query_btn)
        query_row1.addWidget(self.add_query_btn)
        query_row1.addWidget(self.run_query_btn)
        query_row1.addWidget(self.remove_query_btn)
        queries_layout.addLayout(query_row1)

        main_layout.addWidget(self.queries_group)

        main_layout.addStretch()

        # ---------------------------------------------------------------------
        # 4. Bottom Controls: Settings Cog, Manual Sync & Auth
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

        self.auth_btn = QPushButton("Auth")
        self.auth_btn.setToolTip("Run 'gcloud auth application-default login' in a terminal")
        self.auth_btn.clicked.connect(self._on_auth_login)
        bottom_layout.addWidget(self.auth_btn)

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
        self.rename_report_btn.setEnabled(has_reports)
        self.open_folder_btn.setEnabled(has_reports)
        self.remove_report_btn.setEnabled(has_reports)

    def _on_report_model_changed(self, report: Optional[Report]) -> None:
        has_report = report is not None
        self.flows_group.setEnabled(has_report)
        self.queries_group.setEnabled(has_report)
        self.rename_report_btn.setEnabled(has_report)
        self.open_folder_btn.setEnabled(has_report)
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
        self.rename_flow_btn.setEnabled(has_flows)
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
        self.rename_query_btn.setEnabled(has_queries)
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
        working_dirs = self.controller.get_working_directories()
        aliases = [d.get("alias", "Default") for d in working_dirs if d.get("alias")]
        if not aliases:
            aliases = ["Default"]

        dialog = AddReportDialog(directory_aliases=aliases, parent=self)
        if dialog.exec() == QDialog.Accepted:
            rep_name = dialog.get_report_name()
            selected_alias = dialog.get_directory_alias()
            if rep_name:
                rep = self.controller.add_report(rep_name, directory_alias=selected_alias)
                if rep:
                    self.status_bar.showMessage(f"Report '{rep.name}' created in [{selected_alias}].", 3000)

    def _on_rename_report(self) -> None:
        rep = self.controller.active_report
        if not rep:
            QMessageBox.information(self, "No Report", "Please select a report first.")
            return
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Report",
            "Enter new report name:",
            text=rep.name,
        )
        if ok and new_name.strip() and new_name.strip() != rep.name:
            current_key = self.reports_combo.currentText()
            success = self.controller.rename_report(current_key, new_name.strip())
            if success:
                self.status_bar.showMessage(f"Report renamed to '{new_name.strip()}'.", 3000)
            else:
                QMessageBox.warning(self, "Rename Failed", f"Could not rename report to '{new_name.strip()}'.")

    def _on_open_report_folder(self) -> None:
        """Open the active report folder in the native file browser."""
        rep = self.controller.active_report
        if not rep or not rep.folder_path.exists():
            QMessageBox.information(self, "No Report", "Please select a report first.")
            return

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder_url = QUrl.fromLocalFile(str(rep.folder_path.resolve()))
        opened = QDesktopServices.openUrl(folder_url)
        if not opened:
            import os, platform, subprocess
            sys_name = platform.system()
            path_str = str(rep.folder_path.resolve())
            try:
                if sys_name == "Windows":
                    os.startfile(path_str)
                elif sys_name == "Darwin":
                    subprocess.Popen(["open", path_str])
                else:
                    subprocess.Popen(["xdg-open", path_str])
            except Exception as e:
                logger.error(f"Failed to open report folder: {e}")

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

    def _on_rename_process_flow(self) -> None:
        report = self.controller.active_report
        if not report:
            QMessageBox.information(self, "No Report Selected", "Please select a report first.")
            return
        current_flow = self.flows_combo.currentText().strip()
        if not current_flow:
            QMessageBox.information(self, "No Flow Selected", "Please select a process flow first.")
            return
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Process Flow",
            "Enter new process flow name:",
            text=current_flow,
        )
        if ok and new_name.strip() and new_name.strip() != current_flow:
            success = self.controller.rename_process_flow(current_flow, new_name.strip())
            if success:
                self.status_bar.showMessage(f"Process flow renamed to '{new_name.strip()}'.", 3000)
            else:
                QMessageBox.warning(self, "Rename Failed", f"Could not rename process flow to '{new_name.strip()}'.")

    def _on_auth_login(self) -> None:
        """Run 'gcloud auth application-default login' in a terminal."""
        from reporting_app.utils.terminal import launch_in_terminal
        self.status_bar.showMessage("Opening terminal for gcloud authentication...", 3000)
        success = launch_in_terminal("gcloud auth application-default login", title="Google Cloud Authentication")
        if not success:
            QMessageBox.warning(
                self,
                "Authentication Error",
                "Could not launch terminal automatically.\nPlease run 'gcloud auth application-default login' manually in a terminal.",
            )

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

    def _on_rename_query(self) -> None:
        current_query = self.queries_combo.currentText().strip()
        if not current_query:
            return

        new_name, ok = QInputDialog.getText(
            self,
            "Rename Query",
            "Enter new query name (without .sql):",
            text=current_query,
        )
        if ok and new_name.strip() and new_name.strip() != current_query:
            clean_name = new_name.strip()
            if clean_name.lower().endswith(".sql"):
                clean_name = clean_name[:-4]
            success = self.controller.rename_query(current_query, clean_name)
            if success:
                self.status_bar.showMessage(f"Query renamed to '{clean_name}'.", 3000)
            else:
                QMessageBox.warning(self, "Rename Failed", f"Could not rename query to '{clean_name}'.")

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
            working_directories=self.controller.get_working_directories(),
            auto_scan=self.controller.get_auto_scan(),
            workbench_dataset=self.controller.repo.get_workbench_dataset(),
            log_database_path=self.controller.get_log_database_path(),
            parent=self,
        )
        if dialog.exec() == SettingsDialog.Accepted:
            new_dirs = dialog.get_working_directories()
            new_auto_scan = dialog.get_auto_scan()
            new_wb = dialog.get_workbench_dataset()
            new_log_db = dialog.get_log_database_path()

            self.controller.set_auto_scan(new_auto_scan)
            self.controller.repo.set_workbench_dataset(new_wb)
            if new_log_db and new_log_db != self.controller.get_log_database_path():
                self.controller.set_log_database_path(new_log_db)
            self._update_sync_button_visibility()

            if new_dirs != self.controller.get_working_directories():
                self.controller.set_working_directories(new_dirs)

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

        existing_flows = [f.name for f in report.process_flows]
        default_name = f"Process-{len(report.process_flows) + 1:02d}"
        dlg = NewProcessFlowDialog(default_name, existing_flows, self)
        if dlg.exec() != QDialog.Accepted:
            return

        flow_name = dlg.get_flow_name()
        if not flow_name:
            return

        source_flow = dlg.get_source_flow()
        flow_info = self.controller.add_process_flow(flow_name, source_flow_name=source_flow)
        editor = ProcessFlowEditorWindow(
            report=report,
            flow_info=flow_info,
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

        qinfo = self.controller.get_query_info(query_name, ensure_parsed=True)
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

            import uuid
            from datetime import datetime, timezone
            run_id = str(uuid.uuid4())
            start_ts = datetime.now(timezone.utc).isoformat()
            t0 = datetime.now(timezone.utc)
            repo = self.controller.repo

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
                    rc = res.get("row_count")
                    rc_str = f" ({rc:,} rows)" if rc is not None else ""
                    msgs.append(f"Created/updated table(s){rc_str}:\n{tbls}")
                if res.get("is_export"):
                    details = res.get("export_details", [])
                    if len(details) == 1:
                        d = details[0]
                        msgs.append(f"Exported '{d['filename']}' ({d['row_count']:,} rows)")
                    elif len(details) > 1:
                        lines = [f"Exported {len(details)} tables:"]
                        for d in details:
                            lines.append(f"* {d['filename']} ({d['row_count']:,} rows)")
                        msgs.append("\n".join(lines))
                    else:
                        msgs.append(
                            f"Exported {res.get('row_count', 0):,} rows to:\n{res.get('output_file')}"
                        )
                if not msgs:
                    msgs.append("Query executed in BigQuery successfully.")

                if repo:
                    t1 = datetime.now(timezone.utc)
                    wall_node_dur = (t1 - t0).total_seconds()
                    repo.record_execution_log({
                        "run_id": run_id,
                        "flow_start_time": start_ts,
                        "node_start_time": start_ts,
                        "node_end_time": t1.isoformat(),
                        "duration_seconds": max(float(res.get("duration_seconds", 0.0) or 0.0), wall_node_dur),
                        "bq_duration_seconds": res.get("bq_duration_seconds"),
                        "report_name": active_report.name,
                        "flow_name": "Standalone",
                        "node_type": "query",
                        "node_name": query_name,
                        "status": "SUCCESS",
                        "submitted_query": res.get("submitted_query"),
                        "output_rows": res.get("row_count"),
                        "total_bytes_processed": res.get("total_bytes_processed"),
                        "total_bytes_billed": res.get("total_bytes_billed"),
                        "slot_millis": res.get("slot_millis"),
                        "cache_hit": res.get("cache_hit"),
                        "export_details_json": res.get("export_details", []),
                    })

                msg = f"Query '{query_name}' completed successfully!\n\n" + "\n\n".join(msgs)
                self.status_bar.showMessage(f"Query '{query_name}' completed.", 5000)
                QMessageBox.information(self, "Query Completed", msg)
            except Exception as e:
                if repo:
                    t1 = datetime.now(timezone.utc)
                    repo.record_execution_log({
                        "run_id": run_id,
                        "flow_start_time": start_ts,
                        "node_start_time": start_ts,
                        "node_end_time": t1.isoformat(),
                        "duration_seconds": (t1 - t0).total_seconds(),
                        "report_name": active_report.name,
                        "flow_name": "Standalone",
                        "node_type": "query",
                        "node_name": query_name,
                        "status": "FAILED",
                        "error_message": str(e),
                    })
                self.status_bar.showMessage(f"Query '{query_name}' failed.", 5000)
                QMessageBox.critical(
                    self,
                    "Query Execution Error",
                    f"Error in query node: '{query_name}'\n\nFailed to execute query '{query_name}':\n\n{e}\n\nNote: If authentication failed, please run 'gcloud auth application-default login' in terminal.",
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

        active_report.ensure_queries_parsed(queries_order)
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

        # Determine filename_date from flow parameter settings or defaults
        filename_date = ""
        if "Report Date" in param_values and param_values["Report Date"].strip():
            filename_date = param_values["Report Date"].strip()
        else:
            sel_param = flow_ctrl.get_selected_filename_date_param()
            if sel_param and sel_param in param_values:
                filename_date = param_values[sel_param]
            else:
                for k, v in param_values.items():
                    if any(token in k.lower() for token in ("date", "dt", "day", "month", "year")):
                        filename_date = v
                        break

        results_log = []
        errors = []

        failed_node_name = None

        # 1. Run CSV Imports if present
        csv_imports = flow_ctrl.get_csv_imports()
        for group in csv_imports:
            imp_items = group.get("items", []) if isinstance(group, dict) and "items" in group else [group]
            for item in imp_items:
                f_path = (item.get("file_path") or item.get("csv_path") or "").strip()
                d_table = item.get("output_table", "").strip()
                headers = item.get("has_headers", True)
                sheet_name = item.get("sheet_name")
                schema_mode = item.get("schema_mode", "auto")
                manual_schema = item.get("manual_schema")
                if not f_path or not d_table:
                    continue
                if filename_date:
                    f_path = format_filename_with_date(f_path, filename_date)

                resolved_file = Path(f_path)
                if not resolved_file.is_absolute() and active_report and active_report.folder_path:
                    candidate = active_report.folder_path / "inputs" / resolved_file
                    if candidate.exists() or not resolved_file.exists():
                        resolved_file = candidate

                self.status_bar.showMessage(f"Importing {resolved_file.name} -> {d_table}...")
                QApplication.processEvents()
                try:
                    from reporting_app.core.bigquery_run import run_bigquery_import_file
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
                    results_log.append(f"📥 Imported '{f_path}' into '{d_table}' ({cnt_str})")
                except Exception as e:
                    failed_node_name = group.get("node_name", "Import Files") if isinstance(group, dict) else "Import Files"
                    err_msg = f"Import failed for {d_table}: {e}"
                    errors.append(err_msg)
                    results_log.append(f"❌ {err_msg}")
                    break
            if errors:
                break

        if not errors:
            self.status_bar.showMessage(f"Running process flow ({len(queries_order)} queries)...")

        for idx, qname in enumerate(queries_order, start=1):
            qinfo = active_report.get_query(qname)
            if not qinfo or not qinfo.file_path.exists():
                failed_node_name = qname
                err = f"Query '{qname}' SQL file not found."
                errors.append(err)
                results_log.append(f"[{idx}/{len(queries_order)}] ❌ {qname}: {err}")
                break

            self.status_bar.showMessage(f"[{idx}/{len(queries_order)}] Running {qname}...")
            QApplication.processEvents()

            try:
                custom_csv = csv_map.get(qname)
                if custom_csv and filename_date:
                    custom_csv = format_filename_with_date(custom_csv, filename_date)
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
                    details = res.get("export_details", [])
                    if len(details) == 1:
                        d = details[0]
                        log_parts.append(f"Exported '{d['filename']}' ({d['row_count']:,} rows)")
                    elif len(details) > 1:
                        exp_lines = [f"Exported {len(details)} tables:"]
                        for d in details:
                            exp_lines.append(f"* {d['filename']} ({d['row_count']:,} rows)")
                        log_parts.append("\n".join(exp_lines))
                    else:
                        log_parts.append(f"Exported {res.get('row_count', 0):,} rows to {res.get('output_file')}")
                if not log_parts:
                    log_parts.append("Executed successfully.")

                results_log.append(f"[{idx}/{len(queries_order)}] 📦 {qname}:\n" + "\n".join(log_parts) if any("\n" in p for p in log_parts) else f"[{idx}/{len(queries_order)}] 📦 {qname}: {' | '.join(log_parts)}")
            except Exception as e:
                failed_node_name = qname
                err = f"Execution failed: {e}"
                errors.append(f"{qname}: {e}")
                results_log.append(f"[{idx}/{len(queries_order)}] ❌ {qname}: {err}")
                break

        if errors:
            failed_node_header = f"Error in node: '{failed_node_name}'\n\n" if failed_node_name else ""
            self.status_bar.showMessage(f"Process flow '{flow_ctrl.flow_name}' failed.", 5000)
            QMessageBox.critical(
                self,
                "Process Flow Execution Error",
                f"{failed_node_header}Process flow stopped due to an error:\n\n" + "\n".join(results_log),
            )
        else:
            self.status_bar.showMessage(f"Process flow '{flow_ctrl.flow_name}' completed.", 5000)
            QMessageBox.information(
                self,
                "Process Flow Completed",
                f"Successfully executed all {len(queries_order)} query step(s):\n\n" + "\n".join(results_log),
            )


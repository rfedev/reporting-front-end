"""Settings dialog for the application."""

from pathlib import Path
from typing import Dict, List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
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
        self.resize(640, 420)

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
        dirs_group = QGroupBox("Working Directories")
        dirs_layout = QVBoxLayout(dirs_group)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Alias", "Directory Path"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        dirs_layout.addWidget(self.table)

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
        dirs_layout.addLayout(btn_row)
        layout.addWidget(dirs_group)

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

"""Settings dialog for the application."""

from pathlib import Path
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    """Dialog allowing configuration of working directory, workbench dataset, and auto-scan preferences."""

    DEFAULT_WORKBENCH_DATASET = "iw-gid-prd-01-c683.gid_art_yourworkbenchID"

    def __init__(
        self,
        current_dir: Path,
        auto_scan: bool,
        workbench_dataset: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 220)

        self._selected_dir = current_dir
        self._auto_scan = auto_scan
        self._workbench_dataset = workbench_dataset

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # Working Directory row
        dir_layout = QHBoxLayout()
        self.dir_edit = QLineEdit(str(self._selected_dir.resolve()))
        self.dir_edit.setReadOnly(True)
        dir_layout.addWidget(self.dir_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse_clicked)
        dir_layout.addWidget(browse_btn)

        form_layout.addRow("Working Directory:", dir_layout)

        # Workbench Dataset row (Requirement 4)
        initial_workbench = self._workbench_dataset or self.DEFAULT_WORKBENCH_DATASET
        self.workbench_edit = QLineEdit(initial_workbench)
        self.workbench_edit.setPlaceholderText(self.DEFAULT_WORKBENCH_DATASET)
        form_layout.addRow("Workbench Dataset:", self.workbench_edit)

        # Auto-scan checkbox
        self.auto_scan_checkbox = QCheckBox("Enable auto-scanning for reports, flows, and queries")
        self.auto_scan_checkbox.setChecked(self._auto_scan)
        form_layout.addRow("Auto-Scan:", self.auto_scan_checkbox)

        layout.addLayout(form_layout)

        # Dialog buttons (OK / Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Working Directory",
            str(self._selected_dir.resolve()),
        )
        if chosen:
            self._selected_dir = Path(chosen)
            self.dir_edit.setText(chosen)

    def get_working_directory(self) -> Path:
        return Path(self.dir_edit.text())

    def get_workbench_dataset(self) -> str:
        return self.workbench_edit.text().strip()

    def get_auto_scan(self) -> bool:
        return self.auto_scan_checkbox.isChecked()

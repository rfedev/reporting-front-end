"""Dialog for displaying process flow execution error details."""

from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class FlowErrorDialog(QDialog):
    """Clean dialog displaying detailed error messages from a failed process flow run."""

    def __init__(
        self,
        flow_name: str,
        failed_node: Optional[str] = None,
        errors: Optional[List[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Execution Error - {flow_name}")
        self.resize(620, 380)
        self.setModal(True)

        self._errors = list(errors or [])
        self._failed_node = failed_node

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header_text = (
            f"Step '{self._failed_node}' failed during execution:"
            if self._failed_node
            else "Process flow execution encountered an error:"
        )
        header_lbl = QLabel(f"❌ {header_text}")
        header_lbl.setStyleSheet("color: #f87171; font-size: 13px; font-weight: bold;")
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # Error text area
        self.error_text_edit = QPlainTextEdit()
        self.error_text_edit.setReadOnly(True)
        full_text = "\n\n".join(self._errors) if self._errors else "Unknown execution error."
        self.error_text_edit.setPlainText(full_text)
        self.error_text_edit.setStyleSheet(
            "QPlainTextEdit {"
            "  background-color: #16181d;"
            "  color: #e5e7eb;"
            "  border: 1px solid #374151;"
            "  border-radius: 6px;"
            "  padding: 10px;"
            "  font-family: monospace;"
            "  font-size: 11px;"
            "}"
        )
        layout.addWidget(self.error_text_edit, stretch=1)

        # Optional contextual hints for permissions or credentials
        lower_err = full_text.lower()
        if any(k in lower_err for k in ("roles/bigquery", "bigquery.jobs.create", "iam permission", "access denied", "403")):
            hint_lbl = QLabel(
                "💡 BigQuery Permission Tip: Ensure your Google Cloud account has 'roles/bigquery.jobUser' on the project to execute queries, and 'roles/bigquery.dataViewer' to access tables."
            )
            hint_lbl.setStyleSheet("color: #60a5fa; font-size: 11px; font-weight: 500;")
            hint_lbl.setWordWrap(True)
            layout.addWidget(hint_lbl)
        elif any(k in lower_err for k in ("credentials", "auth", "401", "reauthentication", "gcloud")):
            hint_lbl = QLabel(
                "💡 Authentication Tip: If credentials expired or are missing, run 'gcloud auth application-default login' in your terminal."
            )
            hint_lbl.setStyleSheet("color: #fbbf24; font-size: 11px; font-style: italic;")
            hint_lbl.setWordWrap(True)
            layout.addWidget(hint_lbl)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.copy_btn = QPushButton("Copy Error")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #2d333b;"
            "  color: #e5e7eb;"
            "  border: 1px solid #444c56;"
            "  border-radius: 4px;"
            "  padding: 6px 14px;"
            "}"
            "QPushButton:hover { background-color: #373e47; }"
        )
        self.copy_btn.clicked.connect(self._on_copy)
        btn_row.addWidget(self.copy_btn)

        btn_row.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setDefault(True)
        close_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #2b78e4;"
            "  color: white;"
            "  font-weight: bold;"
            "  border: none;"
            "  border-radius: 4px;"
            "  padding: 6px 18px;"
            "}"
            "QPushButton:hover { background-color: #3b82f6; }"
        )
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _on_copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.error_text_edit.toPlainText())
            self.copy_btn.setText("Copied!")

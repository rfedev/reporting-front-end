"""Modal progress dialog for background process flow execution."""

import logging
from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from reporting_app.controllers.flow_runner import FlowWorker

logger = logging.getLogger(__name__)


class FlowProgressDialog(QDialog):
    """Non-blocking modal dialog displaying real-time execution progress and logs."""

    def __init__(
        self,
        worker: FlowWorker,
        flow_name: str,
        total_steps: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.worker = worker
        self.flow_name = flow_name
        self.total_steps = max(total_steps, 1)
        self.is_finished: bool = False
        self.success: bool = False
        self.summary_message: str = ""
        self.results_log: List[str] = []
        self.errors: List[str] = []

        self.setWindowTitle(f"Running - {self.flow_name}")
        self.resize(650, 420)
        self.setWindowModality(Qt.WindowModal)

        self._build_ui()
        self._wire_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header status label
        self.status_label = QLabel(f"<b>Initializing execution of '{self.flow_name}'...</b>", self)
        self.status_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, self.total_steps)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m steps (%p%)")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 4px;
                text-align: center;
                height: 22px;
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #2b78e4;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)

        # Log viewer console
        layout.addWidget(QLabel("<b>Execution Log:</b>", self))
        self.log_console = QPlainTextEdit(self)
        self.log_console.setReadOnly(True)
        font = QFont("Monospace", 9)
        font.setStyleHint(QFont.Monospace)
        self.log_console.setFont(font)
        self.log_console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        layout.addWidget(self.log_console, 1)

        # Bottom button bar
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()

        self.action_btn = QPushButton("Cancel", self)
        self.action_btn.setFixedWidth(110)
        self.action_btn.clicked.connect(self._on_action_clicked)
        bottom_bar.addWidget(self.action_btn)

        layout.addLayout(bottom_bar)

    def _wire_signals(self) -> None:
        self.worker.progress_updated.connect(self._on_progress_updated)
        self.worker.log_message.connect(self._on_log_message)
        self.worker.flow_finished.connect(self._on_flow_finished)

    def _on_progress_updated(self, current_step: int, total_steps: int, step_name: str) -> None:
        self.progress_bar.setRange(0, total_steps)
        self.progress_bar.setValue(current_step)
        self.status_label.setText(f"<b>Step {current_step} of {total_steps}:</b> {step_name}")

    def _on_log_message(self, message: str) -> None:
        self.log_console.appendPlainText(message)
        # Scroll to bottom
        sb = self.log_console.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_flow_finished(self, success: bool, summary: str, results_log: list, errors: list) -> None:
        self.is_finished = True
        self.success = success
        self.summary_message = summary
        self.results_log = results_log
        self.errors = errors

        if success:
            self.status_label.setText("<b>Execution completed successfully!</b>")
            self.progress_bar.setValue(self.progress_bar.maximum())
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #444;
                    border-radius: 4px;
                    text-align: center;
                    height: 22px;
                    background-color: #2b2b2b;
                    color: #ffffff;
                }
                QProgressBar::chunk {
                    background-color: #2da44e;
                    border-radius: 3px;
                }
            """)
        elif self.worker.is_cancelled():
            self.status_label.setText("<b>Execution was cancelled by user.</b>")
        else:
            self.status_label.setText("<b>Execution halted due to an error.</b>")
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #444;
                    border-radius: 4px;
                    text-align: center;
                    height: 22px;
                    background-color: #2b2b2b;
                    color: #ffffff;
                }
                QProgressBar::chunk {
                    background-color: #cf222e;
                    border-radius: 3px;
                }
            """)

        self.action_btn.setText("Close")
        self.action_btn.setEnabled(True)
        self.action_btn.setStyleSheet("font-weight: bold;")

    def _on_action_clicked(self) -> None:
        if not self.is_finished:
            self.action_btn.setText("Cancelling...")
            self.action_btn.setEnabled(False)
            self.worker.cancel()
        else:
            if self.success:
                self.accept()
            else:
                self.reject()

    def closeEvent(self, event) -> None:
        if not self.is_finished:
            # User clicked the X button while execution is running
            self.worker.cancel()
            self.action_btn.setText("Cancelling...")
            self.action_btn.setEnabled(False)
            event.ignore()
        else:
            event.accept()

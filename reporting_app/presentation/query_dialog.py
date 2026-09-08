"""Dialog for configuring and running a query with parameters."""

from datetime import date
from typing import Dict, List, Optional
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCalendarWidget,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from reporting_app.core.models import QueryInfo


class DatePickerPopup(QDialog):
    """Simple modal popup calendar for choosing a date."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Date")
        layout = QVBoxLayout(self)

        self.calendar = QCalendarWidget(self)
        self.calendar.setSelectedDate(QDate.currentDate())
        layout.addWidget(self.calendar)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_selected_date_str(self) -> str:
        """Return date formatted as YYYY-MM-DD."""
        qdate = self.calendar.selectedDate()
        return qdate.toString("yyyy-MM-dd")


class QueryRunDialog(QDialog):
    """Shows all query parameters with default values and date pickers."""

    def __init__(
        self,
        query_info: QueryInfo,
        initial_defaults: Optional[Dict[str, str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.query_info = query_info
        self.initial_defaults = initial_defaults or {}
        self.param_edits: Dict[str, QLineEdit] = {}

        self.setWindowTitle(f"Run Query - {query_info.name}")
        self.resize(550, 400)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        # Header description
        desc_label = QLabel(
            f"<b>Query:</b> {self.query_info.filename}<br>"
            f"<i>Configure the parameters below before running. Use the date picker button or enter any custom text.</i>"
        )
        desc_label.setWordWrap(True)
        main_layout.addWidget(desc_label)

        # Scroll area for parameters
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        content_widget = QWidget()
        form_layout = QFormLayout(content_widget)

        if not self.query_info.parameters:
            form_layout.addRow(QLabel("<i>No parameters defined in this query.</i>"))
        else:
            for param in self.query_info.parameters:
                pname = param.name
                row_layout = QHBoxLayout()

                # Default value priority: passed in initial_defaults, then param model
                default_val = self.initial_defaults.get(pname, param.default_value)
                edit = QLineEdit(default_val)
                self.param_edits[pname] = edit
                row_layout.addWidget(edit)

                # Date picker button
                date_btn = QPushButton("📅 Pick Date")
                date_btn.setToolTip("Insert selected date in YYYY-MM-DD format")
                date_btn.clicked.connect(self._create_date_picker_callback(edit))
                row_layout.addWidget(date_btn)

                form_layout.addRow(f"{{{pname}}}:", row_layout)

        content_widget.setLayout(form_layout)
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("Run Query")
        self.run_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        self.run_btn.clicked.connect(self.accept)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.run_btn)

        main_layout.addLayout(btn_layout)

    def _create_date_picker_callback(self, target_edit: QLineEdit):
        """Returns a handler to pop up the calendar and populate the target QLineEdit."""
        def handler():
            popup = DatePickerPopup(self)
            if popup.exec() == QDialog.Accepted:
                date_str = popup.get_selected_date_str()
                target_edit.setText(date_str)
        return handler

    def get_parameter_values(self) -> Dict[str, str]:
        """Return user entered key/value pairs for all parameters."""
        return {pname: edit.text().strip() for pname, edit in self.param_edits.items()}

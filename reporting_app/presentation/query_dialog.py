"""Dialog for configuring and running a query with parameters."""

from datetime import date
from typing import Dict, List, Optional
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCalendarWidget,
    QComboBox,
    QDateEdit,
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


from reporting_app.utils.date_calc import DATE_OPTIONS, calculate_date_for_option, is_date_param


class QueryRunDialog(QDialog):
    """Shows all query parameters with default values, date calculation options, and date pickers."""

    # Static dictionary to remember last selected option across dialog openings
    _last_selected_date_options: Dict[str, str] = {}

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
        self.date_combos: Dict[str, QComboBox] = {}
        self.date_pickers: Dict[str, QDateEdit] = {}

        self.setWindowTitle(f"Run Query - {query_info.name}")
        self.resize(580, 420)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        # Header description
        desc_label = QLabel(
            f"<b>Query:</b> {self.query_info.filename}<br>"
            f"<i>Configure the parameters below before running. Date parameters provide quick period calculations and date picking.</i>"
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

                default_val = self.initial_defaults.get(pname, param.default_value)

                if is_date_param(pname):
                    combo = QComboBox(self)
                    for opt in DATE_OPTIONS:
                        combo.addItem(opt)

                    # Remember last selected option or default to "pick date"
                    last_opt = self._last_selected_date_options.get(pname, "pick date")
                    idx = combo.findText(last_opt)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                    else:
                        combo.setCurrentIndex(0)

                    # Hidden or read-only text edit to hold the resulting date
                    edit = QLineEdit(default_val)
                    self.param_edits[pname] = edit
                    self.date_combos[pname] = combo

                    # Date picker widget
                    date_picker = QDateEdit(self)
                    date_picker.setCalendarPopup(True)
                    date_picker.setDisplayFormat("yyyy-MM-dd")

                    # Initialize date_picker with default_val if valid, else today
                    cur_qdate = QDate.fromString(default_val, "yyyy-MM-dd")
                    if cur_qdate.isValid():
                        date_picker.setDate(cur_qdate)
                    else:
                        date_picker.setDate(QDate.currentDate())
                    self.date_pickers[pname] = date_picker

                    def make_combo_handler(p=pname, c=combo, dp=date_picker, ed=edit):
                        def on_option_changed(text):
                            self._last_selected_date_options[p] = text
                            if text == "pick date":
                                dp.setVisible(True)
                                ed.setText(dp.date().toString("yyyy-MM-dd"))
                            else:
                                dp.setVisible(False)
                                calculated = calculate_date_for_option(text)
                                ed.setText(calculated)
                        return on_option_changed

                    def make_date_handler(ed=edit):
                        def on_date_changed(new_date):
                            ed.setText(new_date.toString("yyyy-MM-dd"))
                        return on_date_changed

                    combo.currentTextChanged.connect(make_combo_handler())
                    date_picker.dateChanged.connect(make_date_handler())

                    row_layout.addWidget(combo)
                    row_layout.addWidget(date_picker)

                    # Initial trigger
                    cur_opt = combo.currentText()
                    if cur_opt == "pick date":
                        date_picker.setVisible(True)
                        edit.setText(date_picker.date().toString("yyyy-MM-dd"))
                    else:
                        date_picker.setVisible(False)
                        edit.setText(calculate_date_for_option(cur_opt))

                    form_layout.addRow(f"{{{pname}}}:", row_layout)
                else:
                    edit = QLineEdit(default_val)
                    self.param_edits[pname] = edit
                    row_layout.addWidget(edit)
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

    def get_parameter_values(self) -> Dict[str, str]:
        """Return user entered key/value pairs for all parameters."""
        return {pname: edit.text().strip() for pname, edit in self.param_edits.items()}

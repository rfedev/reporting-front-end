from typing import Dict, List, Optional
from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from reporting_app.utils.date_calc import DATE_OPTIONS, calculate_date_for_option, is_date_param


class FitScrollArea(QScrollArea):
    """Scroll area that adapts its size hint to its contents up to a maximum height."""

    def sizeHint(self) -> QSize:
        if not self.widget():
            return super().sizeHint()
        whint = self.widget().sizeHint()
        w = max(whint.width() + 16, 260)
        h = min(whint.height() + 8, 400)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


class CanvasParameterOverlay(QFrame):
    """Expandable/collapsible overlay displaying Flow Parameters on the flow canvas."""

    parameter_changed = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("canvas_parameter_overlay")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)
        self.setStyleSheet("""
            QFrame#canvas_parameter_overlay {
                background-color: rgba(36, 40, 48, 220);
                border: 1px solid #4a5568;
                border-radius: 6px;
            }
            QLabel {
                color: #e2e8f0;
                font-size: 11px;
            }
            QLineEdit, QComboBox, QDateEdit {
                background-color: #1a202c;
                color: #f7fafc;
                border: 1px solid #4a5568;
                border-radius: 3px;
                padding: 2px 4px;
                font-size: 11px;
            }
            QPushButton#toggle_btn {
                background: transparent;
                color: #cbd5e0;
                border: none;
                font-weight: bold;
                font-size: 12px;
                text-align: left;
                padding: 2px;
            }
            QPushButton#toggle_btn:hover {
                color: #ffffff;
            }
        """)

        self._is_collapsed = False
        self._param_names: List[str] = []
        self._param_values: Dict[str, str] = {}
        self._last_selected_date_options: Dict[str, str] = {}
        self._param_edits: Dict[str, QLineEdit] = {}
        self._date_combos: Dict[str, QComboBox] = {}
        self._date_pickers: Dict[str, QDateEdit] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 4, 6, 6)
        self.main_layout.setSpacing(4)

        # Header bar with toggle arrow and title
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.toggle_btn = QPushButton("▼ Flow Parameters")
        self.toggle_btn.setObjectName("toggle_btn")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle_collapsed)
        header_layout.addWidget(self.toggle_btn)

        header_layout.addStretch()
        self.main_layout.addLayout(header_layout)

        # Container for the parameter form
        self.content_widget = QWidget(self)
        self.form_layout = QFormLayout(self.content_widget)
        self.form_layout.setContentsMargins(2, 2, 2, 2)
        self.form_layout.setSpacing(4)
        self.form_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.scroll_area = FitScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("background: transparent;")
        self.scroll_area.setWidget(self.content_widget)

        self.main_layout.addWidget(self.scroll_area)

    def _update_geometry(self) -> None:
        """Inform the scroll area and overlay layout to update geometry and repaint."""
        if hasattr(self, "content_widget"):
            self.content_widget.adjustSize()
        if hasattr(self, "scroll_area"):
            self.scroll_area.updateGeometry()
        self.main_layout.invalidate()
        self.adjustSize()
        self.raise_()
        self.update()
        self.repaint()

    def toggle_collapsed(self) -> None:
        """Expand or collapse the parameter list."""
        self._is_collapsed = not self._is_collapsed
        self.scroll_area.setVisible(not self._is_collapsed)
        arrow = "▶" if self._is_collapsed else "▼"
        self.toggle_btn.setText(f"{arrow} Flow Parameters ({len(self._param_names)})" if self._is_collapsed else f"{arrow} Flow Parameters")
        self._update_geometry()

    def set_parameters(
        self,
        param_names: List[str],
        current_defaults: Optional[Dict[str, str]] = None,
        date_option_defaults: Optional[Dict[str, str]] = None,
    ) -> None:
        """Populate the parameters in the list without intermediate flicker or collapse."""
        new_names = list(param_names)
        # If parameters haven't changed and no new defaults provided, skip rebuilding
        if new_names == self._param_names and not current_defaults and not date_option_defaults:
            return

        self._param_names = new_names
        if current_defaults:
            self._param_values.update(current_defaults)
        if date_option_defaults:
            self._last_selected_date_options.update(date_option_defaults)

        # Freeze updates while modifying widgets
        self.setUpdatesEnabled(False)
        try:
            # Clear existing rows
            while self.form_layout.count() > 0:
                child = self.form_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
                elif child.layout():
                    while child.layout().count() > 0:
                        subchild = child.layout().takeAt(0)
                        if subchild.widget():
                            subchild.widget().deleteLater()

            self._param_edits.clear()
            self._date_combos.clear()
            self._date_pickers.clear()

            if not self._param_names:
                empty_lbl = QLabel("<i>No parameters used in flow</i>")
                empty_lbl.setStyleSheet("color: #a0aec0;")
                self.form_layout.addRow(empty_lbl)
                self.toggle_btn.setText("▼ Flow Parameters (0)")
                return

            arrow = "▶" if self._is_collapsed else "▼"
            self.toggle_btn.setText(f"{arrow} Flow Parameters ({len(self._param_names)})" if self._is_collapsed else f"{arrow} Flow Parameters")

            for pname in self._param_names:
                lbl = QLabel(f"{pname}:")
                lbl.setStyleSheet("font-weight: bold; color: #cbd5e0;")

                init_val = self._param_values.get(pname, "")

                if is_date_param(pname):
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(3)

                    combo = QComboBox(row_widget)
                    for opt in DATE_OPTIONS:
                        combo.addItem(opt)

                    saved_opt = self._last_selected_date_options.get(pname, "pick date")
                    idx = combo.findText(saved_opt)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                    else:
                        combo.setCurrentIndex(0)

                    date_picker = QDateEdit(row_widget)
                    date_picker.setCalendarPopup(True)
                    date_picker.setDisplayFormat("yyyy-MM-dd")

                    parsed_date = QDate.fromString(init_val, "yyyy-MM-dd")
                    if parsed_date.isValid():
                        date_picker.setDate(parsed_date)
                    else:
                        date_picker.setDate(QDate.currentDate())

                    self._date_combos[pname] = combo
                    self._date_pickers[pname] = date_picker

                    # Value holder
                    edit = QLineEdit(init_val, row_widget)
                    edit.setVisible(False)
                    self._param_edits[pname] = edit

                    def make_combo_handler(p=pname, c=combo, dp=date_picker, ed=edit):
                        def on_opt_changed(text):
                            self._last_selected_date_options[p] = text
                            if text == "pick date":
                                dp.setVisible(True)
                                val = dp.date().toString("yyyy-MM-dd")
                                ed.setText(val)
                                self._param_values[p] = val
                                self._update_geometry()
                                self.parameter_changed.emit(p, val)
                            else:
                                dp.setVisible(False)
                                val = calculate_date_for_option(text)
                                ed.setText(val)
                                self._param_values[p] = val
                                self._update_geometry()
                                self.parameter_changed.emit(p, val)
                        return on_opt_changed

                    def make_dp_handler(p=pname, ed=edit):
                        def on_date_changed(new_date):
                            val = new_date.toString("yyyy-MM-dd")
                            ed.setText(val)
                            self._param_values[p] = val
                            self.parameter_changed.emit(p, val)
                        return on_date_changed

                    combo.currentTextChanged.connect(make_combo_handler())
                    date_picker.dateChanged.connect(make_dp_handler())

                    row_layout.addWidget(combo)
                    row_layout.addWidget(date_picker)

                    # Initialize state
                    cur_opt = combo.currentText()
                    if cur_opt == "pick date":
                        date_picker.setVisible(True)
                        val = date_picker.date().toString("yyyy-MM-dd")
                        edit.setText(val)
                        self._param_values[pname] = val
                    else:
                        date_picker.setVisible(False)
                        val = calculate_date_for_option(cur_opt)
                        edit.setText(val)
                        self._param_values[pname] = val

                    self.form_layout.addRow(lbl, row_widget)
                else:
                    edit = QLineEdit(init_val)
                    self._param_edits[pname] = edit

                    def make_edit_handler(p=pname, ed=edit):
                        def on_text_changed(val):
                            self._param_values[p] = val
                            self.parameter_changed.emit(p, val)
                        return on_text_changed

                    edit.textChanged.connect(make_edit_handler())
                    self.form_layout.addRow(lbl, edit)
        finally:
            self._update_geometry()
            self.setUpdatesEnabled(True)

    def get_parameter_values(self) -> Dict[str, str]:
        """Return currently entered values for all parameters."""
        res: Dict[str, str] = {}
        for pname in self._param_names:
            if pname in self._param_edits:
                res[pname] = self._param_edits[pname].text().strip()
            elif pname in self._param_values:
                res[pname] = self._param_values[pname].strip()
        return res

    def get_date_option_selections(self) -> Dict[str, str]:
        """Return selected option names for date parameters."""
        return dict(self._last_selected_date_options)

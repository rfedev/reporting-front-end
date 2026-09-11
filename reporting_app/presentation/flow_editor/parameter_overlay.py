"""Canvas Parameter Overlay widget for process flow editor."""

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
    QVBoxLayout,
    QWidget,
)

from reporting_app.utils.date_calc import DATE_OPTIONS, calculate_date_for_option, is_date_param


class CanvasParameterOverlay(QFrame):
    """Expandable/collapsible overlay displaying Flow Parameters cleanly over the flow canvas."""

    parameter_changed = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("canvas_parameter_overlay")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame#canvas_parameter_overlay {
                background-color: rgba(30, 34, 42, 235);
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
        self.main_layout.setContentsMargins(8, 6, 8, 8)
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

        # Content container
        self.content_widget = QWidget(self)
        self.form_layout = QFormLayout(self.content_widget)
        self.form_layout.setContentsMargins(0, 4, 0, 4)
        self.form_layout.setSpacing(6)
        self.form_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.main_layout.addWidget(self.content_widget)

    def _update_overlay_size(self) -> None:
        """Resize the overlay to fit content cleanly without empty space or clipping."""
        self.content_widget.setVisible(not self._is_collapsed)
        if not self._is_collapsed:
            for i in range(self.form_layout.count()):
                item = self.form_layout.itemAt(i)
                if item and item.widget():
                    item.widget().show()
        self.content_widget.adjustSize()
        hint = self.layout().sizeHint()
        self.resize(hint)
        self.raise_()

    def showEvent(self, event) -> None:
        """Ensure overlay recalculates its snug size when parent or window is displayed."""
        super().showEvent(event)
        self._update_overlay_size()

    def toggle_collapsed(self) -> None:
        """Expand or collapse the parameter list."""
        self._is_collapsed = not self._is_collapsed
        arrow = "▶" if self._is_collapsed else "▼"
        count = len(self._param_names)
        self.toggle_btn.setText(
            f"{arrow} Flow Parameters ({count})" if self._is_collapsed else f"{arrow} Flow Parameters"
        )
        self._update_overlay_size()

    def set_parameters(
        self,
        param_names: List[str],
        current_defaults: Optional[Dict[str, str]] = None,
        date_option_defaults: Optional[Dict[str, str]] = None,
    ) -> None:
        """Populate the parameters in the list cleanly with exact sizing and state preservation."""
        new_names = list(param_names)

        # Preserve current values from UI
        for pname, edit in self._param_edits.items():
            txt = edit.text().strip()
            if txt:
                self._param_values[pname] = txt
        for pname, combo in self._date_combos.items():
            self._last_selected_date_options[pname] = combo.currentText()
            if combo.currentText() == "pick date" and pname in self._date_pickers:
                self._param_values[pname] = self._date_pickers[pname].date().toString("yyyy-MM-dd")

        if current_defaults:
            self._param_values.update(current_defaults)
        if date_option_defaults:
            self._last_selected_date_options.update(date_option_defaults)

        # Only rebuild widgets if parameter names on canvas have actually changed
        if self._param_names == new_names and (self._param_edits or self._date_combos or not new_names):
            return

        self._param_names = new_names

        # Completely remove existing widgets from layout immediately
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
                w.hide()

        self._param_edits.clear()
        self._date_combos.clear()
        self._date_pickers.clear()

        arrow = "▶" if self._is_collapsed else "▼"
        count = len(self._param_names)
        self.toggle_btn.setText(
            f"{arrow} Flow Parameters ({count})" if self._is_collapsed else f"{arrow} Flow Parameters"
        )

        if not self._param_names:
            empty_lbl = QLabel("<i>No parameters used in flow</i>")
            empty_lbl.setStyleSheet("color: #a0aec0; padding: 4px;")
            self.form_layout.addRow(empty_lbl)
            self._update_overlay_size()
            return

        for pname in self._param_names:
            lbl = QLabel(f"{pname}:", self.content_widget)
            lbl.setStyleSheet("font-weight: bold; color: #cbd5e0;")
            init_val = self._param_values.get(pname, "")

            if is_date_param(pname):
                row_widget = QWidget(self.content_widget)
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(4)

                combo = QComboBox(row_widget)
                combo.setMinimumWidth(105)
                for opt in DATE_OPTIONS:
                    combo.addItem(opt)

                saved_opt = self._last_selected_date_options.get(pname, "pick date")
                idx = combo.findText(saved_opt)
                combo.setCurrentIndex(idx if idx >= 0 else 0)

                date_picker = QDateEdit(row_widget)
                date_picker.setMinimumWidth(105)
                date_picker.setCalendarPopup(True)
                date_picker.setDisplayFormat("yyyy-MM-dd")

                parsed_date = QDate.fromString(init_val, "yyyy-MM-dd")
                date_picker.setDate(parsed_date if parsed_date.isValid() else QDate.currentDate())

                self._date_combos[pname] = combo
                self._date_pickers[pname] = date_picker

                def make_combo_handler(p=pname, c=combo, dp=date_picker):
                    def on_opt_changed(text):
                        self._last_selected_date_options[p] = text
                        if text == "pick date":
                            dp.setVisible(True)
                            val = dp.date().toString("yyyy-MM-dd")
                        else:
                            dp.setVisible(False)
                            val = calculate_date_for_option(text)
                        self._param_values[p] = val
                        self._update_overlay_size()
                        self.parameter_changed.emit(p, val)
                    return on_opt_changed

                def make_dp_handler(p=pname):
                    def on_date_changed(new_date):
                        val = new_date.toString("yyyy-MM-dd")
                        self._param_values[p] = val
                        self.parameter_changed.emit(p, val)
                    return on_date_changed

                combo.currentTextChanged.connect(make_combo_handler())
                date_picker.dateChanged.connect(make_dp_handler())

                row_layout.addWidget(combo)
                row_layout.addWidget(date_picker)

                cur_opt = combo.currentText()
                if cur_opt == "pick date":
                    date_picker.setVisible(True)
                    val = date_picker.date().toString("yyyy-MM-dd")
                else:
                    date_picker.setVisible(False)
                    val = calculate_date_for_option(cur_opt)
                self._param_values[pname] = val

                self.form_layout.addRow(lbl, row_widget)
            else:
                edit = QLineEdit(init_val, self.content_widget)
                edit.setMinimumWidth(130)
                self._param_edits[pname] = edit

                def make_edit_handler(p=pname):
                    def on_text_changed(val):
                        self._param_values[p] = val
                        self.parameter_changed.emit(p, val)
                    return on_text_changed

                edit.textChanged.connect(make_edit_handler())
                self.form_layout.addRow(lbl, edit)

        self._update_overlay_size()

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


"""Custom hierarchical preset date picker widget with submenus and hover preview."""

from datetime import date
from typing import Optional, List, Callable

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtWidgets import (
    QPushButton,
    QMenu,
    QWidgetAction,
    QLabel,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QCalendarWidget,
    QDialogButtonBox,
    QWidget,
)

from reporting_app.utils.date_calc import (
    DAYS_OF_WEEK,
    MONTH_ITEMS,
    QUARTER_ITEMS,
    evaluate_date_preset,
)


class DatePresetPicker(QPushButton):
    """A dropdown button displaying preset dates with nested submenus and live preview.

    Hierarchy:
      - (Optional) Flow Date
      - (Optional) None
      - Pick Date (Triggers modal calendar dialog)
      - Separator
      - Current -> Week -> [Mon..Sun]
                -> Month -> [1st of Month, 1st Workday, Last Workday, Month End]
                -> Quarter -> [1st of Qtr, 1st Workday, Last Workday, Quarter End]
      - Previous -> Week -> [Mon..Sun]
                 -> Month -> [1st of Month, 1st Workday, Last Workday, Month End]
                 -> Quarter -> [1st of Qtr, 1st Workday, Last Workday, Quarter End]
      - Separator
      - Preview Footer Label: Shows evaluated date of hovered item or currently active preset
    """

    preset_changed = Signal(str)      # Emitted with the selected preset string
    date_evaluated = Signal(str)      # Emitted with the evaluated YYYY-MM-DD date string

    def __init__(
        self,
        initial_preset: str = "Pick Date",
        include_flow_date: bool = False,
        include_none: bool = False,
        flow_date: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.include_flow_date = include_flow_date
        self.include_none = include_none
        self._flow_date = flow_date or ""
        self._picked_date = date.today().strftime("%Y-%m-%d")

        self.setObjectName("date_preset_picker")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton#date_preset_picker {"
            "  text-align: left;"
            "  padding-left: 10px;"
            "  padding-right: 20px;"
            "  padding-top: 3px;"
            "  padding-bottom: 3px;"
            "  border: 1px solid #4a5568;"
            "  border-radius: 3px;"
            "  background-color: #1a202c;"
            "  color: #f7fafc;"
            "  font-size: 11px;"
            "}"
            "QPushButton#date_preset_picker:hover { background-color: #2d3748; }"
        )

        self._menu = QMenu(self)
        self._menu.setStyleSheet(
            "QMenu {"
            "  background-color: #1f2937;"
            "  color: #f3f4f6;"
            "  border: 1px solid #4b5563;"
            "  padding: 4px;"
            "}"
            "QMenu::item {"
            "  padding: 5px 24px 5px 12px;"
            "  border-radius: 3px;"
            "}"
            "QMenu::item:selected { background-color: #3b82f6; color: #ffffff; }"
            "QMenu::separator { height: 1px; background: #374151; margin: 4px 6px; }"
        )

        self._preview_lbl = QLabel(self)
        self._preview_lbl.setStyleSheet(
            "color: #9ca3af; font-size: 11px; font-style: italic; padding: 4px 10px; background: #111827;"
        )
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_action = QWidgetAction(self._menu)
        self._preview_action.setDefaultWidget(self._preview_lbl)
        self._preview_action.setEnabled(False)

        self._action_preset_map = {}
        self._current_preset = "Pick Date"
        self._build_menu()
        self.setMenu(self._menu)

        self.set_preset(initial_preset)

    def set_flow_date(self, flow_date: str) -> None:
        """Update flow_date context for 'Flow Date' evaluation."""
        self._flow_date = flow_date
        self._update_preview()

    def set_picked_date(self, picked_date: str) -> None:
        """Update the custom picked date value."""
        self._picked_date = picked_date
        if self._current_preset.lower() == "pick date":
            self.date_evaluated.emit(self._picked_date)

    def get_picked_date(self) -> str:
        return self._picked_date

    def get_preset(self) -> str:
        return self._current_preset

    def currentText(self) -> str:
        """Compatibility helper returning current preset."""
        return self._current_preset

    def evaluate_current_date(self, ref_date: Optional[date] = None) -> str:
        """Calculate the current preset's date string."""
        if self._current_preset.lower() == "pick date":
            return self._picked_date
        return evaluate_date_preset(self._current_preset, ref_date=ref_date, flow_date=self._flow_date)

    def set_preset(self, preset: Optional[str]) -> None:
        """Select a preset and update button label and evaluations."""
        raw = (preset or "Pick Date").strip()
        # Verify valid preset or default to Pick Date
        if self.include_flow_date and raw.lower() == "flow date":
            self._current_preset = "Flow Date"
        elif self.include_none and raw.lower() == "none":
            self._current_preset = "None"
        elif raw.lower() in ("pick date", "pick_date"):
            self._current_preset = "Pick Date"
        elif raw in self._action_preset_map.values():
            self._current_preset = raw
        else:
            # Check case-insensitive match
            matched = next((p for p in self._action_preset_map.values() if p.lower() == raw.lower()), None)
            self._current_preset = matched if matched else "Pick Date"

        self.setText(self._current_preset)
        self._update_preview()
        evaluated = self.evaluate_current_date()
        self.preset_changed.emit(self._current_preset)
        self.date_evaluated.emit(evaluated)

    def _build_menu(self) -> None:
        self._menu.clear()
        self._action_preset_map.clear()

        # Connect hover tracking across menu
        self._menu.hovered.connect(self._on_action_hovered)
        self._menu.aboutToShow.connect(self._update_preview)

        # 1. Top extras if enabled
        if self.include_flow_date:
            act = self._menu.addAction("Flow Date")
            act.triggered.connect(lambda: self.set_preset("Flow Date"))
            self._action_preset_map[act] = "Flow Date"

        if self.include_none:
            act = self._menu.addAction("None")
            act.triggered.connect(lambda: self.set_preset("None"))
            self._action_preset_map[act] = "None"

        # 2. Pick Date
        pick_act = self._menu.addAction("Pick Date")
        pick_act.triggered.connect(self._on_pick_date_triggered)
        self._action_preset_map[pick_act] = "Pick Date"

        self._menu.addSeparator()

        # 3. Current -> Week, Month, Quarter
        cur_menu = self._menu.addMenu("Current")
        self._populate_cadence_menu(cur_menu, "Current")

        # 4. Previous -> Week, Month, Quarter
        prev_menu = self._menu.addMenu("Previous")
        self._populate_cadence_menu(prev_menu, "Previous")

        self._menu.addSeparator()

        # 5. Bottom Preview Footer
        self._menu.addAction(self._preview_action)

    def _populate_cadence_menu(self, parent_menu: QMenu, period_type: str) -> None:
        parent_menu.hovered.connect(self._on_action_hovered)

        # Today (for Current menu)
        if period_type == "Current":
            today_preset = "Current • Today"
            today_act = parent_menu.addAction("Today")
            today_act.triggered.connect(lambda checked=False, p=today_preset: self.set_preset(p))
            self._action_preset_map[today_act] = today_preset
            parent_menu.addSeparator()

        # Week
        week_menu = parent_menu.addMenu("Week")
        week_menu.hovered.connect(self._on_action_hovered)
        for d in DAYS_OF_WEEK:
            preset_str = f"{period_type} • Week • {d}"
            act = week_menu.addAction(d)
            act.triggered.connect(lambda checked=False, p=preset_str: self.set_preset(p))
            self._action_preset_map[act] = preset_str

        # Month
        month_menu = parent_menu.addMenu("Month")
        month_menu.hovered.connect(self._on_action_hovered)
        for m in MONTH_ITEMS:
            preset_str = f"{period_type} • Month • {m}"
            act = month_menu.addAction(m)
            act.triggered.connect(lambda checked=False, p=preset_str: self.set_preset(p))
            self._action_preset_map[act] = preset_str

        # Quarter
        quarter_menu = parent_menu.addMenu("Quarter")
        quarter_menu.hovered.connect(self._on_action_hovered)
        for q in QUARTER_ITEMS:
            preset_str = f"{period_type} • Quarter • {q}"
            act = quarter_menu.addAction(q)
            act.triggered.connect(lambda checked=False, p=preset_str: self.set_preset(p))
            self._action_preset_map[act] = preset_str

    def _on_action_hovered(self, action) -> None:
        if action in self._action_preset_map:
            p_str = self._action_preset_map[action]
            eval_date = self._evaluate_for_preview(p_str)
            self._preview_lbl.setText(eval_date)
        else:
            self._update_preview()

    def _evaluate_for_preview(self, preset_str: str) -> str:
        if preset_str == "Pick Date":
            return self._picked_date
        return evaluate_date_preset(preset_str, flow_date=self._flow_date)

    def _update_preview(self) -> None:
        eval_date = self.evaluate_current_date()
        self._preview_lbl.setText(eval_date)

    def _on_pick_date_triggered(self) -> None:
        # Open modal calendar dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Pick Date")
        dlg.setModal(True)
        vbox = QVBoxLayout(dlg)

        calendar = QCalendarWidget(dlg)
        calendar.setGridVisible(True)
        cur_qd = QDate.fromString(self._picked_date, "yyyy-MM-dd")
        if cur_qd.isValid():
            calendar.setSelectedDate(cur_qd)
        vbox.addWidget(calendar)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        vbox.addWidget(buttons)

        if dlg.exec() == QDialog.Accepted:
            chosen = calendar.selectedDate().toString("yyyy-MM-dd")
            self._picked_date = chosen
            self.set_preset("Pick Date")
        else:
            self.set_preset("Pick Date")

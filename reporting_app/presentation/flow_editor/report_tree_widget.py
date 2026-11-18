"""Left panel widget for browsing Reports and Process Flows in a tree hierarchy."""

from typing import Dict, Iterable, List, Optional, Set, Tuple
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from reporting_app.core.models import Report


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


_INLINE_BTN_STYLE = """
QPushButton {
    background: transparent;
    border: none;
    border-radius: 3px;
    color: #c8ccd4;
    font-weight: bold;
    padding: 0px;
}
QPushButton:hover {
    background-color: rgba(255, 255, 255, 0.12);
    color: #ffffff;
}
"""


class _NoTextTreeItemDelegate(QStyledItemDelegate):
    """Item delegate that suppresses default QTreeWidgetItem text painting behind custom row widgets."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""


class ReportFlowTreeWidget(QTreeWidget):
    """Tree widget where clicking a Report parent row toggles expansion without changing selection."""

    report_row_clicked = Signal(QTreeWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._item_delegate = _NoTextTreeItemDelegate(self)
        self.setItemDelegate(self._item_delegate)

    def _is_report_item(self, item: Optional[QTreeWidgetItem]) -> bool:
        if item is None:
            return False
        data = item.data(0, Qt.UserRole)
        return isinstance(data, tuple) and len(data) >= 1 and data[0] == "report"

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if self._is_report_item(item):
            if event.button() == Qt.LeftButton:
                self.report_row_clicked.emit(item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.pos())
        if self._is_report_item(item):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if self._is_report_item(item):
            if event.button() == Qt.LeftButton:
                self.report_row_clicked.emit(item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ReportRowWidget(QWidget):
    """Custom widget for top-level Report items with chevron, optional pin badge, '+' add flow, and '⋯' menu."""

    def __init__(
        self,
        report_key: str,
        expanded: bool,
        on_add_flow,
        on_menu,
        is_pinned: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.report_key = report_key
        self.is_pinned = bool(is_pinned)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self.chevron_lbl = QLabel("▼" if expanded else "▶", self)
        self.chevron_lbl.setFixedWidth(14)
        self.chevron_lbl.setAlignment(Qt.AlignCenter)
        self.chevron_lbl.setStyleSheet("color: #9ca3af; font-size: 10px; background: transparent;")
        self.chevron_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.chevron_lbl)

        self.pin_lbl = QLabel("📌", self)
        self.pin_lbl.setFixedWidth(14)
        self.pin_lbl.setAlignment(Qt.AlignCenter)
        self.pin_lbl.setStyleSheet("font-size: 11px; background: transparent;")
        self.pin_lbl.setToolTip("Pinned Report")
        self.pin_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.pin_lbl.setVisible(self.is_pinned)
        layout.addWidget(self.pin_lbl)

        self.name_lbl = QLabel(report_key, self)
        self.name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name_lbl.setToolTip(report_key)
        self.name_lbl.setStyleSheet("font-weight: bold; color: #e5e7eb; background: transparent;")
        self.name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.name_lbl, 1)

        self.add_flow_btn = QPushButton("+", self)
        self.add_flow_btn.setFixedSize(22, 22)
        self.add_flow_btn.setToolTip(f"Add Process Flow to '{report_key}'")
        self.add_flow_btn.setStyleSheet(_INLINE_BTN_STYLE)
        self.add_flow_btn.clicked.connect(lambda: on_add_flow(self.report_key))
        layout.addWidget(self.add_flow_btn)

        self.menu_btn = QPushButton("⋯", self)
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setToolTip("Report Options (Pin / Rename / Remove)")
        self.menu_btn.setStyleSheet(_INLINE_BTN_STYLE)
        self.menu_btn.clicked.connect(
            lambda: on_menu(self.report_key, self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height())))
        )
        layout.addWidget(self.menu_btn)

    def set_expanded_indicator(self, expanded: bool) -> None:
        self.chevron_lbl.setText("▼" if expanded else "▶")

    def set_pinned_indicator(self, is_pinned: bool) -> None:
        self.is_pinned = bool(is_pinned)
        self.pin_lbl.setVisible(self.is_pinned)


class FlowRowWidget(QWidget):
    """Custom widget for child Process Flow items with label and right-aligned '⋯' menu."""

    def __init__(self, report_key: str, flow_name: str, is_active: bool, on_menu, parent=None):
        super().__init__(parent)
        self.report_key = report_key
        self.flow_name = flow_name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(4)

        self.name_lbl = QLabel(flow_name, self)
        self.name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name_lbl.setToolTip(flow_name)
        self.name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.set_active(is_active)
        layout.addWidget(self.name_lbl, 1)

        self.menu_btn = QPushButton("⋯", self)
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setToolTip("Process Flow Options (Rename / Remove)")
        self.menu_btn.setStyleSheet(_INLINE_BTN_STYLE)
        self.menu_btn.clicked.connect(
            lambda: on_menu(
                self.report_key,
                self.flow_name,
                self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height())),
            )
        )
        layout.addWidget(self.menu_btn)

    def set_active(self, is_active: bool) -> None:
        if is_active:
            self.name_lbl.setStyleSheet("font-weight: bold; color: #60a5fa; background: transparent;")
        else:
            self.name_lbl.setStyleSheet("font-weight: normal; color: #d1d5db; background: transparent;")


class ReportFlowTreePanel(QWidget):
    """Left collapsible panel displaying Reports and Process Flows in an interactive tree."""

    EXPANDED_MIN_WIDTH = 180
    COLLAPSED_WIDTH = 32
    DEFAULT_EXPANDED_WIDTH = 250

    process_flow_selected = Signal(str, str)  # (report_key, flow_name)
    add_report_requested = Signal()
    rename_report_requested = Signal(str)  # report_key
    remove_report_requested = Signal(str)  # report_key
    add_flow_requested = Signal(str)  # report_key
    rename_flow_requested = Signal(str, str)  # (report_key, flow_name)
    remove_flow_requested = Signal(str, str)  # (report_key, flow_name)
    auth_requested = Signal()
    settings_requested = Signal()
    sync_requested = Signal()
    collapse_toggled = Signal(bool)  # is_collapsed
    pinned_reports_changed = Signal(list)  # List[str] of pinned report keys

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_collapsed = False
        self._expanded_report_keys: Set[str] = set()
        self._pinned_reports: List[str] = []
        self._all_reports_by_key: Dict[str, Report] = {}
        self._active_report_key: Optional[str] = None
        self._active_flow_name: Optional[str] = None
        self._flow_items_by_key: Dict[Tuple[str, str], QTreeWidgetItem] = {}
        self._current_tree_signature: Optional[List[Tuple[str, bool, Tuple[str, ...]]]] = None

        self.setMinimumWidth(self.EXPANDED_MIN_WIDTH)
        self._build_ui()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ---------------------------------------------------------------------
        # Expanded Container
        # ---------------------------------------------------------------------
        self.expanded_widget = QWidget(self)
        exp_layout = QVBoxLayout(self.expanded_widget)
        exp_layout.setContentsMargins(6, 6, 6, 6)
        exp_layout.setSpacing(4)

        # Row 1: Active Report Title (left) + '<|' Collapse Button (right)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(2, 0, 0, 2)
        header_row.setSpacing(4)

        self.report_title_label = QLabel("No Report Selected", self.expanded_widget)
        self.report_title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.report_title_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #f3f4f6;")
        header_row.addWidget(self.report_title_label, 1)

        self.collapse_btn = QPushButton("<|", self.expanded_widget)
        self.collapse_btn.setFixedSize(28, 26)
        self.collapse_btn.setToolTip("Collapse Reports Panel")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(True, emit_signal=True))
        header_row.addWidget(self.collapse_btn)
        exp_layout.addLayout(header_row)

        sep1 = QFrame(self.expanded_widget)
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFrameShadow(QFrame.Sunken)
        sep1.setStyleSheet("color: #374151;")
        exp_layout.addWidget(sep1)

        # Row 2: 'Reports' header (left) + Search Box + '+' Add Report button (right)
        reports_header = QHBoxLayout()
        reports_header.setContentsMargins(2, 2, 0, 2)
        reports_header.setSpacing(4)

        self.reports_header_label = QLabel("<b>Reports</b>", self.expanded_widget)
        self.reports_header_label.setStyleSheet("color: #d1d5db; font-size: 12px;")
        reports_header.addWidget(self.reports_header_label)

        self.search_edit = QLineEdit(self.expanded_widget)
        self.search_edit.setPlaceholderText("Search reports...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedHeight(24)
        self.search_edit.setStyleSheet(
            "QLineEdit { background-color: #16181d; border: 1px solid #374151; "
            "border-radius: 3px; padding: 1px 6px; color: #e5e7eb; font-size: 11px; }"
            "QLineEdit:focus { border: 1px solid #3b82f6; }"
        )
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        reports_header.addWidget(self.search_edit, 1)

        self.add_report_btn = QPushButton("+", self.expanded_widget)
        self.add_report_btn.setFixedSize(24, 24)
        self.add_report_btn.setToolTip("Add Report")
        self.add_report_btn.setStyleSheet("QPushButton { font-weight: bold; font-size: 14px; padding: 0px; }")
        self.add_report_btn.clicked.connect(self.add_report_requested.emit)
        reports_header.addWidget(self.add_report_btn)
        exp_layout.addLayout(reports_header)

        # Tree Widget: Reports -> Process Flows
        self.tree = ReportFlowTreeWidget(self.expanded_widget)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(16)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.setStyleSheet(
            "QTreeWidget { border: 1px solid #2d3138; background-color: #1c1e22; outline: 0; }"
            "QTreeWidget::item { min-height: 26px; }"
            "QTreeWidget::item:selected { background-color: rgba(43, 120, 228, 0.28); }"
        )
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.report_row_clicked.connect(lambda item: self._on_item_clicked(item, 0))
        self.tree.itemExpanded.connect(self._on_item_expanded_state_changed)
        self.tree.itemCollapsed.connect(self._on_item_expanded_state_changed)
        exp_layout.addWidget(self.tree, 1)

        # Bottom Controls Bar (Icon-only buttons)
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 4, 0, 0)
        bottom_bar.setSpacing(6)

        self.auth_btn = QPushButton("🔑", self.expanded_widget)
        self.auth_btn.setFixedSize(28, 28)
        self.auth_btn.setStyleSheet("QPushButton { font-size: 14px; padding: 0px; }")
        self.auth_btn.setToolTip("Run 'gcloud auth application-default login' in a terminal")
        self.auth_btn.clicked.connect(self.auth_requested.emit)
        bottom_bar.addWidget(self.auth_btn)

        self.settings_btn = QPushButton("⚙", self.expanded_widget)
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.setStyleSheet("QPushButton { font-size: 15px; padding: 0px; }")
        self.settings_btn.setToolTip("Configure working directory and auto-scan")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        bottom_bar.addWidget(self.settings_btn)

        self.sync_btn = QPushButton("🔄", self.expanded_widget)
        self.sync_btn.setFixedSize(28, 28)
        self.sync_btn.setStyleSheet("QPushButton { font-size: 14px; padding: 0px; }")
        self.sync_btn.setToolTip("Manually scan for reports, process flows, and queries")
        self.sync_btn.clicked.connect(self.sync_requested.emit)
        bottom_bar.addWidget(self.sync_btn)

        bottom_bar.addStretch()
        exp_layout.addLayout(bottom_bar)

        outer_layout.addWidget(self.expanded_widget)

        # ---------------------------------------------------------------------
        # Collapsed Rail Container
        # ---------------------------------------------------------------------
        self.collapsed_widget = QWidget(self)
        col_layout = QVBoxLayout(self.collapsed_widget)
        col_layout.setContentsMargins(2, 6, 2, 6)
        col_layout.setSpacing(4)

        self.expand_btn = QPushButton("|>", self.collapsed_widget)
        self.expand_btn.setFixedSize(28, 26)
        self.expand_btn.setToolTip("Expand Reports Panel")
        self.expand_btn.clicked.connect(lambda: self.set_collapsed(False, emit_signal=True))
        col_layout.addWidget(self.expand_btn, 0, Qt.AlignHCenter)
        col_layout.addStretch()

        self.collapsed_widget.setVisible(False)
        outer_layout.addWidget(self.collapsed_widget)

    def set_collapsed(self, collapsed: bool, emit_signal: bool = False) -> None:
        """Collapse panel to a 32px rail or restore expanded view."""
        self._is_collapsed = bool(collapsed)
        self.expanded_widget.setVisible(not self._is_collapsed)
        self.collapsed_widget.setVisible(self._is_collapsed)

        if self._is_collapsed:
            self.setMinimumWidth(self.COLLAPSED_WIDTH)
            self.setMaximumWidth(self.COLLAPSED_WIDTH)
        else:
            self.setMinimumWidth(self.EXPANDED_MIN_WIDTH)
            self.setMaximumWidth(16777215)

        if emit_signal:
            self.collapse_toggled.emit(self._is_collapsed)

    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def set_active_report_title(self, title: str) -> None:
        display = title.strip() if title and title.strip() else "No Report Selected"
        self.report_title_label.setText(display)
        self.report_title_label.setToolTip(display)

    def set_sync_visible(self, visible: bool) -> None:
        self.sync_btn.setVisible(bool(visible))

    def set_pinned_reports(self, pinned: Iterable[str]) -> None:
        """Set the list of pinned report keys without emitting pinned_reports_changed."""
        seen: Set[str] = set()
        ordered: List[str] = []
        for k in pinned:
            if k and k not in seen:
                seen.add(k)
                ordered.append(k)
        self._pinned_reports = ordered
        if self._all_reports_by_key:
            self._rebuild_tree()

    def get_pinned_reports(self) -> List[str]:
        return list(self._pinned_reports)

    def is_report_pinned(self, report_key: str) -> bool:
        if report_key in self._pinned_reports:
            return True
        rep = self._all_reports_by_key.get(report_key)
        if rep and rep.name in self._pinned_reports:
            return True
        return False

    def toggle_pin_report(self, report_key: str) -> None:
        """Pin or unpin a report and refresh the tree ordering."""
        rep = self._all_reports_by_key.get(report_key)
        if self.is_report_pinned(report_key):
            self._pinned_reports = [
                k for k in self._pinned_reports
                if k != report_key and (not rep or k != rep.name)
            ]
        else:
            self._pinned_reports.append(report_key)
        self.pinned_reports_changed.emit(list(self._pinned_reports))
        self._rebuild_tree()

    def _on_search_text_changed(self, _text: str) -> None:
        """Re-filter and re-order the reports tree as the user types in the search box."""
        self._rebuild_tree()

    def _get_filtered_ordered_reports(self) -> List[Tuple[str, Report]]:
        """Filter reports by report name only and order by prefix match / pinned status."""
        query = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        items = list(self._all_reports_by_key.items())

        if not query:
            # No search text: pinned reports at the top (in original/pin order), then unpinned reports
            return sorted(
                items,
                key=lambda pair: (0 if self.is_report_pinned(pair[0]) else 1),
            )

        # Filter by report name only (not process flow names)
        matched: List[Tuple[Tuple[int, int, int, str, int], str, Report]] = []
        for idx, (rk, rep) in enumerate(items):
            rep_name_lower = rep.name.lower()
            rk_lower = rk.lower()
            pos_in_name = rep_name_lower.find(query)
            pos_in_key = rk_lower.find(query)
            if pos_in_name < 0 and pos_in_key < 0:
                continue

            starts_with = rep_name_lower.startswith(query) or rk_lower.startswith(query)
            match_pos = pos_in_name if pos_in_name >= 0 else pos_in_key
            is_pinned = self.is_report_pinned(rk)
            sort_key = (
                0 if starts_with else 1,
                match_pos,
                0 if is_pinned else 1,
                rep_name_lower,
                idx,
            )
            matched.append((sort_key, rk, rep))

        matched.sort(key=lambda entry: entry[0])
        return [(rk, rep) for _, rk, rep in matched]

    def clear_items(self) -> None:
        """Clear all tree items and release cached item references cleanly before teardown."""
        self._flow_items_by_key.clear()
        self._current_tree_signature = None
        self.tree.blockSignals(True)
        for idx in range(self.tree.topLevelItemCount()):
            top_item = self.tree.topLevelItem(idx)
            if top_item:
                for c_idx in range(top_item.childCount()):
                    c_item = top_item.child(c_idx)
                    if c_item:
                        self.tree.removeItemWidget(c_item, 0)
                self.tree.removeItemWidget(top_item, 0)
        self.tree.clear()
        self.tree.blockSignals(False)

    def set_reports_tree(
        self,
        reports_by_key: Dict[str, Report],
        active_report_key: Optional[str] = None,
        active_flow_name: Optional[str] = None,
    ) -> None:
        """Populate the Reports -> Process Flows tree while preserving expansion states."""
        self._all_reports_by_key = dict(reports_by_key)
        self._active_report_key = active_report_key
        self._active_flow_name = active_flow_name
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Rebuild or update the filtered & sorted Reports -> Process Flows tree."""
        reports_by_key = self._all_reports_by_key
        active_report_key = self._active_report_key
        active_flow_name = self._active_flow_name
        ordered_reports = self._get_filtered_ordered_reports()

        new_sig = [
            (rk, self.is_report_pinned(rk), tuple(pf.name for pf in rep.process_flows))
            for rk, rep in ordered_reports
        ]
        if (
            self._current_tree_signature == new_sig
            and self.tree.topLevelItemCount() == len(ordered_reports)
        ):
            rep_title = (
                reports_by_key[active_report_key].name
                if (active_report_key and active_report_key in reports_by_key)
                else (active_report_key or "No Report Selected")
            )
            self.set_active_selection(active_report_key, active_flow_name, report_title=rep_title)
            return

        # Preserve current expanded report keys before clearing
        for idx in range(self.tree.topLevelItemCount()):
            top_item = self.tree.topLevelItem(idx)
            data = top_item.data(0, Qt.UserRole)
            if isinstance(data, tuple) and len(data) >= 2 and data[0] == "report":
                if top_item.isExpanded():
                    self._expanded_report_keys.add(data[1])
                else:
                    self._expanded_report_keys.discard(data[1])

        if active_report_key:
            self._expanded_report_keys.add(active_report_key)

        self.clear_items()
        self._current_tree_signature = new_sig

        self.tree.blockSignals(True)

        for report_key, report in ordered_reports:
            is_expanded = report_key in self._expanded_report_keys
            is_pinned = self.is_report_pinned(report_key)
            rep_item = QTreeWidgetItem(self.tree)
            rep_item.setFlags(rep_item.flags() & ~Qt.ItemIsSelectable)
            rep_item.setData(0, Qt.UserRole, ("report", report_key, ""))
            rep_item.setSizeHint(0, QSize(0, 26))
            rep_item.setExpanded(is_expanded)

            rep_widget = ReportRowWidget(
                report_key=report_key,
                expanded=is_expanded,
                on_add_flow=lambda rk: self.add_flow_requested.emit(rk),
                on_menu=self._open_report_menu,
                is_pinned=is_pinned,
                parent=None,
            )
            self.tree.setItemWidget(rep_item, 0, rep_widget)

            for pf in report.process_flows:
                is_active = (report_key == active_report_key and pf.name == active_flow_name)
                flow_item = QTreeWidgetItem(rep_item)
                flow_item.setData(0, Qt.UserRole, ("flow", report_key, pf.name))
                flow_item.setSizeHint(0, QSize(0, 24))

                flow_widget = FlowRowWidget(
                    report_key=report_key,
                    flow_name=pf.name,
                    is_active=is_active,
                    on_menu=self._open_flow_menu,
                    parent=None,
                )
                self.tree.setItemWidget(flow_item, 0, flow_widget)
                self._flow_items_by_key[(report_key, pf.name)] = flow_item
                if is_active:
                    self.tree.setCurrentItem(flow_item)

        self.tree.blockSignals(False)

        if active_report_key and active_report_key in reports_by_key:
            self.set_active_report_title(reports_by_key[active_report_key].name)
        elif active_report_key:
            self.set_active_report_title(active_report_key)
        else:
            self.set_active_report_title("No Report Selected")

    def set_active_selection(
        self,
        active_report_key: Optional[str],
        active_flow_name: Optional[str],
        report_title: Optional[str] = None,
    ) -> None:
        """Update highlighted active process flow item and report header without rebuilding the tree."""
        self._active_report_key = active_report_key
        self._active_flow_name = active_flow_name

        if report_title is not None:
            self.set_active_report_title(report_title)
        elif active_report_key:
            self.set_active_report_title(active_report_key)

        self.tree.blockSignals(True)
        for (rk, fn), item in self._flow_items_by_key.items():
            is_active = (rk == active_report_key and fn == active_flow_name)
            w = self.tree.itemWidget(item, 0)
            if isinstance(w, FlowRowWidget):
                w.set_active(is_active)
            if is_active:
                self.tree.setCurrentItem(item)
                parent = item.parent()
                if parent and not parent.isExpanded():
                    parent.setExpanded(True)
                    self._expanded_report_keys.add(rk)
                    pw = self.tree.itemWidget(parent, 0)
                    if isinstance(pw, ReportRowWidget):
                        pw.set_expanded_indicator(True)
        self.tree.blockSignals(False)

    def _on_item_expanded_state_changed(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole)
        if isinstance(data, tuple) and len(data) >= 2 and data[0] == "report":
            report_key = data[1]
            expanded = item.isExpanded()
            if expanded:
                self._expanded_report_keys.add(report_key)
            else:
                self._expanded_report_keys.discard(report_key)
            w = self.tree.itemWidget(item, 0)
            if isinstance(w, ReportRowWidget):
                w.set_expanded_indicator(expanded)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, tuple) or len(data) < 3:
            return
        kind, report_key, flow_name = data
        if kind == "report":
            new_expanded = not item.isExpanded()
            item.setExpanded(new_expanded)
            if new_expanded:
                self._expanded_report_keys.add(report_key)
            else:
                self._expanded_report_keys.discard(report_key)
            w = self.tree.itemWidget(item, 0)
            if isinstance(w, ReportRowWidget):
                w.set_expanded_indicator(new_expanded)

            # Restore selection highlight to currently active flow so report click doesn't alter active selection
            if self._active_report_key and self._active_flow_name:
                active_item = self._flow_items_by_key.get((self._active_report_key, self._active_flow_name))
                if active_item:
                    self.tree.setCurrentItem(active_item)
        elif kind == "flow":
            self.process_flow_selected.emit(report_key, flow_name)

    def _create_report_menu(self, report_key: str) -> QMenu:
        menu = QMenu(self)
        pin_label = "Unpin" if self.is_report_pinned(report_key) else "Pin"
        pin_act = menu.addAction(pin_label)
        pin_act.triggered.connect(lambda: self.toggle_pin_report(report_key))
        menu.addSeparator()
        add_flow_act = menu.addAction("Add Process Flow")
        add_flow_act.triggered.connect(lambda: self.add_flow_requested.emit(report_key))
        menu.addSeparator()
        rename_act = menu.addAction("Rename")
        rename_act.triggered.connect(lambda: self.rename_report_requested.emit(report_key))
        remove_act = menu.addAction("Remove")
        remove_act.triggered.connect(lambda: self.remove_report_requested.emit(report_key))
        return menu

    def _open_report_menu(self, report_key: str, global_pos: QPoint) -> None:
        menu = self._create_report_menu(report_key)
        menu.exec(global_pos)

    def _open_flow_menu(self, report_key: str, flow_name: str, global_pos: QPoint) -> None:
        menu = QMenu(self)
        rename_act = menu.addAction("Rename")
        rename_act.triggered.connect(lambda: self.rename_flow_requested.emit(report_key, flow_name))
        remove_act = menu.addAction("Remove")
        remove_act.triggered.connect(lambda: self.remove_flow_requested.emit(report_key, flow_name))
        menu.exec(global_pos)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        global_pos = self.tree.viewport().mapToGlobal(pos)
        if not item:
            menu = QMenu(self)
            add_rep_act = menu.addAction("Add Report")
            add_rep_act.triggered.connect(self.add_report_requested.emit)
            menu.exec(global_pos)
            return

        data = item.data(0, Qt.UserRole)
        if not isinstance(data, tuple) or len(data) < 3:
            return
        kind, report_key, flow_name = data
        if kind == "report":
            self._open_report_menu(report_key, global_pos)
        elif kind == "flow":
            self._open_flow_menu(report_key, flow_name, global_pos)

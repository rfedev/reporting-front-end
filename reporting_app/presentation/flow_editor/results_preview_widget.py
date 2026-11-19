"""Modular Results Preview panel widget, model, table view, and background fetcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from reporting_app.core.result_preview_cache import (
    ResultPreviewCache,
    ResultPreviewData,
    fetch_bigquery_select_preview,
    fetch_bigquery_table_preview,
    load_csv_preview,
    load_local_file_preview,
)


@dataclass
class TablePreviewItemSpec:
    """Descriptor for a single BigQuery table, Output CSV, or Input CSV/Excel file inside a TableBoxNode."""

    cache_key: str
    label: str
    full_name: str
    kind: str  # "bq_table" | "csv" | "input_file"
    fallback_keys: List[str] = field(default_factory=list)
    bq_table_ref: str = ""
    csv_path: Optional[Path] = None
    fallback_csv_path: Optional[Path] = None
    query_name: str = ""
    sql_file_path: Optional[Path] = None
    has_headers: bool = True
    sheet_name: Optional[str] = None


@dataclass
class TableBoxPreviewSpec:
    """Descriptor for all previewable entries belonging to a selected TableBoxNode."""

    node_id: str
    box_type: str  # "Output Tables" | "Output CSV" | "Input Tables"
    query_owner: str = ""
    items: List[TablePreviewItemSpec] = field(default_factory=list)
    parameters: Dict[str, str] = field(default_factory=dict)
    project_id: str = ""
    workbench_dataset: str = ""


class PreviewTableModel(QAbstractTableModel):
    """Virtual table model displaying cached preview rows, NULL styling, and column type tooltips."""

    MAX_DISPLAY_LEN = 500

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._columns: List[str] = []
        self._column_types: List[str] = []
        self._column_modes: List[str] = []
        self._rows: List[List[Any]] = []
        self._source: str = ""

    def set_preview(self, data: Optional[ResultPreviewData]) -> None:
        self.beginResetModel()
        if data is None:
            self._columns = []
            self._column_types = []
            self._column_modes = []
            self._rows = []
            self._source = ""
        else:
            self._columns = list(data.columns or [])
            self._column_types = list(data.column_types or [])
            self._column_modes = list(data.column_modes or [])
            self._rows = [list(r) for r in (data.rows or [])]
            self._source = str(data.source or "")
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row_idx = index.row()
        col_idx = index.column()
        if row_idx < 0 or row_idx >= len(self._rows) or col_idx < 0 or col_idx >= len(self._columns):
            return None

        row = self._rows[row_idx]
        raw_val = row[col_idx] if col_idx < len(row) else None

        if role == Qt.UserRole:
            return raw_val

        if role == Qt.DisplayRole:
            if raw_val is None:
                return "NULL"
            text = str(raw_val).replace("\r\n", "\n").replace("\n", " ↵ ")
            if len(text) > self.MAX_DISPLAY_LEN:
                return text[: self.MAX_DISPLAY_LEN - 1] + "…"
            return text

        if role == Qt.ToolTipRole:
            if raw_val is None:
                return "NULL"
            return str(raw_val)

        if role == Qt.ForegroundRole and raw_val is None:
            return QColor("#6b7280")

        if role == Qt.FontRole and raw_val is None:
            font = QFont()
            font.setItalic(True)
            return font

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if orientation == Qt.Horizontal:
            if section < 0 or section >= len(self._columns):
                return None
            col_name = self._columns[section]
            if role == Qt.DisplayRole:
                return col_name
            if role == Qt.ToolTipRole:
                col_type = self._column_types[section] if section < len(self._column_types) else ""
                col_mode = self._column_modes[section] if section < len(self._column_modes) else ""
                if col_type:
                    mode_str = f" ({col_mode})" if col_mode else ""
                    return f"{col_name}\nType: {col_type}{mode_str}"
                if self._source == "csv":
                    return f"{col_name}\nSource: CSV"
                if self._source == "excel":
                    return f"{col_name}\nSource: Excel"
                return col_name
            return None

        if role == Qt.DisplayRole:
            return str(section + 1)
        return None


class ResultsHeaderView(QHeaderView):
    """Header supporting click-and-drag multi-column or multi-row selection while preserving resize handles."""

    range_selected = Signal(int, int, object)  # (anchor_section, current_section, Qt.KeyboardModifiers)

    def __init__(self, orientation: Qt.Orientation, parent: Optional[QWidget] = None):
        super().__init__(orientation, parent)
        self._dragging = False
        self._anchor_section = -1
        self._drag_modifiers: Qt.KeyboardModifiers = Qt.NoModifier
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setSectionsMovable(False)

    def _is_on_resize_grip(self, pos: QPoint, logical_sec: int) -> bool:
        if self.cursor().shape() in (Qt.SplitHCursor, Qt.SplitVCursor):
            return True
        if logical_sec < 0:
            return False
        coord = pos.x() if self.orientation() == Qt.Horizontal else pos.y()
        sec_pos = self.sectionViewportPosition(logical_sec)
        sec_size = self.sectionSize(logical_sec)
        if sec_size <= 8:
            return False
        grip_margin = max(3, min(5, self.style().pixelMetric(QStyle.PM_HeaderGripMargin, None, self)))
        if coord >= (sec_pos + sec_size - grip_margin):
            return True
        vis_idx = self.visualIndex(logical_sec)
        if vis_idx > 0 and coord <= (sec_pos + grip_margin):
            return True
        return False

    def _section_at_clamped_pos(self, pos: QPoint) -> int:
        count = self.count()
        if count <= 0:
            return -1
        sec = self.logicalIndexAt(pos)
        if sec >= 0:
            return sec
        coord = pos.x() if self.orientation() == Qt.Horizontal else pos.y()
        extent = self.width() if self.orientation() == Qt.Horizontal else self.height()
        if coord <= 0:
            first_pt = QPoint(1, 1)
            first_sec = self.logicalIndexAt(first_pt)
            return first_sec if first_sec >= 0 else 0
        last_logical = self.logicalIndex(count - 1)
        if last_logical >= 0:
            last_end = self.sectionViewportPosition(last_logical) + self.sectionSize(last_logical)
            if coord >= last_end or coord >= extent:
                return last_logical
        if coord >= extent:
            return count - 1
        return -1

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self.count() > 0:
            pos = event.position().toPoint()
            sec = self.logicalIndexAt(pos)
            if sec >= 0 and not self._is_on_resize_grip(pos, sec):
                self._dragging = True
                self._anchor_section = sec
                self._drag_modifiers = event.modifiers()
                if self.parentWidget() is not None:
                    self.parentWidget().setFocus()
                self.range_selected.emit(self._anchor_section, sec, self._drag_modifiers)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and (event.buttons() & Qt.LeftButton):
            cur_sec = self._section_at_clamped_pos(event.position().toPoint())
            if cur_sec >= 0 and self._anchor_section >= 0:
                self.range_selected.emit(self._anchor_section, cur_sec, self._drag_modifiers)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.button() == Qt.LeftButton:
            cur_sec = self._section_at_clamped_pos(event.position().toPoint())
            if cur_sec >= 0 and self._anchor_section >= 0:
                self.range_selected.emit(self._anchor_section, cur_sec, self._drag_modifiers)
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _escape_tsv_cell(value: str) -> str:
    """Format a single cell for TSV clipboard paste into Excel or Google Sheets."""
    if any(ch in value for ch in ("\t", "\n", "\r", '"')):
        return '"' + value.replace('"', '""') + '"'
    return value


class ResultsPreviewTableView(QTableView):
    """Read-only preview grid with column/row header drag selection and Excel/Sheets TSV Ctrl+C copy."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._header_anchors: Dict[Qt.Orientation, int] = {}

        self.setSortingEnabled(False)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        h_header = ResultsHeaderView(Qt.Horizontal, self)
        v_header = ResultsHeaderView(Qt.Vertical, self)
        v_header.setDefaultSectionSize(22)
        v_header.setMinimumWidth(34)
        self.setHorizontalHeader(h_header)
        self.setVerticalHeader(v_header)

        h_header.range_selected.connect(
            lambda a, c, m: self._select_header_range(Qt.Horizontal, a, c, m)
        )
        v_header.range_selected.connect(
            lambda a, c, m: self._select_header_range(Qt.Vertical, a, c, m)
        )

        self.setStyleSheet(
            "QTableView {"
            "  background-color: #16181d;"
            "  alternate-background-color: #1b1e25;"
            "  color: #e5e7eb;"
            "  gridline-color: #2d3138;"
            "  border: 1px solid #2d3138;"
            "  border-radius: 3px;"
            "  font-size: 11px;"
            "  selection-background-color: rgba(59, 130, 246, 0.35);"
            "  selection-color: #ffffff;"
            "}"
            "QHeaderView::section {"
            "  background-color: #20242c;"
            "  color: #d1d5db;"
            "  border: none;"
            "  border-right: 1px solid #2d3138;"
            "  border-bottom: 1px solid #2d3138;"
            "  padding: 3px 6px;"
            "  font-size: 11px;"
            "  font-weight: bold;"
            "}"
            "QHeaderView::section:checked {"
            "  background-color: #2a3446;"
            "  color: #ffffff;"
            "}"
        )

    def _select_header_range(
        self,
        orientation: Qt.Orientation,
        anchor: int,
        current: int,
        modifiers: Any,
    ) -> None:
        model = self.model()
        sel_model = self.selectionModel()
        if not model or not sel_model:
            return
        rows = model.rowCount()
        cols = model.columnCount()
        if rows <= 0 or cols <= 0:
            return

        eff_anchor = anchor
        mods = modifiers if isinstance(modifiers, Qt.KeyboardModifiers) else Qt.KeyboardModifiers(modifiers)
        if (mods & Qt.ShiftModifier) and orientation in self._header_anchors:
            eff_anchor = self._header_anchors[orientation]
        else:
            self._header_anchors[orientation] = anchor

        start_idx = max(0, min(eff_anchor, current))
        end_idx = max(eff_anchor, current)
        if orientation == Qt.Horizontal:
            end_idx = min(cols - 1, end_idx)
            tl = model.index(0, start_idx)
            br = model.index(rows - 1, end_idx)
        else:
            end_idx = min(rows - 1, end_idx)
            tl = model.index(start_idx, 0)
            br = model.index(end_idx, cols - 1)

        cmd = QItemSelectionModel.Select if (mods & Qt.ControlModifier) else QItemSelectionModel.ClearAndSelect
        sel_model.select(QItemSelection(tl, br), cmd)
        sel_model.setCurrentIndex(tl, QItemSelectionModel.NoUpdate)

    def format_selection_as_tsv(self, include_headers: bool = False) -> str:
        """Serialize currently selected cells as tab-separated values for Excel/Google Sheets."""
        model = self.model()
        sel_model = self.selectionModel()
        if not model or not sel_model:
            return ""
        indexes = sel_model.selectedIndexes()
        if not indexes:
            return ""

        selected_rows = sorted({idx.row() for idx in indexes})
        selected_cols = sorted({idx.column() for idx in indexes})
        selected_set = {(idx.row(), idx.column()) for idx in indexes}

        lines: List[str] = []
        if include_headers:
            hdr_cells = [
                _escape_tsv_cell(str(model.headerData(col, Qt.Horizontal, Qt.DisplayRole) or ""))
                for col in selected_cols
            ]
            lines.append("\t".join(hdr_cells))

        for row in selected_rows:
            row_cells: List[str] = []
            for col in selected_cols:
                if (row, col) not in selected_set:
                    row_cells.append("")
                else:
                    raw = model.data(model.index(row, col), Qt.UserRole)
                    row_cells.append(_escape_tsv_cell("" if raw is None else str(raw)))
            lines.append("\t".join(row_cells))

        return "\n".join(lines)

    def copy_selection_to_clipboard(self, include_headers: bool = False) -> str:
        """Copy selected cells as TSV to the system clipboard."""
        tsv = self.format_selection_as_tsv(include_headers=include_headers)
        if tsv:
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(tsv)
        return tsv

    def _on_context_menu(self, pos: QPoint) -> None:
        sel_model = self.selectionModel()
        has_sel = bool(sel_model and sel_model.hasSelection())
        menu = QMenu(self)
        copy_act = menu.addAction("Copy\tCtrl+C")
        copy_act.setEnabled(has_sel)
        copy_act.triggered.connect(lambda: self.copy_selection_to_clipboard(include_headers=False))

        copy_hdr_act = menu.addAction("Copy with Headers")
        copy_hdr_act.setEnabled(has_sel)
        copy_hdr_act.triggered.connect(lambda: self.copy_selection_to_clipboard(include_headers=True))

        menu.addSeparator()
        sel_all_act = menu.addAction("Select All\tCtrl+A")
        sel_all_act.setEnabled(bool(self.model() and self.model().rowCount() > 0))
        sel_all_act.triggered.connect(self.selectAll)
        menu.exec(self.viewport().mapToGlobal(pos))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.Copy) or (
            (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_C
        ):
            self.copy_selection_to_clipboard(include_headers=False)
            event.accept()
            return
        if event.matches(QKeySequence.SelectAll) or (
            (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_A
        ):
            self.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    def adjust_column_widths(self) -> None:
        """Size columns cleanly to content with sensible min/max bounds."""
        model = self.model()
        if not model or model.columnCount() == 0:
            return
        self.resizeColumnsToContents()
        for col in range(model.columnCount()):
            w = self.columnWidth(col)
            self.setColumnWidth(col, max(70, min(260, w + 14)))


class PreviewFetchWorker(QThread):
    """Background worker fetching table, CSV, Excel, or BigQuery SELECT previews without blocking UI."""

    item_cached = Signal(str)
    progress_changed = Signal(int, int, str)
    item_failed = Signal(str, str)
    finished_fetch = Signal(bool, str)

    def __init__(
        self,
        mode: str,
        spec: TableBoxPreviewSpec,
        target_index: int,
        limit: int,
        cache: ResultPreviewCache,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.mode = mode  # "bq_single" | "bq_all" | "csv_single" | "csv_all" | "file_single" | "file_all" | "bq_select"
        self.spec = spec
        self.target_index = target_index
        self.limit = max(1, int(limit or 100))
        self.cache = cache
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _load_local_file_item(self, item: TablePreviewItemSpec) -> ResultPreviewData:
        candidate = item.csv_path
        if (candidate is None or not candidate.exists()) and item.fallback_csv_path and item.fallback_csv_path.exists():
            candidate = item.fallback_csv_path
        if candidate is None:
            label_noun = "Output CSV" if item.kind == "csv" else "File"
            raise FileNotFoundError(f"{label_noun} path not resolved for '{item.label}'.")
        return load_local_file_preview(
            candidate,
            limit=self.limit,
            sheet_name=item.sheet_name,
            has_headers=item.has_headers,
            display_name=item.label,
            cache_key=item.cache_key,
        )

    def _load_csv_item(self, item: TablePreviewItemSpec) -> ResultPreviewData:
        return self._load_local_file_item(item)

    def _fetch_single_item(self, item: TablePreviewItemSpec) -> ResultPreviewData:
        if item.kind == "input_file":
            return self._load_local_file_item(item)
        if item.kind == "csv" or self.mode in ("csv_single", "csv_all"):
            return self._load_csv_item(item)
        return fetch_bigquery_table_preview(
            item.bq_table_ref or item.full_name,
            limit=self.limit,
            project_id=self.spec.project_id or None,
            workbench_dataset=self.spec.workbench_dataset,
            cache_key=item.cache_key,
        )

    def run(self) -> None:
        items = self.spec.items
        if not items:
            self.finished_fetch.emit(False, "No tables available to preview.")
            return

        try:
            if self.mode in ("bq_single", "bq_all", "csv_single", "csv_all", "file_single", "file_all"):
                fetch_all = self.mode.endswith("_all")
                indices = (
                    list(range(len(items)))
                    if fetch_all
                    else [max(0, min(len(items) - 1, self.target_index))]
                )
                total = len(indices)
                errors: List[str] = []
                cancel_msg = (
                    "CSV load cancelled."
                    if self.mode in ("csv_single", "csv_all")
                    else "Preview fetch cancelled."
                )
                for pos, idx in enumerate(indices, start=1):
                    if self._cancel_requested:
                        self.finished_fetch.emit(False, cancel_msg)
                        return
                    item = items[idx]
                    self.progress_changed.emit(pos, total, item.label)
                    try:
                        pdata = self._fetch_single_item(item)
                        self.cache.put(pdata)
                        self.item_cached.emit(pdata.key)
                    except Exception as exc:
                        err = str(exc)
                        errors.append(f"{item.label}: {err}")
                        self.item_failed.emit(item.cache_key, err)

                if errors:
                    self.finished_fetch.emit(False, "\n\n".join(errors))
                else:
                    self.finished_fetch.emit(True, "")
                return

            if self.mode == "bq_select":
                sql_path = next((it.sql_file_path for it in items if it.sql_file_path), None)
                if not sql_path or not sql_path.exists():
                    self.finished_fetch.emit(
                        False,
                        f"Could not locate SQL script for '{self.spec.query_owner or 'query'}'.",
                    )
                    return

                self.progress_changed.emit(1, 1, self.spec.query_owner or sql_path.stem)
                csv_targets = [
                    it.csv_path if it.csv_path is not None else Path(it.label)
                    for it in items
                ]
                previews = fetch_bigquery_select_preview(
                    sql_script_path=sql_path,
                    target_csv_paths=csv_targets,
                    parameters=self.spec.parameters,
                    limit=self.limit,
                    project_id=self.spec.project_id or None,
                    workbench_dataset=self.spec.workbench_dataset,
                )
                for pdata in previews:
                    self.cache.put(pdata)
                    self.item_cached.emit(pdata.key)
                self.finished_fetch.emit(True, "")
                return

            self.finished_fetch.emit(False, f"Unsupported preview fetch mode: {self.mode}")
        except Exception as exc:
            self.finished_fetch.emit(False, str(exc))


class ResultsPreviewWidget(QWidget):
    """Modular UI panel displaying session-cached Results Preview with table dropdown & Refresh ▾."""

    close_requested = Signal()

    def __init__(
        self,
        cache: Optional[ResultPreviewCache] = None,
        row_limit_getter: Optional[Callable[[], int]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._cache: Optional[ResultPreviewCache] = cache
        self._row_limit_getter: Optional[Callable[[], int]] = row_limit_getter
        self._spec: Optional[TableBoxPreviewSpec] = None
        self._active_worker: Optional[PreviewFetchWorker] = None
        self._updating_combo = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header row: Box type title + Refresh ▾ split button + close button (☰)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(2, 0, 0, 2)
        header_row.setSpacing(6)

        self.title_label = QLabel("Results Preview", self)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.title_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #f3f4f6;")
        header_row.addWidget(self.title_label, 1)

        self.action_menu = QMenu(self)

        split_btn_style = (
            "QToolButton { "
            "  font-size: 11px; font-weight: bold; padding: 2px 18px 2px 8px; "
            "  background-color: #2563eb; color: white; border: 1px solid #3b82f6; border-radius: 3px;"
            "} "
            "QToolButton:hover { background-color: #1d4ed8; } "
            "QToolButton:disabled { background-color: #262b33; color: #6b7280; border-color: #374151; } "
            "QToolButton::menu-button { "
            "  border-left: 1px solid rgba(255,255,255,0.25); width: 16px; "
            "  border-top-right-radius: 3px; border-bottom-right-radius: 3px;"
            "} "
            "QToolButton::menu-button:hover { background-color: rgba(255,255,255,0.12); }"
        )

        self.action_btn = QToolButton(self)
        self.action_btn.setFixedHeight(24)
        self.action_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.action_btn.setMenu(self.action_menu)
        self.action_btn.setText("↻ Refresh")
        self.action_btn.setStyleSheet(split_btn_style)
        self.action_btn.clicked.connect(self.trigger_primary_action)
        header_row.addWidget(self.action_btn)

        self.close_btn = QPushButton("☰", self)
        self.close_btn.setFixedSize(26, 24)
        self.close_btn.setToolTip("SQL Editor / Results Preview")
        self.close_btn.setStyleSheet("QPushButton { font-size: 14px; padding: 0px; }")
        self.close_btn.clicked.connect(self.close_requested.emit)
        header_row.addWidget(self.close_btn)

        layout.addLayout(header_row)

        # Table selector row (dropdown when >1 table, compact label when 1 table)
        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(2, 0, 2, 2)
        selector_row.setSpacing(6)

        self.table_selector_combo = QComboBox(self)
        self.table_selector_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.table_selector_combo.setMinimumWidth(80)
        self.table_selector_combo.setStyleSheet(
            "QComboBox {"
            "  background-color: #1c1e24; color: #e5e7eb; border: 1px solid #2d3138;"
            "  border-radius: 3px; padding: 3px 6px; font-size: 11px; font-family: monospace;"
            "}"
            "QComboBox::drop-down { width: 18px; }"
        )
        self.table_selector_combo.currentIndexChanged.connect(self._on_table_selection_changed)
        selector_row.addWidget(self.table_selector_combo, 1)

        self.single_table_label = QLabel("", self)
        self.single_table_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.single_table_label.setStyleSheet(
            "color: #cbd5e1; font-size: 11px; font-family: monospace; padding: 2px 2px;"
        )
        selector_row.addWidget(self.single_table_label, 1)

        layout.addLayout(selector_row)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #374151;")
        layout.addWidget(sep)

        # Body stack: empty state / loading / error / table grid
        self.body_stack = QStackedWidget(self)

        # Page 0: Not fetched yet in this session
        self.empty_page = QWidget(self)
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(16, 24, 16, 24)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)

        self.empty_label = QLabel("Results have not been fetched in this session.", self.empty_page)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color: #9ca3af; font-size: 12px;")
        empty_layout.addWidget(self.empty_label)

        empty_btn_row = QHBoxLayout()
        empty_btn_row.addStretch(1)
        self.empty_action_btn = QToolButton(self.empty_page)
        self.empty_action_btn.setFixedHeight(28)
        self.empty_action_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.empty_action_btn.setMenu(self.action_menu)
        self.empty_action_btn.setText("↻ Refresh")
        self.empty_action_btn.setStyleSheet(split_btn_style)
        self.empty_action_btn.clicked.connect(self.trigger_primary_action)
        empty_btn_row.addWidget(self.empty_action_btn)
        empty_btn_row.addStretch(1)
        empty_layout.addLayout(empty_btn_row)
        empty_layout.addStretch(1)
        self.body_stack.addWidget(self.empty_page)

        # Page 1: Loading state
        self.loading_page = QWidget(self)
        loading_layout = QVBoxLayout(self.loading_page)
        loading_layout.setContentsMargins(16, 24, 16, 24)
        loading_layout.addStretch(1)
        self.loading_label = QLabel("Fetching preview rows...", self.loading_page)
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setWordWrap(True)
        self.loading_label.setStyleSheet("color: #93c5fd; font-size: 12px;")
        loading_layout.addWidget(self.loading_label)
        loading_layout.addStretch(1)
        self.body_stack.addWidget(self.loading_page)

        # Page 2: Error state
        self.error_page = QWidget(self)
        err_layout = QVBoxLayout(self.error_page)
        err_layout.setContentsMargins(16, 24, 16, 24)
        err_layout.setSpacing(12)
        err_layout.addStretch(1)
        self.error_label = QLabel("", self.error_page)
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error_label.setStyleSheet("color: #f87171; font-size: 11px;")
        err_layout.addWidget(self.error_label)

        err_btn_row = QHBoxLayout()
        err_btn_row.addStretch(1)
        self.error_action_btn = QToolButton(self.error_page)
        self.error_action_btn.setFixedHeight(26)
        self.error_action_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.error_action_btn.setMenu(self.action_menu)
        self.error_action_btn.setText("↻ Retry")
        self.error_action_btn.setStyleSheet(split_btn_style)
        self.error_action_btn.clicked.connect(self.trigger_primary_action)
        err_btn_row.addWidget(self.error_action_btn)
        err_btn_row.addStretch(1)
        err_layout.addLayout(err_btn_row)
        err_layout.addStretch(1)
        self.body_stack.addWidget(self.error_page)

        # Page 3: Table Grid
        self.table_model = PreviewTableModel(self)
        self.table_view = ResultsPreviewTableView(self)
        self.table_view.setModel(self.table_model)
        self.body_stack.addWidget(self.table_view)

        layout.addWidget(self.body_stack, 1)

        # Compact footer
        self.footer_label = QLabel("", self)
        self.footer_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.footer_label.setStyleSheet("font-size: 11px; color: #9ca3af; padding: 2px 4px;")
        layout.addWidget(self.footer_label)

    def set_cache(
        self,
        cache: Optional[ResultPreviewCache],
        row_limit_getter: Optional[Callable[[], int]] = None,
    ) -> None:
        self._cache = cache
        if row_limit_getter is not None:
            self._row_limit_getter = row_limit_getter

    def _get_row_limit(self) -> int:
        if callable(self._row_limit_getter):
            try:
                return max(1, int(self._row_limit_getter()))
            except Exception:
                pass
        return 100

    @staticmethod
    def _is_excel_item(item: Optional[TablePreviewItemSpec]) -> bool:
        if item is None:
            return False
        for path in (item.csv_path, item.fallback_csv_path):
            if path is not None and path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
                return True
        return Path(item.full_name or item.label).suffix.lower() in {".xlsx", ".xlsm", ".xls"}

    def _rebuild_action_controls(self) -> None:
        self.action_menu.clear()
        spec = self._spec
        cur_item = self.current_item()
        is_output_csv = bool(spec and spec.box_type == "Output CSV")
        is_input_file = bool(cur_item and cur_item.kind == "input_file")
        has_items = bool(spec and spec.items)
        is_busy = self._active_worker is not None and self._active_worker.isRunning()

        if is_output_csv:
            primary_text = "📂 Load from CSV"
            primary_tip = "Load preview rows from the local output CSV file"
            act_load = QAction("Load from CSV", self.action_menu)
            act_load.triggered.connect(self.trigger_primary_action)
            self.action_menu.addAction(act_load)

            if spec and len(spec.items) > 1:
                act_load_all = QAction("Load All from CSV", self.action_menu)
                act_load_all.triggered.connect(self.trigger_refresh_all)
                self.action_menu.addAction(act_load_all)

            self.action_menu.addSeparator()
            act_bq = QAction("Refresh from BigQuery", self.action_menu)
            act_bq.triggered.connect(self.trigger_refresh_from_bigquery)
            self.action_menu.addAction(act_bq)
        elif is_input_file:
            is_excel = self._is_excel_item(cur_item)
            kind_noun = "Excel" if is_excel else "CSV"
            primary_text = f"📂 Load from {kind_noun}"
            primary_tip = f"Load preview rows from the local {kind_noun} file"
            act_load = QAction(f"Load from {kind_noun}", self.action_menu)
            act_load.triggered.connect(self.trigger_primary_action)
            self.action_menu.addAction(act_load)

            if spec and len(spec.items) > 1:
                act_load_all = QAction("Load All Files", self.action_menu)
                act_load_all.triggered.connect(self.trigger_refresh_all)
                self.action_menu.addAction(act_load_all)
        else:
            primary_text = "↻ Refresh"
            primary_tip = "Fetch preview rows from BigQuery for the selected table"
            act_one = QAction("Refresh Selected", self.action_menu)
            act_one.triggered.connect(self.trigger_primary_action)
            self.action_menu.addAction(act_one)

            act_all = QAction("Refresh All", self.action_menu)
            act_all.triggered.connect(self.trigger_refresh_all)
            self.action_menu.addAction(act_all)

        for btn in (self.action_btn, self.empty_action_btn):
            btn.setText(primary_text)
            btn.setToolTip(primary_tip)
            btn.setEnabled(has_items and not is_busy)
        self.error_action_btn.setText(primary_text)
        self.error_action_btn.setToolTip(primary_tip)
        self.error_action_btn.setEnabled(has_items and not is_busy)

    def current_item_index(self) -> int:
        if not self._spec or not self._spec.items:
            return -1
        if len(self._spec.items) == 1:
            return 0
        return max(0, min(len(self._spec.items) - 1, self.table_selector_combo.currentIndex()))

    def current_item(self) -> Optional[TablePreviewItemSpec]:
        idx = self.current_item_index()
        if idx < 0 or not self._spec:
            return None
        return self._spec.items[idx]

    def show_table_box(self, spec: TableBoxPreviewSpec) -> None:
        """Display Results Preview for the given TableBoxNode descriptor."""
        prev_node_id = self._spec.node_id if self._spec else None
        prev_label = self.table_selector_combo.currentText() if prev_node_id == spec.node_id else ""
        self._spec = spec

        self.title_label.setText(spec.box_type or "Results Preview")
        title_tip = f"{spec.box_type} ({spec.query_owner})" if spec.query_owner else spec.box_type
        self.title_label.setToolTip(title_tip)

        self._updating_combo = True
        try:
            self.table_selector_combo.clear()
            for item in spec.items:
                self.table_selector_combo.addItem(item.label)
                idx = self.table_selector_combo.count() - 1
                self.table_selector_combo.setItemData(idx, item.full_name, Qt.ToolTipRole)

            if len(spec.items) > 1:
                self.table_selector_combo.setVisible(True)
                self.single_table_label.setVisible(False)
                if prev_label:
                    match_idx = self.table_selector_combo.findText(prev_label)
                    if match_idx >= 0:
                        self.table_selector_combo.setCurrentIndex(match_idx)
                cur_it = self.current_item()
                if cur_it:
                    self.table_selector_combo.setToolTip(cur_it.full_name)
            elif len(spec.items) == 1:
                self.table_selector_combo.setVisible(False)
                self.single_table_label.setVisible(True)
                self.single_table_label.setText(spec.items[0].label)
                self.single_table_label.setToolTip(spec.items[0].full_name)
            else:
                self.table_selector_combo.setVisible(False)
                self.single_table_label.setVisible(True)
                self.single_table_label.setText("(No tables listed)")
                self.single_table_label.setToolTip("")
        finally:
            self._updating_combo = False

        self._rebuild_action_controls()
        self.reload_from_cache()

    def _on_table_selection_changed(self, _index: int) -> None:
        if self._updating_combo:
            return
        cur_it = self.current_item()
        if cur_it:
            self.table_selector_combo.setToolTip(cur_it.full_name)
        self._rebuild_action_controls()
        self.reload_from_cache()

    def reload_from_cache(self) -> None:
        """Check session temp cache for the currently selected table and update the view."""
        if not self._spec or not self._spec.items:
            self.table_model.set_preview(None)
            self.empty_label.setText("This table box does not list any tables.")
            self.footer_label.setText("")
            self.body_stack.setCurrentWidget(self.empty_page)
            return

        item = self.current_item()
        if item is None:
            return

        cached = (
            self._cache.get(item.cache_key, fallback_keys=item.fallback_keys)
            if self._cache is not None
            else None
        )
        if cached is not None:
            self._display_preview_data(cached)
            return

        if self._active_worker is not None and self._active_worker.isRunning():
            return

        self.table_model.set_preview(None)
        self.footer_label.setText("")
        if item.kind == "csv":
            exists_on_disk = bool(
                (item.csv_path and item.csv_path.exists())
                or (item.fallback_csv_path and item.fallback_csv_path.exists())
            )
            if exists_on_disk:
                self.empty_label.setText(
                    f"Output CSV has not been loaded in this session yet.\n"
                    f"Click 'Load from CSV' to preview '{item.label}'."
                )
            else:
                self.empty_label.setText(
                    f"Output CSV file does not exist on disk yet:\n{item.label}\n\n"
                    f"Run the query, or open ▾ → Refresh from BigQuery."
                )
        elif item.kind == "input_file":
            kind_noun = "Excel" if self._is_excel_item(item) else "CSV"
            exists_on_disk = bool(
                (item.csv_path and item.csv_path.exists())
                or (item.fallback_csv_path and item.fallback_csv_path.exists())
            )
            if exists_on_disk:
                self.empty_label.setText(
                    f"Input {kind_noun} file has not been loaded in this session yet.\n"
                    f"Click 'Load from {kind_noun}' to preview '{item.label}'."
                )
            else:
                self.empty_label.setText(
                    f"Input {kind_noun} file was not found on disk:\n{item.full_name or item.label}"
                )
        else:
            self.empty_label.setText(
                f"Results for '{item.label}' have not been fetched this session.\n"
                f"Click 'Refresh' to fetch up to {self._get_row_limit():,} rows."
            )
        self.body_stack.setCurrentWidget(self.empty_page)

    @staticmethod
    def _format_timestamp(fetched_at: str) -> str:
        if not fetched_at:
            return ""
        try:
            dt = datetime.fromisoformat(fetched_at)
            return dt.strftime("%H:%M:%S")
        except Exception:
            if "T" in fetched_at:
                return fetched_at.split("T", 1)[1][:8]
            return fetched_at[:8]

    def _display_preview_data(self, data: ResultPreviewData) -> None:
        self.table_model.set_preview(data)
        self.body_stack.setCurrentWidget(self.table_view)
        self.table_view.adjust_column_widths()

        row_cnt = len(data.rows)
        if data.source == "csv":
            src_tag = " (from CSV)"
        elif data.source == "excel":
            src_tag = " (from Excel)"
        else:
            src_tag = ""
        if data.total_rows is not None and data.total_rows > row_cnt:
            count_text = f"Showing {row_cnt:,} of {data.total_rows:,} rows{src_tag}"
        elif data.truncated:
            count_text = f"Showing first {row_cnt:,} rows{src_tag}"
        else:
            noun = "row" if row_cnt == 1 else "rows"
            count_text = f"Showing {row_cnt:,} {noun}{src_tag}"

        ts = self._format_timestamp(data.fetched_at)
        footer_text = f"{count_text} • {ts}" if ts else count_text
        self.footer_label.setText(footer_text)
        self.footer_label.setToolTip(f"{data.display_name} ({data.source_detail})")

    def trigger_primary_action(self) -> None:
        if not self._spec or not self._spec.items:
            return
        cur_item = self.current_item()
        if self._spec.box_type == "Output CSV":
            mode = "csv_single"
        elif cur_item is not None and cur_item.kind == "input_file":
            mode = "file_single"
        else:
            mode = "bq_single"
        self._start_fetch(mode=mode)

    def trigger_refresh_all(self) -> None:
        if not self._spec or not self._spec.items:
            return
        if self._spec.box_type == "Output CSV":
            mode = "csv_all"
        elif any(it.kind == "input_file" for it in self._spec.items):
            mode = "file_all"
        else:
            mode = "bq_all"
        self._start_fetch(mode=mode)

    def trigger_refresh_from_bigquery(self) -> None:
        if not self._spec or not self._spec.items:
            return
        self._start_fetch(mode="bq_select")

    def _start_fetch(self, mode: str) -> None:
        if not self._spec or not self._spec.items or self._cache is None:
            return
        if self._active_worker is not None and self._active_worker.isRunning():
            return

        target_idx = max(0, self.current_item_index())
        cur_item = self.current_item()
        label = cur_item.label if cur_item else ""
        if mode == "bq_all":
            self.loading_label.setText(f"Refreshing {len(self._spec.items)} table(s) from BigQuery...")
        elif mode == "csv_all":
            self.loading_label.setText(f"Loading {len(self._spec.items)} CSV file(s)...")
        elif mode == "csv_single":
            self.loading_label.setText(f"Loading CSV preview for '{label}'...")
        elif mode == "file_all":
            self.loading_label.setText(f"Loading {len(self._spec.items)} input file(s)...")
        elif mode == "file_single":
            kind_noun = "Excel" if self._is_excel_item(cur_item) else "CSV"
            self.loading_label.setText(f"Loading {kind_noun} preview for '{label}'...")
        elif mode == "bq_select":
            self.loading_label.setText(f"Running '{self._spec.query_owner or 'query'}' preview in BigQuery...")
        else:
            self.loading_label.setText(f"Fetching preview rows for '{label}'...")

        self.body_stack.setCurrentWidget(self.loading_page)
        self.footer_label.setText("")

        worker = PreviewFetchWorker(
            mode=mode,
            spec=self._spec,
            target_index=target_idx,
            limit=self._get_row_limit(),
            cache=self._cache,
            parent=self,
        )
        self._active_worker = worker
        worker.progress_changed.connect(self._on_worker_progress)
        worker.item_cached.connect(self._on_worker_item_cached)
        worker.finished_fetch.connect(self._on_worker_finished)
        self._rebuild_action_controls()
        worker.start()

    def _on_worker_progress(self, cur: int, total: int, label: str) -> None:
        if total > 1:
            self.loading_label.setText(f"Fetching [{cur}/{total}]: {label}...")
        else:
            self.loading_label.setText(f"Fetching preview for '{label}'...")

    def _on_worker_item_cached(self, cache_key: str) -> None:
        cur_item = self.current_item()
        if cur_item and (cur_item.cache_key == cache_key or cache_key in cur_item.fallback_keys):
            self.reload_from_cache()

    def _on_worker_finished(self, success: bool, error_message: str) -> None:
        worker = self._active_worker
        self._active_worker = None
        if worker is not None:
            worker.deleteLater()
        self._rebuild_action_controls()

        cur_item = self.current_item()
        if not success:
            cached = (
                self._cache.get(cur_item.cache_key, fallback_keys=cur_item.fallback_keys)
                if (cur_item and self._cache is not None)
                else None
            )
            if cached is not None:
                self._display_preview_data(cached)
                if error_message:
                    self.footer_label.setText(f"{self.footer_label.text()}  •  Partial error")
                    self.footer_label.setToolTip(error_message)
                return

            self.table_model.set_preview(None)
            self.error_label.setText(error_message or "Failed to load preview.")
            self.body_stack.setCurrentWidget(self.error_page)
            self.footer_label.setText("")
            return

        self.reload_from_cache()

    def shutdown(self) -> None:
        """Stop any running background preview worker before widget destruction."""
        worker = self._active_worker
        if worker is not None:
            worker.request_cancel()
            if worker.isRunning():
                worker.wait(1500)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(500)
            self._active_worker = None

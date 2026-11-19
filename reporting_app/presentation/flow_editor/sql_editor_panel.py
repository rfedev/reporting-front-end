"""Built-in SQL code editor and Results Preview panel."""

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QResizeEvent,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from reporting_app.presentation.flow_editor.results_preview_widget import (
    ResultsPreviewWidget,
    TableBoxPreviewSpec,
)
from reporting_app.presentation.flow_editor.sql_highlighter import (
    SqlSyntaxErrorInfo,
    SqlSyntaxHighlighter,
    validate_bigquery_sql,
)


class _LineNumberArea(QWidget):
    """Left gutter widget rendering line numbers and syntax error indicators."""

    def __init__(self, editor: "SqlCodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.line_number_area_paint_event(event)


class SqlCodeEditor(QPlainTextEdit):
    """Code editor built on QPlainTextEdit with line numbers, Tab indent, and syntax error underlines."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._error_lines: Dict[int, str] = {}

        font = QFont("JetBrains Mono, Fira Code, Consolas, Monospace", 10)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setPlaceholderText("Select a query node to edit SQL, or a table box to preview results.")
        self.setStyleSheet(
            "QPlainTextEdit { "
            "background-color: #16181d; color: #e5e7eb; "
            "border: 1px solid #2d3138; border-radius: 3px; "
            "selection-background-color: rgba(59, 130, 246, 0.35); "
            "}"
        )

        self._line_number_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._update_extra_selections)

        self._update_line_number_area_width(0)
        self._update_extra_selections()

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_area_width(self, _new_block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor("#1c1e24"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur_line = self.textCursor().blockNumber() + 1

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_num = block_number + 1
                if line_num in self._error_lines:
                    painter.setPen(QColor("#f87171"))
                elif line_num == cur_line:
                    painter.setPen(QColor("#e5e7eb"))
                else:
                    painter.setPen(QColor("#6b7280"))
                painter.drawText(
                    0,
                    top,
                    self._line_number_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignRight | Qt.AlignVCenter,
                    str(line_num),
                )

            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def set_error_lines(self, error_lines: Dict[int, str]) -> None:
        """Mark 1-based error line numbers with red underlines and update the gutter."""
        self._error_lines = dict(error_lines or {})
        self._update_extra_selections()
        self._line_number_area.update()

    def _update_extra_selections(self) -> None:
        extra_selections: List[QTextEdit.ExtraSelection] = []

        if not self.isReadOnly():
            cur_sel = QTextEdit.ExtraSelection()
            cur_sel.format.setBackground(QColor(255, 255, 255, 12))
            cur_sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            cur_sel.cursor = self.textCursor()
            cur_sel.cursor.clearSelection()
            extra_selections.append(cur_sel)

        doc = self.document()
        for line_num, err_msg in sorted(self._error_lines.items()):
            block = doc.findBlockByNumber(max(0, line_num - 1))
            if not block.isValid():
                continue

            # Subtle full-line error tint
            bg_sel = QTextEdit.ExtraSelection()
            bg_sel.format.setBackground(QColor(239, 68, 68, 32))
            bg_sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            bg_sel.cursor = QTextCursor(block)
            extra_selections.append(bg_sel)

            # Wavy underline across non-whitespace content on the error line
            text = block.text()
            stripped = text.strip()
            if stripped:
                lead_ws = len(text) - len(text.lstrip())
                wave_sel = QTextEdit.ExtraSelection()
                wave_sel.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
                wave_sel.format.setUnderlineColor(QColor("#f87171"))
                wave_sel.format.setToolTip(err_msg)
                cur = QTextCursor(block)
                cur.setPosition(block.position() + lead_ws)
                cur.setPosition(block.position() + lead_ws + len(stripped), QTextCursor.KeepAnchor)
                wave_sel.cursor = cur
                extra_selections.append(wave_sel)

        self.setExtraSelections(extra_selections)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.isReadOnly():
            super().keyPressEvent(event)
            return

        if (event.modifiers() & Qt.ControlModifier) and (
            event.key() == Qt.Key_Slash or event.text() == "/"
        ):
            self.toggle_line_comment()
            event.accept()
            return

        if event.key() == Qt.Key_Tab and not (event.modifiers() & Qt.ControlModifier):
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._indent_selection(cursor, unindent=False)
            else:
                cursor.insertText("    ")
            event.accept()
            return

        if event.key() == Qt.Key_Backtab or (event.key() == Qt.Key_Tab and (event.modifiers() & Qt.ShiftModifier)):
            self._indent_selection(self.textCursor(), unindent=True)
            event.accept()
            return

        super().keyPressEvent(event)

    def toggle_line_comment(self) -> None:
        """Toggle '-- ' line comments on the current line or all selected lines."""
        if self.isReadOnly():
            return

        cursor = self.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.document()
        first_block = doc.findBlock(start).blockNumber()
        last_block_obj = doc.findBlock(end)
        last_block = last_block_obj.blockNumber()
        if end > start and last_block_obj.position() == end:
            last_block = max(first_block, last_block - 1)

        blocks = [
            doc.findBlockByNumber(blk_num)
            for blk_num in range(first_block, last_block + 1)
            if doc.findBlockByNumber(blk_num).isValid()
        ]
        if not blocks:
            return

        non_empty = [b for b in blocks if b.text().strip()] or blocks
        all_commented = all(b.text().lstrip().startswith("--") for b in non_empty)

        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()
        for blk in blocks:
            line_text = blk.text()
            if all_commented:
                stripped = line_text.lstrip()
                if stripped.startswith("--"):
                    lead_len = len(line_text) - len(stripped)
                    remove_len = 3 if stripped.startswith("-- ") else 2
                    line_cursor = QTextCursor(blk)
                    line_cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, lead_len)
                    line_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, remove_len)
                    line_cursor.removeSelectedText()
            else:
                if not line_text.strip() and len(blocks) > 1:
                    continue
                lead_len = len(line_text) - len(line_text.lstrip()) if line_text.strip() else 0
                line_cursor = QTextCursor(blk)
                line_cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, lead_len)
                line_cursor.insertText("-- ")
        edit_cursor.endEditBlock()

    def _indent_selection(self, cursor: QTextCursor, unindent: bool) -> None:
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.document()
        first_block = doc.findBlock(start).blockNumber()
        last_block_obj = doc.findBlock(end)
        last_block = last_block_obj.blockNumber()
        if end > start and last_block_obj.position() == end:
            last_block = max(first_block, last_block - 1)

        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()
        for blk_num in range(first_block, last_block + 1):
            blk = doc.findBlockByNumber(blk_num)
            if not blk.isValid():
                continue
            line_cursor = QTextCursor(blk)
            if unindent:
                line_text = blk.text()
                remove_count = 0
                if line_text.startswith("\t"):
                    remove_count = 1
                else:
                    while remove_count < 4 and remove_count < len(line_text) and line_text[remove_count] == " ":
                        remove_count += 1
                if remove_count > 0:
                    line_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, remove_count)
                    line_cursor.removeSelectedText()
            else:
                line_cursor.insertText("    ")
        edit_cursor.endEditBlock()


class SqlEditorPanel(QWidget):
    """Resizable panel displaying syntax-highlighted SQL for the selected QueryNode."""

    MINIMUM_WIDTH = 220
    DEFAULT_WIDTH = 380
    VALIDATION_DEBOUNCE_MS = 450
    AUTO_SAVE_DEBOUNCE_MS = 1500

    save_requested = Signal(str, str)  # (query_name, sql_text)
    auto_save_requested = Signal(str, str)  # (query_name, sql_text)
    dry_run_requested = Signal(str, str)  # (query_name, sql_text)
    run_requested = Signal(str)  # (query_name)
    external_reload_requested = Signal(str)  # (query_name)
    external_ignore_requested = Signal(str)  # (query_name)
    external_overwrite_requested = Signal(str, str)  # (query_name, sql_text)
    close_requested = Signal()
    dirty_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_query_name: Optional[str] = None
        self._saved_sql_text: str = ""
        self._is_dirty: bool = False
        self._loading_text: bool = False
        self._auto_save_enabled: bool = False
        self._has_external_conflict: bool = False
        self._dry_run_in_progress: bool = False
        self._run_in_progress: bool = False
        self._syntax_errors: List[SqlSyntaxErrorInfo] = []

        self.setMinimumWidth(self.MINIMUM_WIDTH)
        self._build_ui()

        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(self.VALIDATION_DEBOUNCE_MS)
        self._validation_timer.timeout.connect(self.run_syntax_check)

        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(self.AUTO_SAVE_DEBOUNCE_MS)
        self._auto_save_timer.timeout.connect(self._on_auto_save_timeout)

        self.clear_query()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._stack = QStackedWidget(self)
        outer_layout.addWidget(self._stack)

        # Page 0: SQL Code Editor
        self._sql_page = QWidget(self)
        layout = QVBoxLayout(self._sql_page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header Row: Query title (left) + Save button + Close button (right)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(2, 0, 0, 2)
        header_row.setSpacing(6)

        self.title_label = QLabel("No Query Selected", self._sql_page)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.title_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #f3f4f6;")
        header_row.addWidget(self.title_label, 1)

        self.save_btn = QPushButton("💾 Save", self._sql_page)
        self.save_btn.setFixedHeight(24)
        self.save_btn.setToolTip("Save SQL File (Ctrl+S)")
        self.save_btn.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 2px 8px; }"
            "QPushButton:enabled { background-color: #2b78e4; color: white; }"
        )
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.request_save)
        header_row.addWidget(self.save_btn)

        self.close_btn = QPushButton("☰", self._sql_page)
        self.close_btn.setFixedSize(26, 24)
        self.close_btn.setToolTip("SQL Editor / Results Preview")
        self.close_btn.setStyleSheet("QPushButton { font-size: 14px; padding: 0px; }")
        self.close_btn.clicked.connect(self.close_requested.emit)
        header_row.addWidget(self.close_btn)

        layout.addLayout(header_row)

        sep = QFrame(self._sql_page)
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #374151;")
        layout.addWidget(sep)

        # External file modification banner (hidden until disk conflict occurs while dirty)
        self.external_change_banner = QFrame(self._sql_page)
        self.external_change_banner.setObjectName("externalChangeBanner")
        self.external_change_banner.setVisible(False)
        self.external_change_banner.setStyleSheet(
            "QFrame#externalChangeBanner {"
            "  background-color: #1F384E;"
            "  border: 1px solid #2e5272;"
            "  border-radius: 3px;"
            "}"
        )
        banner_layout = QHBoxLayout(self.external_change_banner)
        banner_layout.setContentsMargins(8, 5, 8, 5)
        banner_layout.setSpacing(6)

        self.external_change_label = QLabel(
            "The current file was modified on disk",
            self.external_change_banner,
        )
        self.external_change_label.setWordWrap(True)
        self.external_change_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.external_change_label.setStyleSheet(
            "color: #f3f4f6; font-size: 11px; font-weight: bold; background: transparent;"
        )
        banner_layout.addWidget(self.external_change_label, 1)

        self.external_reload_btn = QPushButton("Reload", self.external_change_banner)
        self.external_reload_btn.setFixedHeight(22)
        self.external_reload_btn.setToolTip("Discard unsaved edits and reload the file from disk")
        self.external_reload_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; font-size: 11px; font-weight: bold; padding: 1px 8px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        self.external_reload_btn.clicked.connect(self._on_external_reload_clicked)
        banner_layout.addWidget(self.external_reload_btn)

        self.external_secondary_btn = QPushButton("Ignore", self.external_change_banner)
        self.external_secondary_btn.setFixedHeight(22)
        self.external_secondary_btn.setStyleSheet(
            "QPushButton { background-color: #334e68; color: #e5e7eb; font-size: 11px; font-weight: bold; padding: 1px 8px; border: 1px solid #486581; border-radius: 3px; }"
            "QPushButton:hover { background-color: #3e5c7a; }"
        )
        self.external_secondary_btn.clicked.connect(self._on_external_secondary_clicked)
        banner_layout.addWidget(self.external_secondary_btn)

        layout.addWidget(self.external_change_banner)

        # SQL Code Editor + Syntax Highlighter
        self.editor = SqlCodeEditor(self._sql_page)
        self.highlighter = SqlSyntaxHighlighter(self.editor.document())
        self.editor.textChanged.connect(self._on_editor_text_changed)
        layout.addWidget(self.editor, 1)

        # Syntax Status Footer + BigQuery Validate (Dry-Run) + Run Buttons
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(6)

        self.syntax_status_label = QLabel("", self._sql_page)
        self.syntax_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.syntax_status_label.setStyleSheet("font-size: 11px; color: #9ca3af; padding: 2px 4px;")
        footer_row.addWidget(self.syntax_status_label, 1)

        self.dry_run_btn = QPushButton("Validate", self._sql_page)
        self.dry_run_btn.setFixedHeight(22)
        self.dry_run_btn.setToolTip("Validate query with BigQuery Native Dry-Run")
        self.dry_run_btn.setStyleSheet(
            "QPushButton { font-size: 11px; font-weight: bold; padding: 1px 8px; }"
            "QPushButton:enabled { background-color: #2563eb; color: white; border-radius: 3px; }"
        )
        self.dry_run_btn.setEnabled(False)
        self.dry_run_btn.clicked.connect(self.request_dry_run)
        footer_row.addWidget(self.dry_run_btn)

        self.run_btn = QPushButton("Run", self._sql_page)
        self.run_btn.setFixedHeight(22)
        self.run_btn.setToolTip("Run the currently selected query node (F5)")
        self.run_btn.setStyleSheet(
            "QPushButton { font-size: 11px; font-weight: bold; padding: 1px 8px; }"
            "QPushButton:enabled { background-color: #16a34a; color: white; border-radius: 3px; }"
            "QPushButton:enabled:hover { background-color: #15803d; }"
        )
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.request_run)
        footer_row.addWidget(self.run_btn)

        layout.addLayout(footer_row)
        self._stack.addWidget(self._sql_page)

        # Page 1: Results Preview
        self.results_preview = ResultsPreviewWidget(parent=self)
        self.results_preview.close_requested.connect(self.close_requested.emit)
        self._stack.addWidget(self.results_preview)
        self._stack.setCurrentWidget(self._sql_page)

    def _update_header(self) -> None:
        if not self._current_query_name:
            self.title_label.setText("No Query Selected")
            self.title_label.setToolTip("Select a single Query node on the canvas to view its SQL.")
            self.save_btn.setEnabled(False)
            return

        suffix = " *" if self._is_dirty else ""
        title_text = f"{self._current_query_name}.sql{suffix}"
        self.title_label.setText(title_text)
        self.title_label.setToolTip(title_text)
        self.save_btn.setEnabled(self._is_dirty)

    def _update_action_buttons(self) -> None:
        has_code = bool(self._current_query_name and self.editor.toPlainText().strip())
        if hasattr(self, "dry_run_btn"):
            self.dry_run_btn.setEnabled(has_code and not self._dry_run_in_progress)
        if hasattr(self, "run_btn"):
            self.run_btn.setEnabled(has_code and not self._run_in_progress)

    def _update_conflict_banner_buttons(self) -> None:
        if not hasattr(self, "external_secondary_btn"):
            return
        if self._auto_save_enabled:
            self.external_secondary_btn.setText("Overwrite")
            self.external_secondary_btn.setToolTip(
                "Overwrite the external changes on disk with your editor changes"
            )
        else:
            self.external_secondary_btn.setText("Ignore")
            self.external_secondary_btn.setToolTip(
                "Dismiss this notice and keep your unsaved editor edits"
            )

    def set_auto_save_enabled(self, enabled: bool) -> None:
        """Synchronize SQL editor auto-save state with the Process Flow Editor setting."""
        self._auto_save_enabled = bool(enabled)
        if hasattr(self, "save_btn"):
            self.save_btn.setVisible(not self._auto_save_enabled)
        self._update_conflict_banner_buttons()
        if self._auto_save_enabled:
            if self._is_dirty and not self._has_external_conflict and self._current_query_name:
                self._auto_save_timer.start()
        else:
            self.stop_auto_save_timer()

    def is_auto_save_enabled(self) -> bool:
        return self._auto_save_enabled

    def set_external_modification_detected(self, detected: bool) -> None:
        """Show or hide the external file modification conflict bar (#1F384E)."""
        self._has_external_conflict = bool(detected)
        self._update_conflict_banner_buttons()
        if hasattr(self, "external_change_banner"):
            self.external_change_banner.setVisible(self._has_external_conflict)
        if self._has_external_conflict:
            self.stop_auto_save_timer()

    def has_external_conflict(self) -> bool:
        return self._has_external_conflict

    def stop_auto_save_timer(self) -> None:
        if hasattr(self, "_auto_save_timer") and self._auto_save_timer.isActive():
            self._auto_save_timer.stop()

    def _on_auto_save_timeout(self) -> None:
        if (
            self._auto_save_enabled
            and self._is_dirty
            and not self._has_external_conflict
            and self._current_query_name
        ):
            self.auto_save_requested.emit(self._current_query_name, self.editor.toPlainText())

    def _on_external_reload_clicked(self) -> None:
        if self._current_query_name:
            self.external_reload_requested.emit(self._current_query_name)

    def _on_external_secondary_clicked(self) -> None:
        if not self._current_query_name:
            return
        if self._auto_save_enabled:
            self.external_overwrite_requested.emit(self._current_query_name, self.editor.toPlainText())
        else:
            self.external_ignore_requested.emit(self._current_query_name)

    def _on_editor_text_changed(self) -> None:
        if self._loading_text or not self._current_query_name:
            return

        new_dirty = self.editor.toPlainText() != self._saved_sql_text
        if new_dirty != self._is_dirty:
            self._is_dirty = new_dirty
            self.dirty_changed.emit(self._is_dirty)
        self._update_header()
        self._update_action_buttons()
        self._validation_timer.start()

        if self._auto_save_enabled and self._is_dirty and not self._has_external_conflict:
            self._auto_save_timer.start()
        elif not self._is_dirty:
            self.stop_auto_save_timer()

    def load_query(self, query_name: str, sql_text: str) -> None:
        """Load SQL text for the selected QueryNode into the editor."""
        self._stack.setCurrentWidget(self._sql_page)
        self._validation_timer.stop()
        self.stop_auto_save_timer()
        self._loading_text = True
        try:
            was_dirty = self._is_dirty
            self._current_query_name = query_name
            self._saved_sql_text = sql_text or ""
            self._is_dirty = False
            self.editor.setReadOnly(False)
            self.editor.setPlainText(self._saved_sql_text)
        finally:
            self._loading_text = False

        self.set_external_modification_detected(False)
        if was_dirty:
            self.dirty_changed.emit(False)
        self._update_header()
        self._update_action_buttons()
        self.run_syntax_check()

    def clear_query(self) -> None:
        """Reset the panel to an empty read-only state when no single query or table node is selected."""
        self._stack.setCurrentWidget(self._sql_page)
        self._validation_timer.stop()
        self.stop_auto_save_timer()
        self._loading_text = True
        try:
            was_dirty = self._is_dirty
            self._current_query_name = None
            self._saved_sql_text = ""
            self._is_dirty = False
            self._syntax_errors = []
            self.editor.clear()
            self.editor.setReadOnly(True)
            self.editor.set_error_lines({})
        finally:
            self._loading_text = False

        self.set_external_modification_detected(False)
        if was_dirty:
            self.dirty_changed.emit(False)
        self._update_header()
        self._update_action_buttons()
        self.syntax_status_label.setText("")
        self.syntax_status_label.setToolTip("")
        self.syntax_status_label.setStyleSheet("font-size: 11px; color: #9ca3af; padding: 2px 4px;")

    def show_table_box_preview(self, spec: TableBoxPreviewSpec) -> None:
        """Switch panel to Results Preview mode for the selected TableBoxNode."""
        self._validation_timer.stop()
        self.stop_auto_save_timer()
        self._loading_text = True
        try:
            was_dirty = self._is_dirty
            self._current_query_name = None
            self._saved_sql_text = ""
            self._is_dirty = False
            self._syntax_errors = []
            self.editor.clear()
            self.editor.setReadOnly(True)
            self.editor.set_error_lines({})
        finally:
            self._loading_text = False

        self.set_external_modification_detected(False)
        if was_dirty:
            self.dirty_changed.emit(False)
        self._update_header()
        self._update_action_buttons()
        self.syntax_status_label.setText("")
        self.syntax_status_label.setToolTip("")

        self.results_preview.show_table_box(spec)
        self._stack.setCurrentWidget(self.results_preview)

    def is_showing_results_preview(self) -> bool:
        return hasattr(self, "_stack") and hasattr(self, "results_preview") and (
            self._stack.currentWidget() is self.results_preview
        )

    def set_preview_cache(
        self,
        cache: Any,
        row_limit_getter: Optional[Callable[[], int]] = None,
    ) -> None:
        if hasattr(self, "results_preview"):
            self.results_preview.set_cache(cache, row_limit_getter=row_limit_getter)

    def shutdown(self) -> None:
        """Stop validation/auto-save timers and any background preview fetch workers cleanly."""
        if self._validation_timer.isActive():
            self._validation_timer.stop()
        self.stop_auto_save_timer()
        if hasattr(self, "results_preview"):
            self.results_preview.shutdown()

    def rename_current_query(self, new_query_name: str) -> None:
        """Update the displayed query title after the active query is renamed."""
        if self._current_query_name:
            self._current_query_name = new_query_name
            self._update_header()

    def mark_saved(self, new_saved_text: Optional[str] = None) -> None:
        """Mark the current editor contents as saved on disk."""
        self.stop_auto_save_timer()
        if new_saved_text is not None:
            if new_saved_text != self.editor.toPlainText():
                old_pos = self.editor.textCursor().position()
                self._loading_text = True
                try:
                    self.editor.setPlainText(new_saved_text)
                    new_cursor = self.editor.textCursor()
                    new_cursor.setPosition(min(old_pos, len(new_saved_text)))
                    self.editor.setTextCursor(new_cursor)
                finally:
                    self._loading_text = False
            self._saved_sql_text = new_saved_text
        else:
            self._saved_sql_text = self.editor.toPlainText()

        self.set_external_modification_detected(False)
        was_dirty = self._is_dirty
        self._is_dirty = False
        if was_dirty:
            self.dirty_changed.emit(False)
        self._update_header()
        self._update_action_buttons()
        self.run_syntax_check()

    def run_syntax_check(self) -> List[SqlSyntaxErrorInfo]:
        """Validate current SQL contents using sqlglot and update error markers."""
        if not self._current_query_name:
            self._syntax_errors = []
            self.editor.set_error_lines({})
            return []

        sql_text = self.editor.toPlainText()
        errors = validate_bigquery_sql(sql_text)
        self._syntax_errors = errors

        if not sql_text.strip():
            self.editor.set_error_lines({})
            self.syntax_status_label.setText("")
            self.syntax_status_label.setToolTip("")
            self.syntax_status_label.setStyleSheet("font-size: 11px; color: #9ca3af; padding: 2px 4px;")
            return []

        if errors:
            error_map: Dict[int, str] = {}
            for err in errors:
                if err.line in error_map:
                    error_map[err.line] += f"\nCol {err.col}: {err.message}"
                else:
                    error_map[err.line] = f"Line {err.line}, Col {err.col}: {err.message}"
            self.editor.set_error_lines(error_map)

            first = errors[0]
            summary = f"⚠ Line {first.line}, Col {first.col}: {first.message}"
            if len(errors) > 1:
                summary += f" (+{len(errors) - 1} more)"
            self.syntax_status_label.setText(summary)
            self.syntax_status_label.setToolTip("\n".join(f"Line {e.line}, Col {e.col}: {e.message}" for e in errors))
            self.syntax_status_label.setStyleSheet("font-size: 11px; color: #f87171; padding: 2px 4px;")
        else:
            # Only show sqlglot validation text when validation fails
            self.editor.set_error_lines({})
            self.syntax_status_label.setText("")
            self.syntax_status_label.setToolTip("")
            self.syntax_status_label.setStyleSheet("font-size: 11px; color: #9ca3af; padding: 2px 4px;")

        return list(errors)

    def current_query_name(self) -> Optional[str]:
        return self._current_query_name

    def current_sql_text(self) -> str:
        return self.editor.toPlainText()

    def is_dirty(self) -> bool:
        return self._is_dirty

    def syntax_errors(self) -> List[SqlSyntaxErrorInfo]:
        return list(self._syntax_errors)

    def request_save(self) -> None:
        if self._current_query_name:
            self.stop_auto_save_timer()
            self.save_requested.emit(self._current_query_name, self.editor.toPlainText())

    def request_dry_run(self) -> None:
        if self._current_query_name and self.editor.toPlainText().strip():
            self.dry_run_requested.emit(self._current_query_name, self.editor.toPlainText())

    def request_run(self) -> None:
        if self._current_query_name and not self._run_in_progress:
            self.run_requested.emit(self._current_query_name)

    def set_dry_run_in_progress(self, in_progress: bool) -> None:
        self._dry_run_in_progress = bool(in_progress)
        if hasattr(self, "dry_run_btn"):
            self.dry_run_btn.setText("⏳ Validating..." if self._dry_run_in_progress else "Validate")
        self._update_action_buttons()
        if self._dry_run_in_progress:
            self.syntax_status_label.setText("Validating with BigQuery...")
            self.syntax_status_label.setStyleSheet("font-size: 11px; color: #93c5fd; padding: 2px 4px;")

    def set_run_in_progress(self, in_progress: bool) -> None:
        self._run_in_progress = bool(in_progress)
        if hasattr(self, "run_btn"):
            self.run_btn.setText("⏳ Running..." if self._run_in_progress else "Run")
        self._update_action_buttons()

    def show_dry_run_result(self, result: dict) -> None:
        """Display BigQuery native dry-run result and highlight reported error line if any."""
        if not result:
            return
        if result.get("valid"):
            self.editor.set_error_lines({})
            msg = result.get("message") or "✓ Valid"
            proj = result.get("project_id") or ""
            bytes_str = result.get("formatted_bytes") or "0 B"
            self.syntax_status_label.setText(msg)
            self.syntax_status_label.setToolTip(
                f"BigQuery validation succeeded (Project: {proj}, Estimated bytes processed: {bytes_str})"
            )
            self.syntax_status_label.setStyleSheet("font-size: 11px; color: #34d399; padding: 2px 4px;")
            return

        err_msg = (result.get("error_message") or "BigQuery validation failed").strip()
        full_err = (result.get("full_error") or err_msg).strip()
        line = result.get("line")
        col = result.get("col") or 1
        if line:
            self.editor.set_error_lines({int(line): err_msg})
            self.syntax_status_label.setText(f"⚠ BQ Line {line}, Col {col}: {err_msg}")
        else:
            self.syntax_status_label.setText(f"⚠ BQ Error: {err_msg}")
        self.syntax_status_label.setToolTip(full_err)
        self.syntax_status_label.setStyleSheet("font-size: 11px; color: #f87171; padding: 2px 4px;")

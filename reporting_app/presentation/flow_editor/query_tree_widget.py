"""Right panel widget for managing queries and initiating drag-and-drop into canvas."""

from typing import List, Optional
from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)


class _NoTextItemDelegate(QStyledItemDelegate):
    """Item delegate that suppresses default QListWidgetItem text painting behind custom row widgets."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""



_INLINE_MENU_BTN_STYLE = """
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


class DraggableQueryList(QListWidget):
    """List widget supporting drag of queries onto the NodeGraph canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self._item_delegate = _NoTextItemDelegate(self)
        self.setItemDelegate(self._item_delegate)
        self._drag_start_pos: Optional[QPoint] = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return

        from PySide6.QtWidgets import QApplication
        start_dist = QApplication.startDragDistance() if QApplication.instance() else 6
        distance = (event.pos() - self._drag_start_pos).manhattanLength()
        if distance < start_dist:
            super().mouseMoveEvent(event)
            return

        item = self.itemAt(self._drag_start_pos) or self.currentItem()
        if not item:
            super().mouseMoveEvent(event)
            return

        query_name = (item.data(Qt.UserRole) or item.text() or "").strip()
        self._drag_start_pos = None

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"query:{query_name}")
        mime.setData("text/plain", f"query:{query_name}".encode("utf-8"))
        mime.setData("application/x-query-name", query_name.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class QueryRowWidget(QWidget):
    """Custom widget for a query list item with label and right-aligned '⋯' action button."""

    def __init__(self, query_name: str, on_menu, parent=None):
        super().__init__(parent)
        self.query_name = query_name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(4)

        self.name_lbl = QLabel(query_name, self)
        self.name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name_lbl.setToolTip(query_name)
        self.name_lbl.setStyleSheet("color: #e5e7eb; background: transparent;")
        self.name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.name_lbl, 1)

        self.menu_btn = QPushButton("⋯", self)
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setToolTip("Query Options (Open / Run / Rename / Remove)")
        self.menu_btn.setStyleSheet(_INLINE_MENU_BTN_STYLE)
        self.menu_btn.clicked.connect(
            lambda: on_menu(
                self.query_name,
                self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height())),
            )
        )
        layout.addWidget(self.menu_btn)


class QueryManagementPanel(QWidget):
    """Right collapsible panel for managing report queries (Add, Open, Run, Rename, Remove, Drag)."""

    EXPANDED_MIN_WIDTH = 180
    COLLAPSED_WIDTH = 32
    DEFAULT_EXPANDED_WIDTH = 240

    query_added = Signal(str)
    query_removed = Signal(str)
    query_renamed = Signal(str, str)
    query_double_clicked = Signal(str)
    query_run_requested = Signal(str)
    collapse_toggled = Signal(bool)  # is_collapsed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_collapsed = False
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
        layout = QVBoxLayout(self.expanded_widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Top Header Bar: '|>' Collapse button (left), 'Queries' title, '+' Add Query (right)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 2)
        header_row.setSpacing(6)

        self.collapse_btn = QPushButton("|>", self.expanded_widget)
        self.collapse_btn.setFixedSize(28, 26)
        self.collapse_btn.setToolTip("Collapse Queries Panel")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(True, emit_signal=True))
        header_row.addWidget(self.collapse_btn)

        self.title_label = QLabel("<b>Queries</b>", self.expanded_widget)
        self.title_label.setStyleSheet("font-size: 13px; color: #f3f4f6;")
        header_row.addWidget(self.title_label, 1)

        self.add_btn = QPushButton("+", self.expanded_widget)
        self.add_btn.setFixedSize(24, 24)
        self.add_btn.setToolTip("Add Query")
        self.add_btn.setStyleSheet("QPushButton { font-weight: bold; font-size: 14px; padding: 0px; }")
        self.add_btn.clicked.connect(self._on_add)
        header_row.addWidget(self.add_btn)

        layout.addLayout(header_row)

        sep = QFrame(self.expanded_widget)
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #374151;")
        layout.addWidget(sep)

        # Draggable Query List
        self.list_widget = DraggableQueryList(self.expanded_widget)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.setStyleSheet(
            "QListWidget { border: 1px solid #2d3138; background-color: #1c1e22; outline: 0; }"
            "QListWidget::item { min-height: 26px; }"
            "QListWidget::item:selected { background-color: rgba(43, 120, 228, 0.28); }"
        )
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemDoubleClicked.connect(
            lambda item: self.query_double_clicked.emit(item.data(Qt.UserRole) or item.text())
        )
        layout.addWidget(self.list_widget, 1)

        outer_layout.addWidget(self.expanded_widget)

        # ---------------------------------------------------------------------
        # Collapsed Rail Container
        # ---------------------------------------------------------------------
        self.collapsed_widget = QWidget(self)
        col_layout = QVBoxLayout(self.collapsed_widget)
        col_layout.setContentsMargins(2, 6, 2, 6)
        col_layout.setSpacing(4)

        self.expand_btn = QPushButton("<|", self.collapsed_widget)
        self.expand_btn.setFixedSize(28, 26)
        self.expand_btn.setToolTip("Expand Queries Panel")
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

    def _open_query_menu(self, query_name: str, global_pos: QPoint) -> None:
        menu = QMenu(self)
        open_act = menu.addAction("Open / Edit")
        open_act.triggered.connect(lambda: self.query_double_clicked.emit(query_name))

        run_act = menu.addAction("Run Query")
        run_act.triggered.connect(lambda: self.query_run_requested.emit(query_name))

        menu.addSeparator()

        rename_act = menu.addAction("Rename")
        rename_act.triggered.connect(lambda: self._on_rename(query_name))

        remove_act = menu.addAction("Remove")
        remove_act.triggered.connect(lambda: self._on_remove(query_name))

        self._exec_context_menu(menu, global_pos)

    def _show_context_menu(self, pos: QPoint) -> None:
        """Show context menu on right-click."""
        item = self.list_widget.itemAt(pos)
        global_pos = self.list_widget.mapToGlobal(pos)
        if item:
            self.list_widget.setCurrentItem(item)
            qname = item.data(Qt.UserRole) or item.text()
            self._open_query_menu(qname, global_pos)
            return

        menu = QMenu(self)
        add_action = menu.addAction("Add Query")
        add_action.triggered.connect(self._on_add)
        self._exec_context_menu(menu, global_pos)

    def _exec_context_menu(self, menu: QMenu, global_pos: QPoint) -> None:
        """Execute context menu."""
        menu.exec(global_pos)

    def clear_items(self) -> None:
        """Clear all query items and detach custom row widgets cleanly before teardown."""
        for idx in range(self.list_widget.count()):
            item = self.list_widget.item(idx)
            if item:
                self.list_widget.removeItemWidget(item)
        self.list_widget.clear()

    def set_queries(self, query_names: List[str]) -> None:
        """Populate the list of queries."""
        self.clear_items()
        for q in query_names:
            item = QListWidgetItem(q)
            item.setData(Qt.UserRole, q)
            item.setForeground(QColor(0, 0, 0, 0))
            item.setSizeHint(QSize(0, 26))
            self.list_widget.addItem(item)

            row_widget = QueryRowWidget(q, on_menu=self._open_query_menu, parent=None)
            self.list_widget.setItemWidget(item, row_widget)

    def get_selected_query(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        return (item.data(Qt.UserRole) or item.text()) if item else None

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Add Query",
            "Enter query name:",
        )
        if ok and name.strip():
            self.query_added.emit(name.strip())

    def _on_rename(self, target_name: Optional[str] = None) -> None:
        current = target_name or self.get_selected_query()
        if not current:
            QMessageBox.information(self, "Rename Query", "Please select a query to rename.")
            return

        new_name, ok = QInputDialog.getText(
            self,
            "Rename Query",
            f"Enter new name for '{current}':",
            text=current,
        )
        if ok and new_name.strip() and new_name.strip() != current:
            self.query_renamed.emit(current, new_name.strip())

    def _on_remove(self, target_name: Optional[str] = None) -> None:
        current = target_name or self.get_selected_query()
        if not current:
            QMessageBox.information(self, "Remove Query", "Please select a query to remove.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove query '{current}'?\nThis will delete the SQL file.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.query_removed.emit(current)

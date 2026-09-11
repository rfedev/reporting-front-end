"""Left panel widget for managing queries and initiating drag-and-drop into canvas."""

from typing import List, Optional
from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DraggableQueryList(QListWidget):
    """List widget supporting drag of queries onto the NodeGraph canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self._drag_start_pos: Optional[QPoint] = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            parent_panel = self.parent()
            while parent_panel and not hasattr(parent_panel, "_on_remove"):
                parent_panel = parent_panel.parent()
            if parent_panel and hasattr(parent_panel, "_on_remove"):
                parent_panel._on_remove()
                event.accept()
                return
        super().keyPressEvent(event)

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

        query_name = item.text().strip()
        self._drag_start_pos = None

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"query:{query_name}")
        mime.setData("text/plain", f"query:{query_name}".encode("utf-8"))
        mime.setData("application/x-query-name", query_name.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class QueryManagementPanel(QWidget):
    """Left collapsible panel for managing report queries (Add, Rename, Delete, Drag)."""

    query_added = Signal(str)
    query_removed = Signal(str)
    query_renamed = Signal(str, str)
    query_double_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Query list
        self.list_widget = DraggableQueryList(self)
        self.list_widget.itemDoubleClicked.connect(
            lambda item: self.query_double_clicked.emit(item.text())
        )
        layout.addWidget(self.list_widget)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add")
        self.add_btn.setToolTip("Create a new query for this report")
        self.add_btn.clicked.connect(self._on_add)

        self.rename_btn = QPushButton("Rename")
        self.rename_btn.setToolTip("Rename the selected query")
        self.rename_btn.clicked.connect(self._on_rename)

        self.remove_btn = QPushButton("Delete")
        self.remove_btn.setToolTip("Delete the selected query")
        self.remove_btn.clicked.connect(self._on_remove)

        # Shortcuts on query list
        self.del_shortcut = QShortcut(QKeySequence.Delete, self.list_widget)
        self.del_shortcut.activated.connect(self._on_remove)
        self.backspace_shortcut = QShortcut(QKeySequence(Qt.Key_Backspace), self.list_widget)
        self.backspace_shortcut.activated.connect(self._on_remove)

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.rename_btn)
        btn_layout.addWidget(self.remove_btn)
        layout.addLayout(btn_layout)

    def set_queries(self, query_names: List[str]) -> None:
        """Populate the list of queries."""
        self.list_widget.clear()
        for q in query_names:
            item = QListWidgetItem(q)
            self.list_widget.addItem(item)

    def get_selected_query(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        return item.text() if item else None

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Add Query",
            "Enter new query name (without .sql extension):",
        )
        if ok and name.strip():
            self.query_added.emit(name.strip())

    def _on_rename(self) -> None:
        current = self.get_selected_query()
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

    def _on_remove(self) -> None:
        current = self.get_selected_query()
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

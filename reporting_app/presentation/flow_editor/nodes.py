"""Custom NodeGraphQt nodes for queries and table lists."""
from pathlib import Path
from typing import List, Optional, Tuple
from PySide6 import QtCore, QtGui, QtWidgets
from NodeGraphQt import BaseNode
from NodeGraphQt.constants import NodeEnum
from NodeGraphQt.qgraphics.node_base import NodeItem
from NodeGraphQt.qgraphics.pipe import PipeItem

# Visual Palette
COLOR_GREEN = (35, 115, 70, 255)         # Input table box & noodle
COLOR_BLUE = (45, 80, 130, 255)          # Query node box & noodle
COLOR_DARK_ORANGE = (180, 85, 20, 255)   # Output table box & noodle
COLOR_DARK_PURPLE = (110, 45, 130, 255)  # Output CSV box & noodle


class CustomNodeTextItem(QtWidgets.QGraphicsTextItem):
    """Clean, standard text item for node title editing that behaves like a normal single-line text box."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._locked = False
        self.setTabChangesFocus(True)
        self.document().setDocumentMargin(2)
        font = QtGui.QFont("sans-serif", 9, QtGui.QFont.Bold)
        self.setFont(font)
        self.setDefaultTextColor(QtGui.QColor(240, 240, 240))
        self.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)

    def mouseDoubleClickEvent(self, event):
        if not self._locked and event.button() == QtCore.Qt.LeftButton:
            self.set_editable(True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.set_editable(False)
            event.accept()
            return
        elif event.key() == QtCore.Qt.Key_Escape:
            parent_node = getattr(self, "node", None) or self.parentItem()
            if parent_node:
                orig_name = getattr(parent_node, "name", "")
                self.setPlainText(orig_name)
            self.set_editable(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.set_editable(False)
        super().focusOutEvent(event)

    def set_editable(self, value=False):
        if self._locked:
            return
        if value:
            self.setTextInteractionFlags(
                QtCore.Qt.TextEditable |
                QtCore.Qt.TextSelectableByMouse |
                QtCore.Qt.TextSelectableByKeyboard
            )
            self.setFocus(QtCore.Qt.MouseFocusReason)
            # Select all text on enter edit mode like a normal text box
            cursor = self.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            self.setTextCursor(cursor)
        else:
            self.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
            cursor = self.textCursor()
            cursor.clearSelection()
            self.setTextCursor(cursor)
            # Commit new text
            new_text = self.toPlainText().strip()
            parent_node = getattr(self, "node", None) or self.parentItem()
            if parent_node and hasattr(parent_node, "viewer") and parent_node.viewer():
                old_name = getattr(parent_node, "name", "")
                if new_text and new_text != old_name:
                    parent_node.viewer().node_name_changed.emit(parent_node.id, new_text)
                elif not new_text:
                    self.setPlainText(old_name)

    @property
    def node(self):
        return self.parentItem()


class CircularInfoButtonItem(QtWidgets.QGraphicsItem):
    """Circular info (ℹ) button with hover highlight and crisp styling."""

    def __init__(self, parent=None, callback=None):
        super().__init__(parent)
        self.callback = callback
        self._hovered = False
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("View Execution Log")

    def boundingRect(self):
        return QtCore.QRectF(-9, -9, 18, 18)

    def paint(self, painter, option, widget):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = QtCore.QRectF(-8, -8, 16, 16)
        if self._hovered:
            bg_brush = QtGui.QBrush(QtGui.QColor(43, 120, 228, 230))
            border_pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 220), 1.2)
            text_color = QtGui.QColor(255, 255, 255)
        else:
            bg_brush = QtGui.QBrush(QtGui.QColor(30, 45, 70, 180))
            border_pen = QtGui.QPen(QtGui.QColor(140, 180, 230, 180), 1.0)
            text_color = QtGui.QColor(180, 215, 255)

        painter.setBrush(bg_brush)
        painter.setPen(border_pen)
        painter.drawEllipse(rect)

        # Draw "i" in center
        font = QtGui.QFont("serif", 9, QtGui.QFont.Bold)
        font.setItalic(True)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(rect, QtCore.Qt.AlignCenter, "i")

        painter.restore()

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            if callable(self.callback):
                self.callback()
            event.accept()
            return
        super().mousePressEvent(event)


class TableBoxItem(NodeItem):

    """Custom graphics item for TableBoxNode with arrow expand/collapse and native text rendering."""

    def __init__(self, name="Table Box", parent=None):
        super().__init__(name, parent)
        self.collapsed = False
        self.table_lines: List[str] = []
        self.table_box_type: str = "Input Tables"
        self.custom_title: Optional[str] = None

        # Arrow icon in node header
        self._arrow_item = QtWidgets.QGraphicsSimpleTextItem("▼", self)
        self._arrow_item.setBrush(QtGui.QBrush(QtGui.QColor(220, 220, 220)))
        font = QtGui.QFont("sans-serif", 9, QtGui.QFont.Bold)
        self._arrow_item.setFont(font)

    def paint(self, painter, option, widget):
        if not self.viewer():
            return
        super().paint(painter, option, widget)


    def set_custom_title(self, title: str):
        self.custom_title = title
        if self._text_item:
            self._text_item.setPlainText(title)
        self.draw_node()

    def set_table_lines(self, lines: List[str]):
        self.table_lines = list(lines)
        self.draw_node()

    def _align_label_horizontal(self, h_offset, v_offset):
        if self.custom_title and self._text_item:
            self._text_item.setPlainText(self.custom_title)
        rect = self.boundingRect()
        text_rect = self._text_item.boundingRect()

        has_in = any(p.isVisible() for p in self.inputs)
        left_x = rect.left() + (22 if has_in else 12)
        y = rect.y() + v_offset + 3

        self._text_item.setPos(left_x, y)
        self._arrow_item.setPos(left_x + text_rect.width() + 6, y + 1)

    def _calc_size_horizontal(self):
        text_w = self._text_item.boundingRect().width() + self._arrow_item.boundingRect().width() + 24
        text_h = self._text_item.boundingRect().height()

        fm = QtGui.QFontMetrics(QtGui.QFont("monospace", 10))
        lines_w = max([fm.horizontalAdvance(line) for line in self.table_lines] or [60])
        body_w = lines_w + 24

        has_in = any(p.isVisible() for p in self.inputs)
        has_out = any(p.isVisible() for p in self.outputs)
        port_pad = (20.0 if has_in else 10.0) + (20.0 if has_out else 10.0)

        total_w = max(text_w, body_w) + port_pad
        if self.collapsed:
            total_h = text_h + 16.0
        else:
            line_h = fm.lineSpacing()
            count = len(self.table_lines) if self.table_lines else 1
            total_h = text_h + 12.0 + (count * line_h) + 12.0

        return max(total_w, 130.0), max(total_h, 42.0)

    def _paint_horizontal(self, painter, option, widget):
        super()._paint_horizontal(painter, option, widget)
        if self.collapsed:
            return

        painter.save()
        rect = self.boundingRect()
        text_rect = self._text_item.boundingRect()

        has_in = any(p.isVisible() for p in self.inputs)
        left_x = rect.left() + (22.0 if has_in else 12.0)
        y = rect.top() + text_rect.height() + 10.0

        font = QtGui.QFont("monospace", 10)
        painter.setFont(font)
        fm = QtGui.QFontMetrics(font)
        line_h = fm.lineSpacing()

        painter.setPen(QtGui.QColor(235, 235, 235))
        display_lines = self.table_lines if self.table_lines else ["• (None)"]
        for line in display_lines:
            painter.drawText(QtCore.QPointF(left_x, y + fm.ascent()), line)
            y += line_h

        painter.restore()

    def mousePressEvent(self, event):
        arrow_rect = self._arrow_item.boundingRect().translated(self._arrow_item.pos()).adjusted(-6, -6, 6, 6)
        if arrow_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return
        super().mousePressEvent(event)

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self._arrow_item.setText("▶" if self.collapsed else "▼")
        self.draw_node()


# Execution node title font lower-limit constants
EXEC_NODE_BASE_FONT_PT = 9.0
EXEC_NODE_MIN_FONT_PT = 4.7
EXEC_NODE_ZOOM_THRESHOLD = EXEC_NODE_MIN_FONT_PT / EXEC_NODE_BASE_FONT_PT  # ~0.522 (~52% zoom)


def get_node_lod(node_item: NodeItem, painter: Optional[QtGui.QPainter] = None) -> float:
    """Return the effective canvas zoom scale (1.0 = 100% zoom)."""
    if painter is not None:
        p_m11 = painter.worldTransform().m11()
        if p_m11 > 0:
            return float(p_m11)
    viewer = node_item.viewer() if node_item else None
    if viewer is not None:
        v_m11 = viewer.transform().m11()
        if v_m11 > 0:
            return float(v_m11)
    return 1.0


def wrap_node_name_lines(
    text: str,
    fm: QtGui.QFontMetricsF,
    max_line_width: float,
    max_lines: int = 2,
) -> List[str]:
    """Wrap a node name into 1 or 2 balanced centered lines for the zoomed-out overlay pill."""
    clean = (text or "").strip() or "Node"
    if max_lines <= 1 or fm.horizontalAdvance(clean) <= max_line_width:
        return [clean]

    mid = len(clean) / 2.0
    candidates = []
    for idx, ch in enumerate(clean[:-1]):
        if ch in (" ", "_", "-", "."):
            split_pos = idx + 1
            left = clean[:split_pos].rstrip()
            right = clean[split_pos:].lstrip()
            if left and right:
                candidates.append((abs(split_pos - mid), left, right))

    if candidates:
        fitting = [c for c in candidates if fm.horizontalAdvance(c[1]) <= max_line_width * 1.15]
        chosen = min(fitting, key=lambda c: c[0]) if fitting else min(candidates, key=lambda c: c[0])
        line1, line2 = chosen[1], chosen[2]
    else:
        split_idx = max(1, int(round(mid)))
        line1 = clean[:split_idx]
        line2 = clean[split_idx:]

    elide_w = max_line_width * 1.25
    if fm.horizontalAdvance(line1) > elide_w:
        line1 = fm.elidedText(line1, QtCore.Qt.ElideRight, elide_w)
    if fm.horizontalAdvance(line2) > elide_w:
        line2 = fm.elidedText(line2, QtCore.Qt.ElideRight, elide_w)
    return [line1, line2]


class NodeExecutionOverlayItem(QtWidgets.QGraphicsItem):
    """Non-interactive overlay graphics item that renders execution halos, elapsed time, and floating name pills on an execution node."""

    def __init__(self, parent_node_item: NodeItem):
        super().__init__(parent_node_item)
        self._node_item = parent_node_item
        self.exec_status: str = "IDLE"  # IDLE, PENDING, RUNNING, SUCCESS, FAILED, SKIPPED
        self.exec_elapsed_sec: float = 0.0
        self.exec_halo_visible: bool = False
        self.exec_dash_offset: float = 0.0
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
        self.setZValue(20.0)

    def shape(self) -> QtGui.QPainterPath:
        """Return empty shape so overlay never intercepts scene hit-testing outside node bounds."""
        return QtGui.QPainterPath()

    def set_state(
        self,
        status: Optional[str] = None,
        elapsed_sec: Optional[float] = None,
        visible: Optional[bool] = None,
    ) -> None:
        self.prepareGeometryChange()
        if status is not None:
            self.exec_status = status
        if elapsed_sec is not None:
            self.exec_elapsed_sec = max(0.0, float(elapsed_sec))
        if visible is not None:
            self.exec_halo_visible = bool(visible)
        self.update()

    def advance_phase(self, delta_offset: float = 1.2, elapsed_sec: Optional[float] = None) -> None:
        self.exec_dash_offset = (self.exec_dash_offset + delta_offset) % 1000.0
        if elapsed_sec is not None:
            self.exec_elapsed_sec = max(0.0, float(elapsed_sec))
        self.update()

    def boundingRect(self) -> QtCore.QRectF:
        w = getattr(self._node_item, "_width", 160.0)
        h = getattr(self._node_item, "_height", 70.0)
        return QtCore.QRectF(-120.0, -90.0, w + 240.0, h + 120.0)

    @staticmethod
    def format_mm_ss(seconds: float) -> str:
        total_sec = max(0, int(seconds))
        mins, secs = divmod(total_sec, 60)
        return f"{mins:02d}:{secs:02d}"

    def _paint_execution_halo(self, painter: QtGui.QPainter, w: float, h: float) -> None:
        """Render the outer status halo and top-right elapsed time badge."""
        painter.save()
        halo_rect = QtCore.QRectF(-1.5, -1.5, w + 3.0, h + 3.0)
        radius = 5.5
        status = self.exec_status.upper()

        if status == "RUNNING":
            glow_pen = QtGui.QPen(QtGui.QColor(175, 190, 210, 50), 6.0)
            glow_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(halo_rect, radius, radius)

            dash_pen = QtGui.QPen(QtGui.QColor(200, 208, 220, 245), 2.5)
            dash_pen.setCapStyle(QtCore.Qt.FlatCap)
            dash_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            dash_pen.setDashPattern([5.0, 4.0])
            dash_pen.setDashOffset(-self.exec_dash_offset)
            painter.setPen(dash_pen)
            painter.drawRoundedRect(halo_rect, radius, radius)

            badge_bg = QtGui.QColor(35, 42, 52, 235)
            badge_border = QtGui.QColor(180, 190, 205, 220)
            badge_text_color = QtGui.QColor(235, 240, 248)
        elif status == "SUCCESS":
            glow_pen = QtGui.QPen(QtGui.QColor(46, 204, 113, 75), 6.5)
            glow_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(halo_rect, radius, radius)

            solid_pen = QtGui.QPen(QtGui.QColor(46, 204, 113, 255), 2.5)
            solid_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(solid_pen)
            painter.drawRoundedRect(halo_rect, radius, radius)

            badge_bg = QtGui.QColor(22, 62, 40, 235)
            badge_border = QtGui.QColor(46, 204, 113, 230)
            badge_text_color = QtGui.QColor(215, 255, 230)
        elif status == "FAILED":
            glow_pen = QtGui.QPen(QtGui.QColor(231, 76, 60, 85), 6.5)
            glow_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(halo_rect, radius, radius)

            solid_pen = QtGui.QPen(QtGui.QColor(231, 76, 60, 255), 2.5)
            solid_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(solid_pen)
            painter.drawRoundedRect(halo_rect, radius, radius)

            badge_bg = QtGui.QColor(75, 24, 24, 235)
            badge_border = QtGui.QColor(231, 76, 60, 230)
            badge_text_color = QtGui.QColor(255, 220, 220)
        else:  # SKIPPED or PENDING
            glow_pen = QtGui.QPen(QtGui.QColor(135, 145, 160, 50), 5.0)
            glow_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(glow_pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(halo_rect, radius, radius)

            solid_pen = QtGui.QPen(QtGui.QColor(135, 145, 160, 215), 2.0)
            solid_pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(solid_pen)
            painter.drawRoundedRect(halo_rect, radius, radius)

            badge_bg = QtGui.QColor(42, 46, 54, 225)
            badge_border = QtGui.QColor(135, 145, 160, 190)
            badge_text_color = QtGui.QColor(195, 202, 212)

        time_str = self.format_mm_ss(self.exec_elapsed_sec)
        font = QtGui.QFont("monospace", 8, QtGui.QFont.Bold)
        painter.setFont(font)
        fm = QtGui.QFontMetrics(font)
        tw = fm.horizontalAdvance(time_str)
        badge_w = tw + 14.0
        badge_h = 18.0
        badge_rect = QtCore.QRectF(w - badge_w - 2.0, -badge_h - 4.0, badge_w, badge_h)

        painter.setBrush(QtGui.QBrush(badge_bg))
        painter.setPen(QtGui.QPen(badge_border, 1.2))
        painter.drawRoundedRect(badge_rect, 4.0, 4.0)

        painter.setPen(badge_text_color)
        painter.drawText(badge_rect, QtCore.Qt.AlignCenter, time_str)
        painter.restore()

    def _paint_zoomed_out_pill(self, painter: QtGui.QPainter, w: float, h: float) -> None:
        """Render a floating name pill badge directly above the node card with a fixed readable screen font size."""
        painter.save()
        lod = get_node_lod(self._node_item, painter)
        inv_scale = 1.0 / max(lod, 0.001)

        # Center horizontally at w / 2.0, anchored at top edge of the node y = 0.0
        painter.translate(w / 2.0, 0.0)
        painter.scale(inv_scale, inv_scale)

        font = QtGui.QFont("sans-serif")
        font.setPointSizeF(8.0)
        font.setBold(True)
        painter.setFont(font)
        fm = QtGui.QFontMetricsF(font)

        node_name = (getattr(self._node_item, "name", "") or "").strip() or "Node"
        display_name = fm.elidedText(node_name, QtCore.Qt.ElideRight, 260.0)

        text_w = fm.horizontalAdvance(display_name)
        text_h = fm.height()

        pad_x = 8.5
        pad_y = 3.5
        pill_w = text_w + pad_x * 2.0
        pill_h = text_h + pad_y * 2.0
        radius = 5.0

        # Position above the top of the node card
        has_halo = bool(self.exec_halo_visible and self.exec_status != "IDLE")
        gap_y = 12.0 if has_halo else 6.0
        y_top = -gap_y - pill_h
        pill_rect = QtCore.QRectF(-pill_w / 2.0, y_top, pill_w, pill_h)

        # Subtle drop shadow
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 110))
        painter.drawRoundedRect(pill_rect.translated(0.0, 1.5), radius, radius)

        # Pill background and border
        base_color = getattr(self._node_item, "color", (45, 80, 130, 255))
        r, g, b = int(base_color[0]), int(base_color[1]), int(base_color[2])
        pill_bg = QtGui.QColor(22, 26, 34, 240)

        if getattr(self._node_item, "selected", False):
            border_pen = QtGui.QPen(QtGui.QColor(254, 207, 42, 240), 1.3)
        else:
            border_color = QtGui.QColor(min(r + 65, 255), min(g + 65, 255), min(b + 65, 255), 210)
            border_pen = QtGui.QPen(border_color, 1.1)

        painter.setBrush(QtGui.QBrush(pill_bg))
        painter.setPen(border_pen)
        painter.drawRoundedRect(pill_rect, radius, radius)

        # Centered text
        painter.setPen(QtGui.QColor(242, 245, 250))
        painter.drawText(pill_rect, QtCore.Qt.AlignCenter, display_name)

        painter.restore()

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        if not self._node_item or not self._node_item.viewer():
            return

        has_exec_halo = bool(self.exec_halo_visible and self.exec_status != "IDLE")
        if not has_exec_halo:
            return

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        w = getattr(self._node_item, "_width", 160.0)
        h = getattr(self._node_item, "_height", 70.0)
        self._paint_execution_halo(painter, w, h)
        painter.restore()


class FloatingPillItem(QtWidgets.QGraphicsItem):
    """Top-level scene graphics item displaying execution node names at high Z-order above all nodes and pipes."""

    def __init__(self, node_item: "ExecutableNodeItem"):
        super().__init__()
        self._node_item = node_item
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
        self.setZValue(1000.0)

    def shape(self) -> QtGui.QPainterPath:
        return QtGui.QPainterPath()

    def boundingRect(self) -> QtCore.QRectF:
        node = self._node_item
        w = getattr(node, "_width", 160.0)
        # Ample bounding rect above the node to ensure redraws don't leave artifacts
        return QtCore.QRectF(-200.0, -100.0, w + 400.0, 100.0)

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        node = self._node_item
        if not node or not node.viewer():
            return
        if not node.is_zoomed_out_mode(painter):
            return

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        lod = get_node_lod(node, painter)
        inv_scale = 1.0 / max(lod, 0.001)

        w = getattr(node, "_width", 160.0)
        # Center horizontally at w / 2.0, anchored at top edge of the node y = 0.0
        painter.translate(w / 2.0, 0.0)
        painter.scale(inv_scale, inv_scale)

        font = QtGui.QFont("sans-serif")
        font.setPointSizeF(8.0)
        font.setBold(True)
        painter.setFont(font)
        fm = QtGui.QFontMetricsF(font)

        node_name = (getattr(node, "name", "") or "").strip() or "Node"
        display_name = fm.elidedText(node_name, QtCore.Qt.ElideRight, 260.0)

        text_w = fm.horizontalAdvance(display_name)
        text_h = fm.height()

        pad_x = 8.5
        pad_y = 3.5
        pill_w = text_w + pad_x * 2.0
        pill_h = text_h + pad_y * 2.0
        radius = 5.0

        # Position above the top of the node card
        overlay = getattr(node, "_exec_overlay", None)
        has_halo = bool(overlay and overlay.exec_halo_visible and overlay.exec_status != "IDLE")
        gap_y = 12.0 if has_halo else 6.0
        y_top = -gap_y - pill_h
        pill_rect = QtCore.QRectF(-pill_w / 2.0, y_top, pill_w, pill_h)

        # Subtle drop shadow
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 110))
        painter.drawRoundedRect(pill_rect.translated(0.0, 1.5), radius, radius)

        # Pill background and border
        base_color = getattr(node, "color", (45, 80, 130, 255))
        r, g, b = int(base_color[0]), int(base_color[1]), int(base_color[2])
        pill_bg = QtGui.QColor(22, 26, 34, 240)

        if getattr(node, "selected", False):
            border_pen = QtGui.QPen(QtGui.QColor(254, 207, 42, 240), 1.3)
        else:
            border_color = QtGui.QColor(min(r + 65, 255), min(g + 65, 255), min(b + 65, 255), 210)
            border_pen = QtGui.QPen(border_color, 1.1)

        painter.setBrush(QtGui.QBrush(pill_bg))
        painter.setPen(border_pen)
        painter.drawRoundedRect(pill_rect, radius, radius)

        # Centered text
        painter.setPen(QtGui.QColor(242, 245, 250))
        painter.drawText(pill_rect, QtCore.Qt.AlignCenter, display_name)

        painter.restore()


class ExecutableNodeItem(NodeItem):
    """Base graphics item for execution nodes (QueryNode and ImportCsvNode) supporting execution overlays and floating name pills."""

    def __init__(self, name="Node", parent=None):
        super().__init__(name, parent)
        self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsScenePositionChanges, True)
        if hasattr(self, "_text_item") and self._text_item:
            try:
                if self.scene():
                    self.scene().removeItem(self._text_item)
            except Exception:
                pass
        self._text_item = CustomNodeTextItem(self.name, self)
        self._exec_overlay = NodeExecutionOverlayItem(self)
        self._pill_item = FloatingPillItem(self)
        self._active_painter: Optional[QtGui.QPainter] = None

    def itemChange(self, change, value):
        res = super().itemChange(change, value)
        pill = getattr(self, "_pill_item", None)
        if pill is not None:
            if change == QtWidgets.QGraphicsItem.ItemSceneChange:
                # Node is leaving its current scene
                curr_scene = self.scene()
                if curr_scene and pill.scene() == curr_scene:
                    curr_scene.removeItem(pill)
            elif change == QtWidgets.QGraphicsItem.ItemSceneHasChanged:
                # Node was added to a new scene
                new_scene = self.scene()
                if new_scene and pill.scene() != new_scene:
                    new_scene.addItem(pill)
                    pill.setPos(self.scenePos())
            elif change in (QtWidgets.QGraphicsItem.ItemPositionHasChanged, QtWidgets.QGraphicsItem.ItemScenePositionHasChanged):
                pill.setPos(self.scenePos())
            elif change == QtWidgets.QGraphicsItem.ItemSelectedChange:
                pill.setZValue(1001.0 if value else 1000.0)
                pill.update()
        return res

    def is_zoomed_out_mode(self, painter: Optional[QtGui.QPainter] = None) -> bool:
        """Return True when canvas zoom level drops below the threshold for showing the floating pill."""
        active_painter = painter or getattr(self, "_active_painter", None)
        lod = get_node_lod(self, active_painter)
        return (EXEC_NODE_BASE_FONT_PT * lod) < EXEC_NODE_MIN_FONT_PT

    def auto_switch_mode(self) -> None:
        """Keep normal node information visible at all zoom levels; sync floating pill state."""
        pill = getattr(self, "_pill_item", None)
        if pill and self.scene():
            if pill.scene() != self.scene():
                self.scene().addItem(pill)
            pill.setPos(self.scenePos())
            is_zoomed = self.is_zoomed_out_mode()
            pill.setVisible(is_zoomed)
            if is_zoomed:
                pill.update()

    def draw_node(self) -> None:
        super().draw_node()
        if hasattr(self, "_exec_overlay") and self._exec_overlay:
            self._exec_overlay.prepareGeometryChange()
            self._exec_overlay.update()
        if hasattr(self, "_pill_item") and self._pill_item:
            self._pill_item.prepareGeometryChange()
            self._pill_item.setPos(self.scenePos())
            self._pill_item.update()

    def set_execution_state(
        self,
        status: Optional[str] = None,
        elapsed_sec: Optional[float] = None,
        visible: Optional[bool] = None,
    ) -> None:
        self._exec_overlay.set_state(status=status, elapsed_sec=elapsed_sec, visible=visible)
        if hasattr(self, "_pill_item") and self._pill_item:
            self._pill_item.update()
        self.update()

    def advance_execution_animation(self, delta_offset: float = 1.2, elapsed_sec: Optional[float] = None) -> None:
        self._exec_overlay.advance_phase(delta_offset=delta_offset, elapsed_sec=elapsed_sec)

    def paint(self, painter, option, widget):
        if not self.viewer():
            return
        self._active_painter = painter
        try:
            super().paint(painter, option, widget)
            pill = getattr(self, "_pill_item", None)
            if pill:
                if self.scene() and pill.scene() != self.scene():
                    self.scene().addItem(pill)
                pill.setPos(self.scenePos())
                is_zoomed = self.is_zoomed_out_mode(painter)
                pill.setVisible(is_zoomed)
                if is_zoomed:
                    pill.update()
        finally:
            self._active_painter = None

    def _align_label_horizontal(self, h_offset, v_offset):
        super()._align_label_horizontal(h_offset, v_offset)


class QueryNodeItem(ExecutableNodeItem):
    """Custom graphics item for QueryNode displaying parameters list within the node."""

    def __init__(self, name="Query", parent=None):
        super().__init__(name, parent)
        self.parameter_list: List[str] = []

    def set_parameters(self, params: List[str]):
        self.parameter_list = list(params)
        self.draw_node()

    def _calc_size_horizontal(self):
        w, h = super()._calc_size_horizontal()
        font = QtGui.QFont("sans-serif", 9)
        fm = QtGui.QFontMetrics(font)
        display_lines = [f"• {{{p}}}" for p in self.parameter_list] if self.parameter_list else []
        lines_w = max([fm.horizontalAdvance(line) for line in display_lines] or [0]) + 50
        extra_h = (len(display_lines) * (fm.lineSpacing() + 2)) + (16 if display_lines else 0)
        return max(w, float(lines_w), 160.0), max(h + extra_h, 70.0)

    def _paint_horizontal(self, painter, option, widget):
        super()._paint_horizontal(painter, option, widget)
        if not self.parameter_list:
            return

        painter.save()
        rect = self.boundingRect()

        # Find the lower boundary of all ports to draw below them
        ports = [p for p in self.inputs + self.outputs if p.isVisible()]
        bottom_port_y = max([p.y() + p.boundingRect().height() for p in ports] or [self._text_item.boundingRect().height() + 10])

        font = QtGui.QFont("sans-serif", 9)
        painter.setFont(font)
        fm = QtGui.QFontMetrics(font)
        line_h = fm.lineSpacing() + 2

        # Draw a subtle separator line
        sep_y = bottom_port_y + 8.0
        painter.setPen(QtGui.QPen(QtGui.QColor(80, 110, 160, 180), 1.0, QtCore.Qt.DashLine))
        painter.drawLine(QtCore.QPointF(rect.left() + 8, sep_y), QtCore.QPointF(rect.right() - 8, sep_y))

        # Draw parameters header and items
        painter.setPen(QtGui.QColor(180, 205, 240))
        text_y = sep_y + 6.0
        for p in self.parameter_list:
            painter.drawText(QtCore.QPointF(rect.left() + 14, text_y + fm.ascent()), f"• {{{p}}}")
            text_y += line_h

        painter.restore()


class QueryNode(BaseNode):
    """Node representing an individual SQL query file."""

    __identifier__ = "reporting.nodes"
    NODE_NAME = "Query"

    def __init__(self):
        super().__init__(QueryNodeItem)
        # Input execution port (connect from prior queries) - Blue
        self.add_input("run_in", multi_input=True, display_name=True, color=(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2]))
        # Input tables port (connect from input TableBox) - Green
        self.add_input("tables_in", multi_input=True, display_name=True, color=(COLOR_GREEN[0], COLOR_GREEN[1], COLOR_GREEN[2]))

        # Output execution port (connect to subsequent queries) - Blue
        self.add_output("run_out", multi_output=True, display_name=True, color=(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2]))
        # Output tables port (connect to output TableBox) - Dark Orange
        self.add_output("tables_out", multi_output=True, display_name=True, color=(COLOR_DARK_ORANGE[0], COLOR_DARK_ORANGE[1], COLOR_DARK_ORANGE[2]))
        # Output CSV port (connect to output CSV TableBox) - Dark Purple
        self.add_output("csv_out", multi_output=True, display_name=True, color=(COLOR_DARK_PURPLE[0], COLOR_DARK_PURPLE[1], COLOR_DARK_PURPLE[2]))

        # Custom properties
        self.create_property("query_name", "")
        self.create_property("query_path", "")
        self.create_property("report_name", "")
        self.create_property("parameters", "")

        # Visual styling - Blue
        self.set_color(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2])

    def set_property(self, name, value, push_undo=True):
        if name not in self.model.properties and not self.has_property(name):
            self.create_property(name, value)
        else:
            super().set_property(name, value, push_undo=push_undo)

    def set_parameters(self, param_list: List[str]):
        """Set query parameters to display in the node body."""
        self.set_property("parameters", ", ".join(param_list))
        if hasattr(self.view, "set_parameters"):
            self.view.set_parameters(param_list)

    def on_property_changed(self, name, value):
        super().on_property_changed(name, value)
        if name == "parameters" and hasattr(self.view, "set_parameters"):
            if isinstance(value, str):
                params = [p.strip() for p in value.split(",") if p.strip()]
            elif isinstance(value, (list, tuple)):
                params = list(value)
            else:
                params = []
            self.view.set_parameters(params)


class ImportCsvItem(ExecutableNodeItem):
    """Custom graphics item for ImportCsvNode / ImportFilesNode."""

    def __init__(self, name="Import Files", parent=None):
        super().__init__(name, parent)



class ImportCsvNode(BaseNode):
    """Node representing one or more CSV or Excel file imports into BigQuery tables."""

    __identifier__ = "reporting.nodes"
    NODE_NAME = "Import Files"

    def __init__(self):
        super().__init__(ImportCsvItem)
        # Input execution port (connect from prior queries/imports) - Blue
        self.add_input("run_in", multi_input=True, display_name=True, color=(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2]))
        # Input tables/files port (connect from input TableBox) - Green
        self.add_input("tables_in", multi_input=True, display_name=True, color=(COLOR_GREEN[0], COLOR_GREEN[1], COLOR_GREEN[2]))

        # Output execution port (connect to subsequent queries) - Blue
        self.add_output("run_out", multi_output=True, display_name=True, color=(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2]))
        # Output tables port (connect to output TableBox) - Dark Orange
        self.add_output("tables_out", multi_output=True, display_name=True, color=(COLOR_DARK_ORANGE[0], COLOR_DARK_ORANGE[1], COLOR_DARK_ORANGE[2]))

        # Custom properties
        self.create_property("import_name", "Import Files")
        self.create_property("imports_json", "[]")  # list of {"file_path": ..., "has_headers": bool, "output_table": ..., ...}

        # Visual styling - Purple
        self.set_color(COLOR_DARK_PURPLE[0], COLOR_DARK_PURPLE[1], COLOR_DARK_PURPLE[2])

    def set_property(self, name, value, push_undo=True):
        if name not in self.model.properties and not self.has_property(name):
            self.create_property(name, value)
        else:
            super().set_property(name, value, push_undo=push_undo)

    def get_imports(self) -> List[dict]:
        import json
        raw = self.get_property("imports_json") or "[]"
        try:
            return json.loads(raw)
        except Exception:
            return []

    def set_imports(self, imports: List[dict]):
        import json
        self.set_property("imports_json", json.dumps(imports))

    def get_input_files(self) -> List[str]:
        files = []
        for item in self.get_imports():
            f = (item.get("file_path") or item.get("csv_path") or "").strip()
            if f and f not in files:
                files.append(f)
        return files

    def get_output_tables(self) -> List[str]:
        tables = []
        for item in self.get_imports():
            t = item.get("output_table", "").strip()
            if t and t not in tables:
                tables.append(t)
        return tables


ImportFilesItem = ImportCsvItem
ImportFilesNode = ImportCsvNode


class TableBoxNode(BaseNode):
    """Node displaying a vertical list of database table names rendered directly in the node box."""

    __identifier__ = "reporting.nodes"
    NODE_NAME = "Table Box"

    def __init__(self):
        super().__init__(TableBoxItem)
        # Ports created unconditionally in __init__ so NodeGraphQt serialization restores them properly
        self.add_input("in_tables", multi_input=True, display_name=False, color=(COLOR_DARK_ORANGE[0], COLOR_DARK_ORANGE[1], COLOR_DARK_ORANGE[2]))
        self.add_output("out_tables", multi_output=True, display_name=False, color=(COLOR_GREEN[0], COLOR_GREEN[1], COLOR_GREEN[2]))

        self.create_property("box_type", "Input Tables")
        self.create_property("raw_tables_json", "")
        self.create_property("query_owner", "")
        self.raw_tables: List[str] = []
        self.show_full_path: bool = True

        self.set_color(COLOR_GREEN[0], COLOR_GREEN[1], COLOR_GREEN[2])

    def set_property(self, name, value, push_undo=True):
        if name not in self.model.properties and not self.has_property(name):
            self.create_property(name, value)
        else:
            super().set_property(name, value, push_undo=push_undo)

    def set_display_mode(self, show_full_path: bool):
        """Switch between full path (e.g. project.dataset.table) and short name (table)."""
        self.show_full_path = show_full_path
        self._refresh_display_text()

    def _refresh_display_text(self):
        # If raw_tables is empty, restore from property if present
        if not self.raw_tables:
            saved = self.get_property("raw_tables_json")
            if saved:
                import json
                try:
                    self.raw_tables = json.loads(saved)
                except Exception:
                    pass

        lines = []
        is_csv = (
            self.get_property("box_type") == "Output CSV"
            or getattr(getattr(self, "view", None), "table_box_type", "") == "Output CSV"
        )
        for t in self.raw_tables:
            if is_csv or t.lower().endswith(".csv") or t.lower().endswith(".xlsx"):
                name = t if self.show_full_path else Path(t).name
            else:
                name = t if self.show_full_path else t.split(".")[-1]
            lines.append(f"• {name}")

        self.view.set_table_lines(lines)

    def setup_as_input(self, tables: List[str]):
        """Configure node as an Input Table box."""
        import json
        self.set_property("box_type", "Input Tables")
        self.view.table_box_type = "Input Tables"
        self.view.set_custom_title("Input Tables")
        self.raw_tables = list(tables)
        self.set_property("raw_tables_json", json.dumps(self.raw_tables))

        # Input tables connect their out_tables to query tables_in
        in_p = self.get_input("in_tables")
        if in_p:
            in_p.set_visible(False)
        out_p = self.get_output("out_tables")
        if out_p:
            out_p.set_visible(True)
            out_p.color = COLOR_GREEN

        self._refresh_display_text()
        self.set_color(COLOR_GREEN[0], COLOR_GREEN[1], COLOR_GREEN[2])

    def setup_as_output(self, tables: List[str]):
        """Configure node as an Output Table box."""
        import json
        self.set_property("box_type", "Output Tables")
        self.view.table_box_type = "Output Tables"
        self.view.set_custom_title("Output Tables")
        self.raw_tables = list(tables)
        self.set_property("raw_tables_json", json.dumps(self.raw_tables))

        # Output tables can receive connections from query tables_out or upstream output boxes
        in_p = self.get_input("in_tables")
        if in_p:
            in_p.set_visible(True)
            in_p.color = COLOR_DARK_ORANGE
        # Output tables can also output to downstream queries or consolidated output boxes
        out_p = self.get_output("out_tables")
        if out_p:
            out_p.set_visible(True)
            out_p.color = COLOR_DARK_ORANGE

        self._refresh_display_text()
        self.set_color(COLOR_DARK_ORANGE[0], COLOR_DARK_ORANGE[1], COLOR_DARK_ORANGE[2])

    def setup_as_csv_output(self, csv_files: List[str] | str):
        """Configure node as an Output CSV box displaying output CSV file name(s)."""
        import json
        self.set_property("box_type", "Output CSV")
        self.view.table_box_type = "Output CSV"
        self.view.set_custom_title("Output CSV")
        if isinstance(csv_files, str):
            self.raw_tables = [csv_files]
        else:
            self.raw_tables = list(csv_files)
        self.set_property("raw_tables_json", json.dumps(self.raw_tables))

        # Output CSV receives connection from query csv_out / tables_out
        out_p = self.get_output("out_tables")
        if out_p:
            out_p.set_visible(False)
        in_p = self.get_input("in_tables")
        if in_p:
            in_p.set_visible(True)
            in_p.color = COLOR_DARK_PURPLE

        self._refresh_display_text()
        self.set_color(COLOR_DARK_PURPLE[0], COLOR_DARK_PURPLE[1], COLOR_DARK_PURPLE[2])

    def on_property_changed(self, name, value):
        super().on_property_changed(name, value)
        if name == "raw_tables_json":
            self._refresh_display_text()
        elif name == "box_type":
            self.view.table_box_type = value
            self.view.set_custom_title(value)
            if value == "Input Tables":
                in_p = self.get_input("in_tables")
                if in_p:
                    in_p.set_visible(False)
                out_p = self.get_output("out_tables")
                if out_p:
                    out_p.set_visible(True)
                    out_p.color = COLOR_GREEN
                self.set_color(COLOR_GREEN[0], COLOR_GREEN[1], COLOR_GREEN[2])
            elif value == "Output CSV":
                out_p = self.get_output("out_tables")
                if out_p:
                    out_p.set_visible(False)
                in_p = self.get_input("in_tables")
                if in_p:
                    in_p.set_visible(True)
                    in_p.color = COLOR_DARK_PURPLE
                self.set_color(COLOR_DARK_PURPLE[0], COLOR_DARK_PURPLE[1], COLOR_DARK_PURPLE[2])
            else:  # Output Tables
                in_p = self.get_input("in_tables")
                if in_p:
                    in_p.set_visible(True)
                    in_p.color = COLOR_DARK_ORANGE
                out_p = self.get_output("out_tables")
                if out_p:
                    out_p.set_visible(True)
                    out_p.color = COLOR_DARK_ORANGE
                self.set_color(COLOR_DARK_ORANGE[0], COLOR_DARK_ORANGE[1], COLOR_DARK_ORANGE[2])


def get_noodle_color(pipe: PipeItem) -> Tuple[int, int, int, int]:
    """Determine pipe color based on source and target node and port types:

    Rules per specification:
    - Orange edges: Query Node -> Output Table collection; or Upstream Output Table -> Consolidated Output box.
    - Green edges: Any intake into a Query Node (External Input Source -> Query Node, or Output Table -> Query Node).
    - Dark Purple edges: Query Node -> Output CSV box.
    - Blue edges: Query Node -> Query Node execution order.
    """
    out_p = pipe.output_port
    in_p = pipe.input_port
    if not out_p or not in_p:
        return COLOR_BLUE

    out_node = out_p.node
    in_node = in_p.node

    out_box_type = getattr(out_node, "table_box_type", "")
    in_box_type = getattr(in_node, "table_box_type", "")
    in_node_name = getattr(in_node, "name", "")
    out_node_name = getattr(out_node, "name", "")

    # 1. Any ingestion into a Query Node tables_in port is GREEN
    if in_p.name == "tables_in" or (isinstance(in_node_name, str) and not ("[" in in_node_name or in_box_type)):
        # If going into a query node tables_in port, it's a consumption link (Green)
        if in_p.name == "tables_in":
            return COLOR_GREEN

    # 2. From query node to output CSV box: DARK PURPLE
    if (
        in_box_type == "Output CSV"
        or in_p.name == "in_csv"
        or out_p.name == "csv_out"
        or "CSV" in in_node_name
    ):
        return COLOR_DARK_PURPLE

    # 3. Direct authorship from Query Node to Output Table collection: ORANGE
    if out_p.name == "tables_out" and (in_box_type == "Output Tables" or "Out" in in_node_name):
        return COLOR_DARK_ORANGE

    # 4. From Upstream Output Table to Consolidated Output box: ORANGE
    if out_box_type == "Output Tables" and in_box_type == "Output Tables":
        return COLOR_DARK_ORANGE

    # 5. From any Table box to Query Node: GREEN
    if (out_box_type in ("Input Tables", "Output Tables") or out_p.name == "out_tables") and in_p.name == "tables_in":
        return COLOR_GREEN

    # 6. From query node to query node: BLUE
    if out_p.name == "run_out" and in_p.name == "run_in":
        return COLOR_BLUE

    # Fallbacks based on box types
    if in_box_type == "Output Tables" or out_box_type == "Output Tables":
        return COLOR_DARK_ORANGE
    if out_box_type == "Input Tables":
        return COLOR_GREEN

    return COLOR_BLUE


# Install PipeItem monkey patches for dynamic noodle coloring
_orig_pipe_reset = PipeItem.reset
_orig_pipe_set_connections = PipeItem.set_connections


def _custom_pipe_reset(self):
    color = get_noodle_color(self)
    if color:
        self._color = color
    _orig_pipe_reset(self)


def _custom_pipe_set_connections(self, port1, port2):
    _orig_pipe_set_connections(self, port1, port2)
    self.reset()


PipeItem.reset = _custom_pipe_reset
PipeItem.set_connections = _custom_pipe_set_connections

# Patch NodeGraphQt PortConnectedCmd and PortDisconnectedCmd to protect against KeyError in ports dictionary
from NodeGraphQt.base import commands
from NodeGraphQt.constants import PortTypeEnum

_orig_port_connected_redo = commands.PortConnectedCmd.redo
_orig_port_disconnected_redo = commands.PortDisconnectedCmd.redo


def _safe_port_connected_redo(self):
    src_model = self.source.model
    trg_model = self.target.model
    src_id = self.source.node().id
    trg_id = self.target.node().id

    src_model.connected_ports[trg_id].append(self.target.name())
    trg_model.connected_ports[src_id].append(self.source.name())

    self.source.view.connect_to(self.target.view)

    if self.emit_signal:
        ports = {p.type_(): p for p in [self.source, self.target]}
        in_p = ports.get(PortTypeEnum.IN.value)
        out_p = ports.get(PortTypeEnum.OUT.value)
        if in_p and out_p:
            graph = self.source.node().graph
            graph.port_connected.emit(in_p, out_p)


def _safe_port_disconnected_redo(self):
    src_model = self.source.model
    trg_model = self.target.model
    src_id = self.source.node().id
    trg_id = self.target.node().id

    port_names = src_model.connected_ports.get(trg_id)
    if port_names is []:
        del src_model.connected_ports[trg_id]
    if port_names and self.target.name() in port_names:
        port_names.remove(self.target.name())

    port_names = trg_model.connected_ports.get(src_id)
    if port_names is []:
        del trg_model.connected_ports[src_id]
    if port_names and self.source.name() in port_names:
        port_names.remove(self.source.name())

    self.source.view.disconnect_from(self.target.view)

    if self.emit_signal:
        ports = {p.type_(): p for p in [self.source, self.target]}
        in_p = ports.get(PortTypeEnum.IN.value)
        out_p = ports.get(PortTypeEnum.OUT.value)
        if in_p and out_p:
            graph = self.source.node().graph
            graph.port_disconnected.emit(in_p, out_p)


commands.PortConnectedCmd.redo = _safe_port_connected_redo
commands.PortDisconnectedCmd.redo = _safe_port_disconnected_redo


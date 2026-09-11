"""Custom NodeGraphQt nodes for queries and table lists."""

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


class QueryNodeItem(NodeItem):
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


class ImportCsvItem(NodeItem):
    """Custom graphics item for ImportCsvNode."""

    def __init__(self, name="Import csv", parent=None):
        super().__init__(name, parent)


class ImportCsvNode(BaseNode):
    """Node representing one or more CSV file imports into BigQuery tables."""

    __identifier__ = "reporting.nodes"
    NODE_NAME = "Import csv"

    def __init__(self):
        super().__init__(ImportCsvItem)
        # Input execution port (connect from prior queries/imports) - Blue
        self.add_input("run_in", multi_input=True, display_name=True, color=(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2]))

        # Output execution port (connect to subsequent queries) - Blue
        self.add_output("run_out", multi_output=True, display_name=True, color=(COLOR_BLUE[0], COLOR_BLUE[1], COLOR_BLUE[2]))
        # Output tables port (connect to output TableBox) - Dark Orange
        self.add_output("tables_out", multi_output=True, display_name=True, color=(COLOR_DARK_ORANGE[0], COLOR_DARK_ORANGE[1], COLOR_DARK_ORANGE[2]))

        # Custom properties
        self.create_property("import_name", "Import csv")
        self.create_property("imports_json", "[]")  # list of {"csv_path": ..., "has_headers": bool, "output_table": ...}

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

    def get_output_tables(self) -> List[str]:
        tables = []
        for item in self.get_imports():
            t = item.get("output_table", "").strip()
            if t and t not in tables:
                tables.append(t)
        return tables


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
        for t in self.raw_tables:
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

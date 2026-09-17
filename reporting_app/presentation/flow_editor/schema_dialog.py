"""Dialog for manually creating, editing, and managing BigQuery table schemas."""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

BQ_TYPES = [
    "STRING",
    "INTEGER",
    "FLOAT",
    "BOOLEAN",
    "DATE",
    "DATETIME",
    "TIMESTAMP",
    "NUMERIC",
    "BYTES",
]

BQ_MODES = [
    "NULLABLE",
    "REQUIRED",
    "REPEATED",
]


def inspect_file_columns(
    file_path: str,
    sheet_name: Optional[str] = None,
    has_headers: bool = True,
) -> List[str]:
    """Extract column headers from a CSV or XLSX file."""
    p = Path(file_path)
    if not p.exists():
        return []

    ext = p.suffix.lower()
    try:
        if ext == ".csv":
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.reader(f)
                first_row = next(reader, None)
                if not first_row:
                    return []
                if has_headers:
                    return [str(c).strip() for c in first_row if str(c).strip()]
                return [f"col_{i+1}" for i in range(len(first_row))]
        elif ext in (".xlsx", ".xls"):
            sheet = sheet_name or 0
            df = pd.read_excel(p, sheet_name=sheet, nrows=0, header=0 if has_headers else None, engine="openpyxl")
            if has_headers:
                return [str(c).strip() for c in df.columns]
            return [f"col_{i+1}" for i in range(len(df.columns))]
    except Exception:
        pass
    return []


class SchemaEditorDialog(QDialog):
    """Visual editor for BigQuery table schemas with JSON import/export."""

    def __init__(
        self,
        parent=None,
        current_schema: Optional[List[Dict[str, Any]]] = None,
        file_path: Optional[str] = None,
        sheet_name: Optional[str] = None,
        has_headers: bool = True,
        table_name: str = "",
    ):
        super().__init__(parent)
        title = f"Configure Schema - {table_name}" if table_name else "Configure Table Schema"
        self.setWindowTitle(title)
        self.resize(650, 420)

        self._file_path = file_path or ""
        self._sheet_name = sheet_name
        self._has_headers = has_headers
        self._schema_fields: List[Dict[str, Any]] = [dict(f) for f in (current_schema or [])]

        self._build_ui()
        if not self._schema_fields and self._file_path:
            self._load_from_file_headers()
        else:
            self._populate_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header info
        info_text = "Define the column names, BigQuery data types, and modes for the imported table."
        if self._file_path:
            info_text += f"\nSource: {Path(self._file_path).name}"
        layout.addWidget(QLabel(info_text))

        # Schema table
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Column Name", "Type", "Mode", "Description"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 180)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table, 1)

        # Table buttons row
        btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ Add Field")
        add_btn.clicked.connect(self._on_add_field)
        btn_row.addWidget(add_btn)

        del_btn = QPushButton("🗑 Remove Field")
        del_btn.clicked.connect(self._on_remove_field)
        btn_row.addWidget(del_btn)

        up_btn = QPushButton("⬆ Move Up")
        up_btn.clicked.connect(self._on_move_up)
        btn_row.addWidget(up_btn)

        down_btn = QPushButton("⬇ Move Down")
        down_btn.clicked.connect(self._on_move_down)
        btn_row.addWidget(down_btn)

        btn_row.addStretch()

        if self._file_path:
            detect_btn = QPushButton("🔄 Reload from File")
            detect_btn.setToolTip("Extract column headers from source file and reset table")
            detect_btn.clicked.connect(self._load_from_file_headers)
            btn_row.addWidget(detect_btn)

        import_json_btn = QPushButton("📂 Import JSON...")
        import_json_btn.setToolTip("Load schema from BigQuery JSON file")
        import_json_btn.clicked.connect(self._on_import_json)
        btn_row.addWidget(import_json_btn)

        export_json_btn = QPushButton("💾 Export JSON...")
        export_json_btn.setToolTip("Export current schema to BigQuery JSON file")
        export_json_btn.clicked.connect(self._on_export_json)
        btn_row.addWidget(export_json_btn)

        layout.addLayout(btn_row)

        # Dialog buttons (OK / Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_from_file_headers(self) -> None:
        cols = inspect_file_columns(self._file_path, self._sheet_name, self._has_headers)
        if not cols:
            if not self._schema_fields:
                # Add default empty row
                self._schema_fields = [{"name": "column_1", "type": "STRING", "mode": "NULLABLE", "description": ""}]
        else:
            self._schema_fields = [
                {"name": c, "type": "STRING", "mode": "NULLABLE", "description": ""}
                for c in cols
            ]
        self._populate_table()

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for field in self._schema_fields:
            self._insert_row(field)

    def _insert_row(self, field: Dict[str, Any], row_idx: Optional[int] = None) -> int:
        if row_idx is None:
            row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)

        # Column Name
        name_item = QTableWidgetItem(field.get("name", ""))
        self.table.setItem(row_idx, 0, name_item)

        # Type Combo
        type_combo = QComboBox()
        type_combo.addItems(BQ_TYPES)
        curr_type = field.get("type", "STRING").upper()
        t_idx = type_combo.findText(curr_type)
        if t_idx >= 0:
            type_combo.setCurrentIndex(t_idx)
        self.table.setCellWidget(row_idx, 1, type_combo)

        # Mode Combo
        mode_combo = QComboBox()
        mode_combo.addItems(BQ_MODES)
        curr_mode = field.get("mode", "NULLABLE").upper()
        m_idx = mode_combo.findText(curr_mode)
        if m_idx >= 0:
            mode_combo.setCurrentIndex(m_idx)
        self.table.setCellWidget(row_idx, 2, mode_combo)

        # Description
        desc_item = QTableWidgetItem(field.get("description", ""))
        self.table.setItem(row_idx, 3, desc_item)
        return row_idx

    def _get_row_data(self, row: int) -> Dict[str, str]:
        name_item = self.table.item(row, 0)
        name = name_item.text().strip() if name_item else ""

        type_combo = self.table.cellWidget(row, 1)
        type_val = type_combo.currentText() if isinstance(type_combo, QComboBox) else "STRING"

        mode_combo = self.table.cellWidget(row, 2)
        mode_val = mode_combo.currentText() if isinstance(mode_combo, QComboBox) else "NULLABLE"

        desc_item = self.table.item(row, 3)
        desc = desc_item.text().strip() if desc_item else ""

        return {"name": name, "type": type_val, "mode": mode_val, "description": desc}

    def _on_add_field(self) -> None:
        idx = self.table.rowCount() + 1
        new_row = {"name": f"column_{idx}", "type": "STRING", "mode": "NULLABLE", "description": ""}
        row = self._insert_row(new_row)
        self.table.selectRow(row)

    def _on_remove_field(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        self.table.removeRow(row)

    def _on_move_up(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        if row <= 0:
            return
        data_curr = self._get_row_data(row)
        data_prev = self._get_row_data(row - 1)
        self.table.removeRow(row)
        self.table.removeRow(row - 1)
        self._insert_row(data_curr, row - 1)
        self._insert_row(data_prev, row)
        self.table.selectRow(row - 1)

    def _on_move_down(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        if row >= self.table.rowCount() - 1:
            return
        data_curr = self._get_row_data(row)
        data_next = self._get_row_data(row + 1)
        self.table.removeRow(row + 1)
        self.table.removeRow(row)
        self._insert_row(data_next, row)
        self._insert_row(data_curr, row + 1)
        self.table.selectRow(row + 1)

    def _on_import_json(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Import BigQuery Schema JSON",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not chosen:
            return
        try:
            with open(chosen, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Expected a JSON list of BigQuery fields: [{\"name\": \"...\", \"type\": \"...\"}]")
            new_fields = []
            for item in data:
                if isinstance(item, dict) and "name" in item:
                    new_fields.append({
                        "name": str(item.get("name", "")).strip(),
                        "type": str(item.get("type", "STRING")).upper(),
                        "mode": str(item.get("mode", "NULLABLE")).upper(),
                        "description": str(item.get("description", "")),
                    })
            if not new_fields:
                raise ValueError("No valid schema fields found in JSON.")
            self._schema_fields = new_fields
            self._populate_table()
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to load schema from JSON:\n{e}")

    def _on_export_json(self) -> None:
        schema = self._collect_schema()
        if not schema:
            QMessageBox.warning(self, "Export Schema", "No schema fields to export.")
            return

        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Export BigQuery Schema JSON",
            "schema.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not chosen:
            return

        try:
            with open(chosen, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)
            QMessageBox.information(self, "Export Success", f"Schema exported successfully to:\n{chosen}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export schema:\n{e}")

    def _collect_schema(self) -> List[Dict[str, str]]:
        result = []
        for r in range(self.table.rowCount()):
            data = self._get_row_data(r)
            if data["name"]:
                result.append(data)
        return result

    def _on_accept(self) -> None:
        schema = self._collect_schema()
        if not schema:
            QMessageBox.warning(self, "Validation Error", "The schema must contain at least one column with a name.")
            return

        # Check for duplicates
        seen = set()
        for field in schema:
            name_lower = field["name"].lower()
            if name_lower in seen:
                QMessageBox.warning(self, "Duplicate Column", f"Column '{field['name']}' is defined more than once.")
                return
            seen.add(name_lower)

        self._schema_fields = schema
        self.accept()

    def get_schema(self) -> List[Dict[str, str]]:
        """Return the configured schema fields."""
        if hasattr(self, "table") and self.table.rowCount() > 0:
            return self._collect_schema()
        return self._schema_fields


# ---------------------------------------------------------------------------
# Execution Log Formatting Utilities
# ---------------------------------------------------------------------------

def format_bytes(b: Optional[int]) -> str:
    """Format bytes to human readable format (B, KB, MB, GB, TB)."""
    if b is None or b < 0:
        return "N/A"
    if b < 1024:
        return f"{b} B"
    elif b < 1024**2:
        return f"{b / 1024:.2f} KB"
    elif b < 1024**3:
        return f"{b / (1024**2):.2f} MB"
    elif b < 1024**4:
        return f"{b / (1024**3):.2f} GB"
    else:
        return f"{b / (1024**4):.2f} TB"


def format_duration(seconds: Optional[float]) -> str:
    """Format duration in seconds to readable string."""
    if seconds is None:
        return "N/A"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60.0:
        return f"{seconds:.2f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m {s:.1f}s"


def format_timestamp(iso_str: Optional[str]) -> str:
    """Convert ISO timestamp to readable date/time string."""
    if not iso_str:
        return "N/A"
    return iso_str.replace("T", " ").split(".")[0]


def format_single_node_log(log: Any) -> str:
    """Render a single ExecutionLog entry as Markdown."""
    lines = []
    is_success = getattr(log, "status", "") == "SUCCESS"
    status_badge = "✅" if is_success else "❌"
    node_type = getattr(log, "node_type", "query")
    node_name = getattr(log, "node_name", "")
    node_type_label = "Query" if node_type == "query" else "Import Files"

    lines.append(f"## {status_badge} {node_type_label}: `{node_name}`\n")

    # Timing and General Info
    flow_name = getattr(log, "flow_name", "")
    flow_desc = f"`{flow_name}`" if flow_name and flow_name != "Standalone" else "*Standalone Run*"
    lines.append(f"* **Process Flow:** {flow_desc}")
    lines.append(f"* **Report:** `{getattr(log, 'report_name', '')}`")
    lines.append(f"* **Flow Started:** `{format_timestamp(getattr(log, 'flow_start_time', ''))}`")
    lines.append(f"* **Node Started:** `{format_timestamp(getattr(log, 'node_start_time', ''))}`")
    lines.append(f"* **Execution Duration:** `{format_duration(getattr(log, 'duration_seconds', 0.0))}`")
    lines.append(f"* **Status:** **{getattr(log, 'status', '')}**")

    err_msg = getattr(log, "error_message", None)
    if not is_success and err_msg:
        lines.append(f"\n> ⚠️ **Execution Error:**\n> {err_msg}\n")

    lines.append("\n---\n")

    if node_type == "query":
        # Data Size and Volume
        lines.append("### 📊 Data Size & Volume")
        lines.append(f"* **Total Bytes Processed:** {format_bytes(getattr(log, 'total_bytes_processed', None))}")
        lines.append(f"* **Total Bytes Billed:** {format_bytes(getattr(log, 'total_bytes_billed', None))}")
        out_rows = getattr(log, "output_rows", None)
        row_cnt_str = f"{out_rows:,} rows" if out_rows is not None else "N/A"
        lines.append(f"* **Output Rows:** {row_cnt_str}\n")

        # Compute & Cost Efficiency
        lines.append("### ⚡ Compute & Cost Efficiency")
        sm = getattr(log, "slot_millis", None)
        slot_str = f"{sm:,} ms" if sm is not None else "N/A"
        lines.append(f"* **Slot Millis (CPU):** {slot_str}")
        ch = getattr(log, "cache_hit", None)
        cache_str = "True (Cached - $0 cost)" if ch else ("False" if ch is not None else "N/A")
        lines.append(f"* **Cache Hit:** {cache_str}\n")

        # Exported outputs
        exp_json = getattr(log, "export_details_json", None)
        if exp_json:
            try:
                exports = json.loads(exp_json) if isinstance(exp_json, str) else exp_json
                if exports:
                    lines.append("### 📁 Exported Files")
                    for exp in exports:
                        fn = exp.get("filename", "output.csv")
                        rc = exp.get("row_count")
                        rc_s = f" ({rc:,} rows)" if rc is not None else ""
                        lines.append(f"* `{fn}`{rc_s}")
                    lines.append("")
            except Exception:
                pass

        # Submitted Query (collapsible details)
        sub_query = getattr(log, "submitted_query", None)
        if sub_query:
            clean_query = sub_query.strip()
            lines.append("<details open>")
            lines.append("<summary><b>🔍 Submitted SQL Query</b> (click to expand/collapse)</summary>\n")
            lines.append("```sql")
            lines.append(clean_query)
            lines.append("```")
            lines.append("</details>\n")

    elif node_type == "import_csv":
        lines.append("### 📥 Import Details")
        out_rows = getattr(log, "output_rows", None)
        if out_rows is not None:
            lines.append(f"* **Rows Loaded:** {out_rows:,} rows")
        tbp = getattr(log, "total_bytes_processed", None)
        if tbp:
            lines.append(f"* **Bytes Processed:** {format_bytes(tbp)}")

        imp_json = getattr(log, "import_details_json", None)
        if imp_json:
            try:
                imports = json.loads(imp_json) if isinstance(imp_json, str) else imp_json
                if imports:
                    lines.append("\n#### Loaded Sources")
                    for imp in imports:
                        src = imp.get("file_path", "")
                        dst = imp.get("destination_table", "")
                        rc = imp.get("row_count")
                        rc_s = f" — {rc:,} rows" if rc is not None else ""
                        lines.append(f"* Source: `{src}` ➔ Table: `{dst}`{rc_s}")
                    lines.append("")
            except Exception:
                pass

    return "\n".join(lines)


def format_run_session_logs(logs: List[Any], title: Optional[str] = None) -> str:
    """Render multiple ExecutionLog entries into a combined markdown document."""
    if not logs:
        return "# Execution Log\n\n*No execution logs found for the selected filter.*"

    doc = []
    if title:
        doc.append(f"# {title}\n")
    else:
        first = logs[0]
        f_time = format_timestamp(getattr(first, "flow_start_time", ""))
        fn = getattr(first, "flow_name", "")
        f_name = fn if fn and fn != "Standalone" else "Standalone Run"
        doc.append(f"# Execution Run: {f_name} ({f_time})\n")

    # Overall Summary
    total_duration = sum(getattr(l, "duration_seconds", 0.0) for l in logs)
    total_bytes = sum(getattr(l, "total_bytes_processed", 0) or 0 for l in logs)
    total_billed = sum(getattr(l, "total_bytes_billed", 0) or 0 for l in logs)
    all_success = all(getattr(l, "status", "") == "SUCCESS" for l in logs)
    status_icon = "✅ Success" if all_success else "❌ Failed"

    doc.append("### Summary Overview")
    doc.append(f"* **Overall Status:** {status_icon}")
    doc.append(f"* **Total Nodes Executed:** {len(logs)}")
    doc.append(f"* **Combined Duration:** `{format_duration(total_duration)}`")
    if total_bytes > 0:
        doc.append(f"* **Total Bytes Processed:** `{format_bytes(total_bytes)}`")
        doc.append(f"* **Total Bytes Billed:** `{format_bytes(total_billed)}`")
    doc.append("\n" + "=" * 50 + "\n")

    for idx, log in enumerate(logs, 1):
        doc.append(f"### Node {idx} of {len(logs)}")
        doc.append(format_single_node_log(log))
        doc.append("\n" + "-" * 40 + "\n")

    return "\n".join(doc)

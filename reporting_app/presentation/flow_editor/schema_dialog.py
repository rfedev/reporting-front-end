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


def _detect_series_type(s: pd.Series) -> str:
    """Infer BigQuery data type from a pandas Series."""
    non_null = s.dropna()
    if non_null.empty:
        return "STRING"

    # 1. Datetime / Date checks
    if pd.api.types.is_datetime64_any_dtype(non_null):
        try:
            if (
                (non_null.dt.hour == 0).all()
                and (non_null.dt.minute == 0).all()
                and (non_null.dt.second == 0).all()
                and (non_null.dt.microsecond == 0).all()
            ):
                return "DATE"
        except Exception:
            pass
        return "TIMESTAMP"

    # 2. Boolean checks
    if pd.api.types.is_bool_dtype(non_null):
        return "BOOLEAN"
    str_vals = non_null.astype(str).str.strip().str.lower()
    if str_vals.isin(["true", "false"]).all():
        return "BOOLEAN"

    # 3. Numeric checks
    num = pd.to_numeric(non_null, errors="coerce")
    if num.notna().all():
        try:
            if (num % 1 == 0).all():
                return "INTEGER"
        except Exception:
            pass
        return "FLOAT"

    # 4. String date checks
    if str_vals.str.contains(r"[-/]").any():
        try:
            dt_parsed = pd.to_datetime(non_null, errors="coerce")
            if dt_parsed.notna().all():
                if (
                    (dt_parsed.dt.hour == 0).all()
                    and (dt_parsed.dt.minute == 0).all()
                    and (dt_parsed.dt.second == 0).all()
                    and (dt_parsed.dt.microsecond == 0).all()
                ):
                    return "DATE"
                return "TIMESTAMP"
        except Exception:
            pass

    return "STRING"


def inspect_file_schema(
    file_path: str,
    sheet_name: Optional[str] = None,
    has_headers: bool = True,
) -> List[Dict[str, Any]]:
    """Extract column headers and auto-detect BigQuery data types from a CSV or XLSX file."""
    p = Path(file_path)
    if not p.exists():
        return []

    ext = p.suffix.lower()
    df = None
    try:
        if ext in (".xlsx", ".xls"):
            sheet = sheet_name or 0
            df = pd.read_excel(p, sheet_name=sheet, header=0 if has_headers else None, nrows=5000, engine="openpyxl")
        elif ext == ".csv":
            df = pd.read_csv(p, header=0 if has_headers else None, nrows=5000, encoding="utf-8-sig", low_memory=False)
    except Exception:
        return []

    if df is None or (df.empty and len(df.columns) == 0):
        return []

    if has_headers:
        col_names = [str(c).strip() for c in df.columns]
    else:
        col_names = [f"col_{i+1}" for i in range(len(df.columns))]

    fields = []
    for idx, col in enumerate(df.columns):
        col_name = col_names[idx] if idx < len(col_names) else f"col_{idx+1}"
        s = df[col]
        detected_type = _detect_series_type(s)
        fields.append({
            "name": col_name,
            "type": detected_type,
            "mode": "NULLABLE",
            "description": "",
        })

    return fields


def inspect_file_columns(
    file_path: str,
    sheet_name: Optional[str] = None,
    has_headers: bool = True,
) -> List[str]:
    """Extract column headers from a CSV or XLSX file."""
    schema = inspect_file_schema(file_path, sheet_name=sheet_name, has_headers=has_headers)
    if schema:
        return [f["name"] for f in schema]

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
            self._auto_detect_all_fields()
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

        # Action buttons row: Auto Detect and Reload from File on left, Import and Export on right
        btn_row = QHBoxLayout()

        self.auto_detect_btn = QPushButton("Auto Detect")
        self.auto_detect_btn.setToolTip("Auto detect all field types from source file")
        self.auto_detect_btn.clicked.connect(self._on_auto_detect)
        btn_row.addWidget(self.auto_detect_btn)

        self.reload_btn = QPushButton("Reload from File")
        self.reload_btn.setToolTip("Reload columns from source file and auto detect types for new fields")
        self.reload_btn.clicked.connect(self._on_reload_from_file)
        btn_row.addWidget(self.reload_btn)

        has_file = bool(self._file_path and Path(self._file_path).exists())
        self.auto_detect_btn.setEnabled(has_file)
        self.reload_btn.setEnabled(has_file)

        btn_row.addStretch()

        import_json_btn = QPushButton("Import JSON...")
        import_json_btn.setToolTip("Load schema from BigQuery JSON file")
        import_json_btn.clicked.connect(self._on_import_json)
        btn_row.addWidget(import_json_btn)

        export_json_btn = QPushButton("Export JSON...")
        export_json_btn.setToolTip("Export current schema to BigQuery JSON file")
        export_json_btn.clicked.connect(self._on_export_json)
        btn_row.addWidget(export_json_btn)

        layout.addLayout(btn_row)

        # Dialog buttons (OK / Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _auto_detect_all_fields(self) -> None:
        detected = inspect_file_schema(self._file_path, self._sheet_name, self._has_headers)
        if not detected:
            if not self._schema_fields:
                self._schema_fields = [{"name": "column_1", "type": "STRING", "mode": "NULLABLE", "description": ""}]
        else:
            current_data = {r["name"].lower(): r for r in self._collect_schema()}
            for f in detected:
                existing = current_data.get(f["name"].lower())
                if existing and existing.get("description"):
                    f["description"] = existing["description"]
            self._schema_fields = detected
        self._populate_table()

    def _on_auto_detect(self) -> None:
        self._auto_detect_all_fields()

    def _on_reload_from_file(self) -> None:
        detected = inspect_file_schema(self._file_path, self._sheet_name, self._has_headers)
        if not detected:
            QMessageBox.warning(self, "Reload from File", "No columns found in the source file.")
            return

        current_schema = self._collect_schema()
        current_map = {f["name"].lower(): f for f in current_schema}

        new_schema = []
        for f in detected:
            name_lower = f["name"].lower()
            if name_lower in current_map:
                existing = current_map[name_lower]
                new_schema.append({
                    "name": f["name"],
                    "type": existing.get("type", "STRING"),
                    "mode": existing.get("mode", "NULLABLE"),
                    "description": existing.get("description", ""),
                })
            else:
                # New field added from the reload: auto detect field type!
                new_schema.append(f)

        self._schema_fields = new_schema
        self._populate_table()

    _load_from_file_headers = _on_reload_from_file

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
# Backward Compatibility Re-exports
# ---------------------------------------------------------------------------
from reporting_app.presentation.log_formatter import (  # noqa: F401
    format_bytes,
    format_duration,
    format_timestamp,
    format_single_node_log,
    format_run_session_logs,
    format_day_summary_logs,
)



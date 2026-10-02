"""Dialog for configuring CSV and Excel file imports into BigQuery."""

import re
from pathlib import Path
from typing import List, Optional, Tuple
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from reporting_app.presentation.flow_editor.schema_dialog import SchemaEditorDialog


class SelectOutputTablesDialog(QDialog):
    """Dialog for selecting which tables to export to CSV when multiple tables exist in an output TableBox."""

    def __init__(self, raw_tables: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Select Tables")
        self.resize(360, 280)
        self.raw_tables = list(raw_tables)
        self.items: List[Tuple[str, QListWidgetItem]] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header = QLabel("<b>Select tables to output:</b>")
        layout.addWidget(header)

        self.list_widget = QListWidget(self)
        for t in self.raw_tables:
            clean_name = t.split(".")[-1] if "." in t else t
            item = QListWidgetItem(clean_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list_widget.addItem(item)
            self.items.append((t, item))
        layout.addWidget(self.list_widget)

        # Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        ok_btn.clicked.connect(self.accept)
        btn_bar.addWidget(cancel_btn)
        btn_bar.addWidget(ok_btn)
        layout.addLayout(btn_bar)

    def get_selected_tables(self) -> List[str]:
        return [raw_t for raw_t, item in self.items if item.checkState() == Qt.Checked]


class ImportFilesDialog(QDialog):
    """Dialog window to configure CSV / Excel file import(s) into BigQuery table(s)."""

    def __init__(
        self,
        initial_imports: Optional[List[dict]] = None,
        workbench_dataset: str = "",
        report_folder: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Import Files Configuration")
        self.resize(750, 520)
        self.workbench_dataset = workbench_dataset.strip()
        self.report_folder = Path(report_folder) if report_folder else None
        self.inputs_dir = (self.report_folder / "inputs") if self.report_folder else None
        if self.inputs_dir:
            try:
                self.inputs_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        self.rows: List[dict] = []
        self._build_ui(initial_imports or [])

    def _get_input_files(self) -> List[str]:
        """Return list of CSV and Excel filenames located in the report's inputs folder."""
        if not self.inputs_dir or not self.inputs_dir.exists():
            return []
        try:
            exts = {".csv", ".xlsx"}
            return sorted([f.name for f in self.inputs_dir.iterdir() if f.is_file() and f.suffix.lower() in exts])
        except Exception:
            return []

    def _get_input_csv_files(self) -> List[str]:
        return self._get_input_files()

    def _inspect_sheets(self, file_path: str) -> List[str]:
        """Return list of sheet names from an Excel workbook."""
        p = Path(file_path)
        if not p.is_absolute() and self.inputs_dir:
            cand = self.inputs_dir / p
            if cand.exists():
                p = cand
        if not p.exists() or p.suffix.lower() not in (".xlsx", ".xls"):
            return []
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), read_only=True)
            names = wb.sheetnames
            wb.close()
            return names
        except Exception:
            return []

    def _compute_auto_table(self, file_path: str) -> str:
        """Derive projectid.dataset.csvtablename from the CSV or Excel file path and workbench dataset.

        Strips away variable expressions enclosed in '%...%' and any trailing '-' or '_' prior to them.
        Example: test-import-%YYYYMM%.csv -> test_import
        """
        if not file_path:
            return ""
        stem = Path(file_path).stem
        # Strip away any %...% tokens and any '-' or '_' immediately preceding them
        clean_stem = re.sub(r"[-_]?%[^%]+%", "", stem)
        # Strip any trailing '-' or '_' left over
        clean_stem = clean_stem.rstrip("-_")
        # Replace remaining non-alphanumeric (except underscores and hyphens in table names)
        clean_stem = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in clean_stem)
        if not clean_stem:
            clean_stem = "imported_table"

        if self.workbench_dataset:
            wb_prefix = self.workbench_dataset.rstrip(".") + "."
        else:
            wb_prefix = "projectid.dataset."
        return f"{wb_prefix}{clean_stem}"

    def _build_ui(self, initial_imports: List[dict]):
        main_layout = QVBoxLayout(self)

        header_label = QLabel(
            "<b>Configure File Import:</b><br>"
            "<i>Specify CSV or Excel (.xlsx) file, sheet, headers, schema mode, and destination BigQuery output table.</i>"
        )
        header_label.setWordWrap(True)
        main_layout.addWidget(header_label)

        # Scroll area for rows
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignTop)
        self.container_layout.setSpacing(12)
        scroll.setWidget(self.container)
        main_layout.addWidget(scroll)

        # Bottom buttons
        bottom_bar = QHBoxLayout()
        add_btn = QPushButton("➕ Add File")
        add_btn.setToolTip("Add another file import section")
        add_btn.clicked.connect(lambda: self._add_row())
        bottom_bar.addWidget(add_btn)
        bottom_bar.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("font-weight: bold; background-color: #2b78e4; color: white;")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom_bar.addWidget(cancel_btn)
        bottom_bar.addWidget(ok_btn)
        main_layout.addLayout(bottom_bar)

        # Populate rows
        if initial_imports:
            for item in initial_imports:
                self._add_row(
                    file_path=item.get("file_path") or item.get("csv_path", ""),
                    has_headers=item.get("has_headers", True),
                    output_table=item.get("output_table", ""),
                    sheet_name=item.get("sheet_name"),
                    schema_mode=item.get("schema_mode", "auto"),
                    manual_schema=item.get("manual_schema"),
                )
        else:
            self._add_row()

    def _add_row(
        self,
        file_path: str = "",
        has_headers: bool = True,
        output_table: str = "",
        sheet_name: Optional[str] = None,
        schema_mode: str = "auto",
        manual_schema: Optional[List[dict]] = None,
        **kwargs,
    ):
        if not file_path and "csv_path" in kwargs:
            file_path = kwargs["csv_path"] or ""
        if not isinstance(file_path, str):
            file_path = ""
        if not isinstance(output_table, str):
            output_table = ""
        if not isinstance(manual_schema, list):
            manual_schema = []
        if schema_mode not in ("auto", "manual"):
            schema_mode = "auto"

        sec_num = len(self.rows) + 1
        section_box = QGroupBox(f"File Import #{sec_num}", self.container)
        section_box.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #555; border-radius: 6px; margin-top: 10px; padding: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #ddd; }"
        )
        row_layout = QVBoxLayout(section_box)
        row_layout.setSpacing(8)

        # Row 1: File path (editable combo) + square Browse button + Headers checkbox + square Delete button
        r1 = QHBoxLayout()
        lbl_file = QLabel("File:")
        lbl_file.setFixedWidth(85)
        r1.addWidget(lbl_file)

        path_combo = QComboBox(self.container)
        path_combo.setEditable(True)
        path_combo.setInsertPolicy(QComboBox.NoInsert)
        path_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        path_combo.lineEdit().setPlaceholderText("Select or enter CSV/XLSX filename or path...")

        # Populate dropdown with available CSV/XLSX files in inputs folder
        input_files = self._get_input_files()
        for f in input_files:
            path_combo.addItem(f)

        if file_path:
            idx = path_combo.findText(file_path)
            if idx >= 0:
                path_combo.setCurrentIndex(idx)
            else:
                path_combo.setEditText(file_path)
        else:
            path_combo.setEditText("")

        r1.addWidget(path_combo, 1)

        browse_btn = QPushButton("📁")
        browse_btn.setToolTip("Browse for CSV or Excel file...")
        browse_btn.setFixedSize(30, 30)
        r1.addWidget(browse_btn)

        headers_cb = QCheckBox("Headers")
        headers_cb.setChecked(has_headers)
        r1.addWidget(headers_cb)

        del_btn = QPushButton("🗑")
        del_btn.setToolTip("Remove this file import section")
        del_btn.setFixedSize(30, 30)
        r1.addWidget(del_btn)

        # Row 2: Output table text box
        r2 = QHBoxLayout()
        lbl_table = QLabel("Output Table:")
        lbl_table.setFixedWidth(85)
        r2.addWidget(lbl_table)
        table_edit = QLineEdit(output_table)
        table_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        table_edit.setPlaceholderText("projectid.dataset.tablename")
        r2.addWidget(table_edit, 1)

        # Row 3: Sheet selector + Schema Mode + Configure Schema Button
        r3 = QHBoxLayout()
        lbl_sheet = QLabel("Sheet:")
        lbl_sheet.setFixedWidth(85)
        r3.addWidget(lbl_sheet)

        sheet_combo = QComboBox(self.container)
        sheet_combo.setMinimumWidth(150)
        sheet_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        r3.addWidget(sheet_combo, 1)

        r3.addSpacing(15)

        lbl_schema = QLabel("Schema:")
        lbl_schema.setFixedWidth(55)
        r3.addWidget(lbl_schema)

        schema_combo = QComboBox(self.container)
        schema_combo.addItems(["Auto Detect", "Manual"])
        schema_combo.setCurrentText("Manual" if schema_mode == "manual" else "Auto Detect")
        schema_combo.setFixedWidth(110)
        r3.addWidget(schema_combo)

        schema_btn = QPushButton("⚙ Configure Schema...")
        schema_btn.setStyleSheet(
            "QPushButton:disabled { color: #6e7681; background-color: rgba(45, 49, 56, 0.4); border: 1px solid #383c44; }"
        )
        schema_btn.setToolTip("Open manual schema field definition editor")
        r3.addWidget(schema_btn)

        row_layout.addLayout(r1)
        row_layout.addLayout(r2)
        row_layout.addLayout(r3)

        path_combo.text = path_combo.currentText
        path_combo.setText = path_combo.setEditText

        row_data = {
            "widget": section_box,
            "path_combo": path_combo,
            "path_edit": path_combo,
            "headers_cb": headers_cb,
            "table_edit": table_edit,
            "sheet_combo": sheet_combo,
            "schema_mode_combo": schema_combo,
            "schema_btn": schema_btn,
            "del_btn": del_btn,
            "manual_schema": list(manual_schema),
        }

        def update_schema_btn_label():
            cnt = len(row_data["manual_schema"])
            is_manual = "manual" in schema_combo.currentText().lower()
            schema_btn.setEnabled(is_manual)
            if is_manual:
                if cnt > 0:
                    schema_btn.setText(f"⚙ Schema ({cnt} fields)...")
                else:
                    schema_btn.setText("⚙ Configure Schema...")
                schema_btn.setToolTip("Open manual schema field definition editor")
            else:
                schema_btn.setText("Configure Schema...")
                schema_btn.setToolTip("Select 'Manual' schema mode to configure fields")

        def update_sheet_combo():
            raw_path = path_combo.currentText().strip()
            sheets = self._inspect_sheets(raw_path)
            sheet_combo.blockSignals(True)
            sheet_combo.clear()
            if sheets:
                sheet_combo.setEnabled(True)
                sheet_combo.addItems(sheets)
                target_sheet = sheet_name or ""
                if target_sheet and target_sheet in sheets:
                    sheet_combo.setCurrentText(target_sheet)
                else:
                    sheet_combo.setCurrentIndex(0)
            else:
                sheet_combo.setEnabled(False)
                if Path(raw_path).suffix.lower() in (".xlsx", ".xls"):
                    sheet_combo.addItem("(No sheets found)")
                else:
                    sheet_combo.addItem("(CSV - No sheets)")
            sheet_combo.blockSignals(False)

        def pick_file():
            start_dir = str(self.inputs_dir) if self.inputs_dir and self.inputs_dir.exists() else ""
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "Select Data File",
                start_dir,
                "Data Files (*.csv *.xlsx);;CSV Files (*.csv);;Excel Files (*.xlsx);;All Files (*)",
            )
            if selected:
                sel_path = Path(selected)
                if self.inputs_dir and sel_path.parent.resolve() == self.inputs_dir.resolve():
                    display_val = sel_path.name
                else:
                    display_val = selected
                path_combo.setEditText(display_val)
                auto_val = self._compute_auto_table(display_val)
                if auto_val:
                    table_edit.setText(auto_val)
                    table_edit._is_auto_populated = True
                update_sheet_combo()

        browse_btn.clicked.connect(pick_file)

        def on_path_changed(new_path: str):
            curr_table = table_edit.text().strip()
            if not curr_table or getattr(table_edit, "_is_auto_populated", False):
                auto_val = self._compute_auto_table(new_path)
                if auto_val:
                    table_edit.setText(auto_val)
                    table_edit._is_auto_populated = True
            update_sheet_combo()

        path_combo.currentTextChanged.connect(on_path_changed)

        def open_schema_editor():
            f_text = path_combo.currentText().strip()
            resolved_p = Path(f_text)
            if not resolved_p.is_absolute() and self.inputs_dir:
                cand = self.inputs_dir / resolved_p
                if cand.exists():
                    resolved_p = cand
            sh_text = sheet_combo.currentText().strip()
            if sh_text.startswith("(") or not sheet_combo.isEnabled():
                sh_text = None
            tbl_text = table_edit.text().strip()
            dlg = SchemaEditorDialog(
                parent=self,
                current_schema=row_data["manual_schema"],
                file_path=str(resolved_p) if resolved_p.exists() else None,
                sheet_name=sh_text,
                has_headers=headers_cb.isChecked(),
                table_name=tbl_text,
            )
            if dlg.exec() == QDialog.Accepted:
                row_data["manual_schema"] = dlg.get_schema()
                if row_data["manual_schema"]:
                    schema_combo.setCurrentText("Manual")
                update_schema_btn_label()

        def on_schema_mode_changed(mode: str):
            update_schema_btn_label()
            if mode == "Manual" and not row_data["manual_schema"]:
                open_schema_editor()

        schema_combo.currentTextChanged.connect(on_schema_mode_changed)
        schema_btn.clicked.connect(open_schema_editor)

        # Initial updates
        update_sheet_combo()
        update_schema_btn_label()

        self.rows.append(row_data)
        self.container_layout.addWidget(section_box)
        section_box.show()
        self.container.adjustSize()

        def remove_this_row():
            if row_data in self.rows:
                self.rows.remove(row_data)
                section_box.setParent(None)
                section_box.deleteLater()
                self._update_section_titles()
                self.container.adjustSize()

        del_btn.clicked.connect(remove_this_row)
        self._update_section_titles()

    def _update_section_titles(self):
        for idx, r in enumerate(self.rows):
            r["widget"].setTitle(f"File Import #{idx + 1}")
            # Only allow deleting if more than 1 section
            r["del_btn"].setEnabled(len(self.rows) > 1)

    def get_imports(self) -> List[dict]:
        results = []
        for r in self.rows:
            f_path = r["path_combo"].currentText().strip()
            table = r["table_edit"].text().strip()
            headers = r["headers_cb"].isChecked()
            sh = r["sheet_combo"].currentText().strip() if r.get("sheet_combo") else ""
            if not r["sheet_combo"].isEnabled() or sh.startswith("(") or not sh:
                sh = None
            schema_mode = "manual" if r["schema_mode_combo"].currentText() == "Manual" else "auto"
            manual_schema = r.get("manual_schema") or []
            if f_path or table:
                results.append({
                    "file_path": f_path,
                    "csv_path": f_path,
                    "has_headers": headers,
                    "output_table": table,
                    "sheet_name": sh,
                    "schema_mode": schema_mode,
                    "manual_schema": manual_schema if schema_mode == "manual" else None,
                })
        return results


ImportCsvDialog = ImportFilesDialog

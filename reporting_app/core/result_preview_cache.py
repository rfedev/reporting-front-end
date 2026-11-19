"""Session-scoped temp-file storage and fetchers for query and table Results Preview."""

from __future__ import annotations

import atexit
import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from google.api_core.exceptions import Forbidden, NotFound, Unauthorized
from google.auth.exceptions import GoogleAuthError
from google.cloud import bigquery

from reporting_app.core.bigquery_run import (
    DEFAULT_BQ_JOB_RESULT_TIMEOUT,
    DEFAULT_BQ_QUERY_TIMEOUT,
    format_bigquery_error,
    substitute_parameters,
)
from reporting_app.core.sql_parser import extract_project_id_from_sql

logger = logging.getLogger(__name__)


@dataclass
class ResultPreviewData:
    """Serializable preview payload for a BigQuery table/query result or output CSV."""

    key: str
    display_name: str
    columns: List[str] = field(default_factory=list)
    column_types: List[str] = field(default_factory=list)
    column_modes: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    total_rows: Optional[int] = None
    row_limit: int = 100
    truncated: bool = False
    source: str = "bigquery"  # "bigquery" | "csv"
    source_detail: str = "BigQuery Table"
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResultPreviewData":
        return cls(
            key=str(data.get("key", "")),
            display_name=str(data.get("display_name", "")),
            columns=[str(c) for c in (data.get("columns") or [])],
            column_types=[str(t) for t in (data.get("column_types") or [])],
            column_modes=[str(m) for m in (data.get("column_modes") or [])],
            rows=[list(r) if isinstance(r, (list, tuple)) else [r] for r in (data.get("rows") or [])],
            total_rows=int(data["total_rows"]) if data.get("total_rows") is not None else None,
            row_limit=int(data.get("row_limit") or 100),
            truncated=bool(data.get("truncated", False)),
            source=str(data.get("source", "bigquery")),
            source_detail=str(data.get("source_detail", "BigQuery Table")),
            fetched_at=str(data.get("fetched_at") or datetime.now().isoformat(timespec="seconds")),
        )


def serialize_preview_value(val: Any) -> Any:
    """Convert a BigQuery/pandas cell value into a JSON-safe primitive while preserving None as SQL NULL."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if math.isnan(val):
            return None
        if math.isinf(val):
            return str(val)
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")

    # Guard against pandas.NA / pandas.NaT without calling pd.isna on lists/arrays
    val_type_name = type(val).__name__
    if val_type_name in ("NAType", "NaTType"):
        return None

    if isinstance(val, (dict, list, tuple)):
        try:
            return json.dumps(val, default=str, ensure_ascii=False)
        except Exception:
            return str(val)

    # Unwrap numpy/pandas scalar types cleanly
    if hasattr(val, "item") and callable(getattr(val, "item")) and not hasattr(val, "__len__"):
        try:
            return serialize_preview_value(val.item())
        except Exception:
            pass

    return str(val)


def extract_schema_metadata(schema: Any) -> Tuple[List[str], List[str], List[str]]:
    """Extract column names, types, and modes from a BigQuery schema sequence."""
    names: List[str] = []
    types: List[str] = []
    modes: List[str] = []
    if not schema:
        return names, types, modes

    try:
        fields = list(schema)
    except TypeError:
        return names, types, modes

    for field_obj in fields:
        fname = getattr(field_obj, "name", None)
        if not isinstance(fname, str):
            continue
        ftype = getattr(field_obj, "field_type", "")
        fmode = getattr(field_obj, "mode", "")
        names.append(fname)
        types.append(str(ftype).upper() if isinstance(ftype, str) else "")
        modes.append(str(fmode).upper() if isinstance(fmode, str) else "")
    return names, types, modes


def normalize_bq_table_ref(
    table_name: str,
    default_project: str = "",
    workbench_dataset: str = "",
) -> str:
    """Normalize a BigQuery table reference to project.dataset.table when defaults are available."""
    cleaned = str(table_name or "").strip().strip("`\"'")
    if not cleaned:
        return ""

    # Handle legacy project:dataset.table notation
    if ":" in cleaned and "." in cleaned:
        cleaned = cleaned.replace(":", ".", 1)

    parts = [p.strip().strip("`") for p in cleaned.split(".") if p.strip()]
    wb_clean = str(workbench_dataset or "").strip().strip("`")
    wb_project = wb_clean.split(".")[0].strip() if "." in wb_clean else ""
    eff_project = str(default_project or "").strip() or wb_project

    if len(parts) == 1 and wb_clean:
        return f"{wb_clean.rstrip('.')}.{parts[0]}"
    if len(parts) == 2 and eff_project:
        return f"{eff_project}.{parts[0]}.{parts[1]}"
    return ".".join(parts)


def make_bq_cache_key(table_ref: str) -> str:
    """Build canonical cache key for a BigQuery table address."""
    cleaned = str(table_ref or "").strip().strip("`\"'").replace(":", ".", 1)
    return f"bq:{cleaned.lower()}"


def make_csv_cache_key(csv_path: str | Path) -> str:
    """Build canonical cache key for a CSV file path."""
    try:
        resolved = str(Path(csv_path).resolve())
    except Exception:
        resolved = str(csv_path)
    return f"csv:{resolved.lower()}"


class ResultPreviewCache:
    """Thread-safe session cache persisting preview payloads as temporary JSON files."""

    def __init__(self, temp_dir: Optional[Path] = None):
        self._temp_dir: Path = (
            Path(temp_dir)
            if temp_dir is not None
            else Path(tempfile.mkdtemp(prefix="reporting_app_preview_"))
        )
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index: Dict[str, Path] = {}
        self._cleaned_up = False
        atexit.register(self.cleanup)

    @property
    def temp_dir(self) -> Path:
        return self._temp_dir

    def _file_path_for_key(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return self._temp_dir / f"preview_{digest}.json"

    def put(self, data: ResultPreviewData) -> Path:
        """Persist preview data to its session temp file and return the file path."""
        payload = data.to_dict()
        serialized = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            if self._cleaned_up:
                self._cleaned_up = False
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            target_path = self._file_path_for_key(data.key)
            tmp_path = self._temp_dir / f".{target_path.name}.{threading.get_ident()}.tmp"
            tmp_path.write_text(serialized, encoding="utf-8")
            os.replace(tmp_path, target_path)
            self._index[data.key] = target_path
            return target_path

    def get(
        self,
        key: str,
        fallback_keys: Optional[List[str]] = None,
    ) -> Optional[ResultPreviewData]:
        """Load cached preview data from disk by key (or fallback keys), returning None if absent."""
        candidates = [key] + [k for k in (fallback_keys or []) if k and k != key]
        with self._lock:
            for cand in candidates:
                path = self._index.get(cand) or self._file_path_for_key(cand)
                if path.exists() and path.is_file():
                    try:
                        raw = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(raw, dict):
                            return ResultPreviewData.from_dict(raw)
                    except Exception as exc:
                        logger.debug(f"Failed to read cached preview temp file {path}: {exc}")
        return None

    def has(
        self,
        key: str,
        fallback_keys: Optional[List[str]] = None,
    ) -> bool:
        """Return True if a preview entry exists in temp storage."""
        return self.get(key, fallback_keys=fallback_keys) is not None

    def remove(self, key: str) -> None:
        """Remove a single cached preview entry and delete its temp file."""
        with self._lock:
            path = self._index.pop(key, None) or self._file_path_for_key(key)
            try:
                if path.exists():
                    path.unlink()
            except Exception as exc:
                logger.debug(f"Failed to remove preview temp file {path}: {exc}")

    def clear(self) -> None:
        """Delete all cached preview temp files while keeping the temp directory usable."""
        with self._lock:
            self._index.clear()
            if self._temp_dir.exists():
                for child in self._temp_dir.iterdir():
                    try:
                        if child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink()
                    except Exception:
                        pass

    def cleanup(self) -> None:
        """Delete all temporary preview files and remove the session temp directory."""
        with self._lock:
            self._cleaned_up = True
            self._index.clear()
            shutil.rmtree(self._temp_dir, ignore_errors=True)


def _resolve_project_and_client(
    table_or_sql: str = "",
    project_id: Optional[str] = None,
    workbench_dataset: str = "",
    client: Optional[Any] = None,
) -> Tuple[str, Any, str]:
    """Resolve effective BigQuery project, workbench dataset, and Client instance."""
    resolved_wb = str(workbench_dataset or "").strip()
    if not resolved_wb:
        try:
            from reporting_app.persistence.database import DatabaseManager
            from reporting_app.persistence.repository import Repository

            resolved_wb = Repository(DatabaseManager()).get_workbench_dataset().strip()
        except Exception:
            resolved_wb = ""

    resolved_project = str(project_id or "").strip()
    if not resolved_project and table_or_sql:
        cleaned = str(table_or_sql).strip().strip("`\"'")
        if "." in cleaned and cleaned.count(".") >= 2 and " " not in cleaned:
            resolved_project = cleaned.split(".")[0].strip()

    if not resolved_project and resolved_wb and "." in resolved_wb:
        resolved_project = resolved_wb.split(".")[0].strip()

    if not resolved_project and table_or_sql:
        try:
            resolved_project = extract_project_id_from_sql(table_or_sql) or ""
        except Exception:
            pass

    if resolved_project and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = resolved_project

    if client is None:
        try:
            client = bigquery.Client(project=resolved_project) if resolved_project else bigquery.Client()
        except Exception as exc:
            raise RuntimeError(format_bigquery_error(exc, resolved_project or None)) from exc

    active_project = resolved_project or (getattr(client, "project", "") if isinstance(getattr(client, "project", None), str) else "")
    if not active_project:
        raise RuntimeError(
            "No Google Cloud project specified or resolved. "
            "Configure Workbench Dataset in Settings or use project.dataset.table addresses."
        )
    return active_project, client, resolved_wb


def collect_rows_from_iterator(
    row_iter: Iterable[Any],
    limit: int,
    schema: Any = None,
) -> Tuple[List[str], List[str], List[str], List[List[Any]]]:
    """Collect up to ``limit`` serialized rows and schema metadata from a BigQuery RowIterator."""
    max_rows = max(1, int(limit or 100))
    schema_to_use = schema if schema is not None else getattr(row_iter, "schema", None)
    cols, col_types, col_modes = extract_schema_metadata(schema_to_use)
    rows: List[List[Any]] = []

    for idx, row in enumerate(row_iter):
        if idx >= max_rows:
            break
        if hasattr(row, "keys") and callable(row.keys) and hasattr(row, "values") and callable(row.values):
            row_keys = [str(k) for k in row.keys()]
            if not cols:
                cols = row_keys
            if len(row_keys) == len(cols):
                row_vals = [serialize_preview_value(v) for v in row.values()]
            else:
                row_map = dict(row)
                row_vals = [serialize_preview_value(row_map.get(c)) for c in cols]
        elif isinstance(row, (list, tuple)):
            if not cols:
                cols = [f"col_{i + 1}" for i in range(len(row))]
            row_vals = [serialize_preview_value(v) for v in row]
        else:
            if not cols:
                cols = ["value"]
            row_vals = [serialize_preview_value(row)]
        rows.append(row_vals)

    return cols, col_types, col_modes, rows


def build_preview_from_dataframe(
    df: Any,
    limit: int,
    key: str,
    display_name: str,
    schema: Any = None,
    total_rows: Optional[int] = None,
    source: str = "bigquery",
    source_detail: str = "BigQuery Query",
) -> ResultPreviewData:
    """Build a ResultPreviewData object from the head of a pandas DataFrame without extra queries."""
    max_rows = max(1, int(limit or 100))
    schema_cols, col_types, col_modes = extract_schema_metadata(schema)
    df_cols = [str(c) for c in list(df.columns)] if hasattr(df, "columns") else []
    cols = schema_cols if (schema_cols and len(schema_cols) == len(df_cols)) else (df_cols or schema_cols)

    if len(col_types) != len(cols):
        col_types = []
        col_modes = []

    head_df = df.head(max_rows)
    rows: List[List[Any]] = [
        [serialize_preview_value(val) for val in row_tuple]
        for row_tuple in head_df.itertuples(index=False, name=None)
    ]
    eff_total = total_rows if total_rows is not None else len(df)
    return ResultPreviewData(
        key=key,
        display_name=display_name,
        columns=cols,
        column_types=col_types,
        column_modes=col_modes,
        rows=rows,
        total_rows=int(eff_total) if eff_total is not None else None,
        row_limit=max_rows,
        truncated=bool(eff_total is not None and eff_total > len(rows)),
        source=source,
        source_detail=source_detail,
    )


def fetch_bigquery_table_preview(
    table_name: str,
    limit: int = 100,
    project_id: Optional[str] = None,
    workbench_dataset: str = "",
    client: Optional[Any] = None,
    cache_key: Optional[str] = None,
) -> ResultPreviewData:
    """Fetch up to ``limit`` rows from a BigQuery table using free list_rows() with view fallback."""
    max_rows = max(1, int(limit or 100))
    active_project, client, resolved_wb = _resolve_project_and_client(
        table_or_sql=table_name,
        project_id=project_id,
        workbench_dataset=workbench_dataset,
        client=client,
    )
    table_ref = normalize_bq_table_ref(
        table_name,
        default_project=active_project,
        workbench_dataset=resolved_wb,
    )
    if not table_ref:
        raise ValueError("Empty BigQuery table reference.")

    table_obj = None
    table_type = ""
    known_num_rows: Optional[int] = None
    table_schema = None

    if hasattr(client, "get_table"):
        try:
            table_obj = client.get_table(table_ref)
            raw_type = getattr(table_obj, "table_type", "")
            table_type = raw_type.upper() if isinstance(raw_type, str) else ""
            raw_num = getattr(table_obj, "num_rows", None)
            if isinstance(raw_num, int) and not isinstance(raw_num, bool):
                known_num_rows = raw_num
            table_schema = getattr(table_obj, "schema", None)
        except (Forbidden, Unauthorized, GoogleAuthError, NotFound) as exc:
            raise RuntimeError(format_bigquery_error(exc, active_project)) from exc
        except Exception as exc:
            logger.debug(f"get_table({table_ref}) non-fatal note, continuing with list_rows: {exc}")

    def _run_select_fallback() -> ResultPreviewData:
        sql = f"SELECT * FROM `{table_ref}` LIMIT {max_rows}"
        try:
            qjob = client.query(
                sql,
                project=active_project,
                job_retry=None,
                timeout=DEFAULT_BQ_QUERY_TIMEOUT,
            )
            res_iter = qjob.result(
                max_results=max_rows,
                job_retry=None,
                timeout=DEFAULT_BQ_JOB_RESULT_TIMEOUT,
            )
        except Exception as exc:
            raise RuntimeError(format_bigquery_error(exc, active_project)) from exc

        cols, col_types, col_modes, rows = collect_rows_from_iterator(
            res_iter,
            max_rows,
            schema=getattr(res_iter, "schema", None) or table_schema,
        )
        raw_total = getattr(res_iter, "total_rows", None)
        total_rows = int(raw_total) if isinstance(raw_total, int) and not isinstance(raw_total, bool) else known_num_rows
        truncated = bool(
            (total_rows is not None and total_rows > len(rows))
            or (total_rows is None and len(rows) >= max_rows)
        )
        return ResultPreviewData(
            key=cache_key or make_bq_cache_key(table_ref),
            display_name=table_ref,
            columns=cols,
            column_types=col_types,
            column_modes=col_modes,
            rows=rows,
            total_rows=total_rows,
            row_limit=max_rows,
            truncated=truncated,
            source="bigquery",
            source_detail="BigQuery View" if table_type == "VIEW" else "BigQuery Table",
        )

    if table_type in ("VIEW", "EXTERNAL") or not hasattr(client, "list_rows"):
        return _run_select_fallback()

    try:
        list_target = table_obj if (table_obj is not None and isinstance(getattr(table_obj, "table_id", None), str)) else table_ref
        row_iter = client.list_rows(list_target, max_results=max_rows)
    except (Forbidden, Unauthorized, GoogleAuthError, NotFound) as exc:
        raise RuntimeError(format_bigquery_error(exc, active_project)) from exc
    except Exception as exc:
        msg_lower = str(exc).lower()
        if any(tok in msg_lower for tok in ("view", "cannot list", "not supported", "400", "external")):
            logger.info(f"list_rows unsupported for {table_ref}, falling back to SELECT * LIMIT {max_rows}")
            return _run_select_fallback()
        raise RuntimeError(format_bigquery_error(exc, active_project)) from exc

    cols, col_types, col_modes, rows = collect_rows_from_iterator(
        row_iter,
        max_rows,
        schema=getattr(row_iter, "schema", None) or table_schema,
    )
    raw_total = getattr(row_iter, "total_rows", None)
    total_rows = int(raw_total) if isinstance(raw_total, int) and not isinstance(raw_total, bool) else known_num_rows
    truncated = bool(
        (total_rows is not None and total_rows > len(rows))
        or (total_rows is None and len(rows) >= max_rows)
    )
    return ResultPreviewData(
        key=cache_key or make_bq_cache_key(table_ref),
        display_name=table_ref,
        columns=cols,
        column_types=col_types,
        column_modes=col_modes,
        rows=rows,
        total_rows=total_rows,
        row_limit=max_rows,
        truncated=truncated,
        source="bigquery",
        source_detail="BigQuery Table",
    )


def load_csv_preview(
    csv_path: str | Path,
    limit: int = 100,
    display_name: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> ResultPreviewData:
    """Read header and up to ``limit`` rows from a local CSV file without loading the full file."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Output CSV file not found: {path}")

    max_rows = max(1, int(limit or 100))
    columns: List[str] = []
    collected: List[List[Any]] = []
    truncated = False

    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return ResultPreviewData(
                key=cache_key or make_csv_cache_key(path),
                display_name=display_name or path.name,
                columns=[],
                column_types=[],
                column_modes=[],
                rows=[],
                total_rows=0,
                row_limit=max_rows,
                truncated=False,
                source="csv",
                source_detail="CSV",
            )

        columns = [str(col) for col in header]
        num_cols = len(columns)
        for row in reader:
            if len(collected) >= max_rows:
                truncated = True
                break
            if len(row) < num_cols:
                padded = list(row) + [""] * (num_cols - len(row))
            elif len(row) > num_cols:
                padded = list(row[:num_cols])
            else:
                padded = list(row)
            collected.append(padded)

    return ResultPreviewData(
        key=cache_key or make_csv_cache_key(path),
        display_name=display_name or path.name,
        columns=columns,
        column_types=[],
        column_modes=[],
        rows=collected,
        total_rows=None if truncated else len(collected),
        row_limit=max_rows,
        truncated=truncated,
        source="csv",
        source_detail="CSV",
    )


def fetch_bigquery_select_preview(
    sql_script_path: str | Path,
    target_csv_paths: List[Path],
    parameters: Optional[Dict[str, str]] = None,
    limit: int = 100,
    project_id: Optional[str] = None,
    workbench_dataset: str = "",
    client: Optional[Any] = None,
) -> List[ResultPreviewData]:
    """Run a SELECT SQL script in BigQuery and capture up to ``limit`` rows without writing CSVs."""
    sql_path = Path(sql_script_path)
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    max_rows = max(1, int(limit or 100))
    raw_sql = sql_path.read_text(encoding="utf-8", errors="replace")
    substituted_sql = substitute_parameters(raw_sql, parameters or {})
    if not substituted_sql.strip():
        raise ValueError(f"SQL file '{sql_path.name}' is empty.")

    active_project, client, _ = _resolve_project_and_client(
        table_or_sql=substituted_sql,
        project_id=project_id,
        workbench_dataset=workbench_dataset,
        client=client,
    )

    try:
        query_job = client.query(
            substituted_sql,
            project=active_project,
            job_retry=None,
            timeout=DEFAULT_BQ_QUERY_TIMEOUT,
        )
        results = query_job.result(
            max_results=max_rows,
            job_retry=None,
            timeout=DEFAULT_BQ_JOB_RESULT_TIMEOUT,
        )
    except Exception as exc:
        raise RuntimeError(format_bigquery_error(exc, active_project)) from exc

    child_jobs: List[Any] = []
    try:
        if hasattr(client, "list_jobs"):
            raw_jobs = client.list_jobs(parent_job=query_job)
            if hasattr(raw_jobs, "__iter__"):
                child_jobs = list(raw_jobs)
    except Exception as exc:
        logger.debug(f"Checking multi-statement child jobs during preview: {exc}")

    if len(child_jobs) > 1:
        try:
            child_jobs.reverse()
        except Exception:
            pass

    result_sets: List[Any] = []
    for child_job in child_jobs:
        if hasattr(child_job, "destination") and child_job.destination:
            try:
                child_res = child_job.result(max_results=max_rows)
                raw_tot = getattr(child_res, "total_rows", 0)
                if raw_tot is not None and raw_tot > 0:
                    result_sets.append(child_res)
            except Exception as exc:
                logger.debug(f"Error fetching child job preview result: {exc}")

    if not result_sets:
        result_sets = [results]

    previews: List[ResultPreviewData] = []
    for idx, res_iter in enumerate(result_sets):
        if idx < len(target_csv_paths):
            csv_target = Path(target_csv_paths[idx])
        elif target_csv_paths:
            csv_target = Path(target_csv_paths[-1]).with_name(f"{sql_path.stem}_{idx + 1}.csv")
        else:
            csv_target = Path(f"{sql_path.stem}.csv")

        cols, col_types, col_modes, rows = collect_rows_from_iterator(res_iter, max_rows)
        raw_total = getattr(res_iter, "total_rows", None)
        total_rows = int(raw_total) if isinstance(raw_total, int) and not isinstance(raw_total, bool) else None
        truncated = bool(
            (total_rows is not None and total_rows > len(rows))
            or (total_rows is None and len(rows) >= max_rows)
        )
        previews.append(
            ResultPreviewData(
                key=make_csv_cache_key(csv_target),
                display_name=csv_target.name,
                columns=cols,
                column_types=col_types,
                column_modes=col_modes,
                rows=rows,
                total_rows=total_rows,
                row_limit=max_rows,
                truncated=truncated,
                source="bigquery",
                source_detail="BigQuery Query",
            )
        )

    return previews

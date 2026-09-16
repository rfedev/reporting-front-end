"""SQL parsing utilities for BigQuery SQL files using sqlglot."""

from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp


# Regex for parameter extraction {parameter_name} or :parameter_name
_PARAM_RE = re.compile(r"\{([^{}]+)\}|(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)(?!:)")

# Comment regexes for CSV output annotations
_CSV_COMMENT_RE = re.compile(
    r"^\s*(?:#|--|/\*)\s*out?put\s*:\s*([^\s;\*]+)",
    re.IGNORECASE,
)
_CSV_IN_COMMENT_RE = re.compile(
    r"^\s*(?:#|--|/\*|\*)\s*(?:out?put\s*:\s*)?([^\s;\*'\"]+\.csv)",
    re.IGNORECASE,
)


def strip_comments(sql: str) -> str:
    """Remove SQL comments (-- ..., /* ... */, and # ...) to avoid false positives."""
    clean = re.sub(r"--[^\n]*|#[^\n]*", "", sql)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    return clean


def clean_table_name(table_ref: str) -> str:
    """Clean backticks and whitespace from table reference."""
    clean = table_ref.strip()
    if clean.startswith("`") and clean.endswith("`"):
        clean = clean[1:-1].strip()
    return clean


def scan_query_parameters(sql: str) -> List[str]:
    """Scan SQL text for parameters in curly brackets like {startDate} or :param.

    Returns a list of unique parameter names preserving appearance order.
    """
    cleaned = strip_comments(sql)
    found: List[str] = []
    seen: Set[str] = set()
    for match in _PARAM_RE.finditer(cleaned):
        param = (match.group(1) or match.group(2) or "").strip()
        if param and param not in seen:
            seen.add(param)
            found.append(param)
    return found


def extract_csv_filename_from_comment(line: str) -> Optional[str]:
    """Extract CSV filename from a SQL comment line.

    Supports #, --, and /* ... */ comments.
    Matches explicit 'output: filename.csv' (or 'ouput:') as well as comments
    naming a .csv file directly.
    """
    clean_line = line.strip()
    if not clean_line.startswith(("#", "--", "/*", "*")):
        return None

    m = _CSV_COMMENT_RE.match(clean_line)
    if m:
        fname = m.group(1).strip().strip("'\"*").rstrip("*/").strip()
        if not fname.lower().endswith(".csv"):
            fname = f"{fname}.csv"
        return fname

    m2 = _CSV_IN_COMMENT_RE.match(clean_line)
    if m2:
        return m2.group(1).strip().strip("'\"*").rstrip("*/").strip()

    return None


def get_next_available_table_csv(existing_names: Iterable[str]) -> str:
    r"""Determine the next available table_NN.csv filename.

    Finds existing numbers from names matching table_(\d+) and increments from the maximum.
    """
    used_numbers: Set[int] = set()
    used_names_lower: Set[str] = set()
    for name in existing_names:
        if not name:
            continue
        clean = Path(str(name)).name.lower()
        used_names_lower.add(clean)
        m = re.search(r"table_(\d+)", clean)
        if m:
            try:
                used_numbers.add(int(m.group(1)))
            except ValueError:
                pass

    next_num = max(used_numbers) + 1 if used_numbers else 1
    candidate = f"table_{next_num:02d}.csv"
    while candidate.lower() in used_names_lower:
        next_num += 1
        candidate = f"table_{next_num:02d}.csv"
    return candidate


def split_sql_statements(sql: str) -> List[Tuple[int, int, str]]:
    """Split SQL into individual statements with start/end character offsets using sqlglot."""
    try:
        tokens = sqlglot.tokenize(sql, dialect="bigquery")
    except Exception:
        tokens = []

    if not tokens:
        trimmed = sql.strip()
        return [(0, len(sql), sql)] if trimmed else []

    statements: List[Tuple[int, int, str]] = []
    start = 0
    for tok in tokens:
        if tok.token_type == sqlglot.TokenType.SEMICOLON:
            chunk = sql[start:tok.end].strip()
            if chunk:
                statements.append((start, tok.end, sql[start:tok.end]))
            start = tok.end

    if start < len(sql):
        chunk = sql[start:].strip()
        if chunk:
            statements.append((start, len(sql), sql[start:]))

    return statements


def find_standalone_select_statements(sql: str) -> List[dict]:
    """Find all standalone SELECT statements in a SQL script using sqlglot.

    For each statement:
    - Parses with sqlglot and checks if type(statement).__name__.upper() == "SELECT".
    - Finds the first line of the SQL statement (first non-blank line).
    - Checks the lines within the statement for any existing comment containing .csv or output:.
    """
    raw_statements = split_sql_statements(sql)
    results: List[dict] = []
    select_idx = 0

    for start_char, end_char, raw_stmt in raw_statements:
        clean = strip_comments(raw_stmt).strip()
        if not clean:
            continue

        try:
            parsed = [s for s in sqlglot.parse(raw_stmt, dialect="bigquery") if s is not None]
        except Exception:
            parsed = []

        is_select = False
        if parsed:
            is_select = type(parsed[0]).__name__.upper() == "SELECT"
        else:
            is_select = bool(re.search(r"\bSELECT\b", clean, re.IGNORECASE)) and not bool(
                re.search(r"\b(?:CREATE|INSERT|UPDATE|DELETE|MERGE)\b", clean, re.IGNORECASE)
            )

        if not is_select:
            continue

        stmt_lines = raw_stmt.splitlines(keepends=True)
        first_rel_idx = 0
        while first_rel_idx < len(stmt_lines) and not stmt_lines[first_rel_idx].strip():
            first_rel_idx += 1
        if first_rel_idx >= len(stmt_lines):
            first_rel_idx = 0

        lead_chars = sum(len(stmt_lines[k]) for k in range(first_rel_idx))
        first_line_idx = sql[:start_char + lead_chars].count("\n")

        comment_line_idx = None
        csv_filename = None
        curr_offset = start_char
        for line in stmt_lines:
            line_str = line.strip()
            if line_str:
                fname = extract_csv_filename_from_comment(line_str)
                if fname:
                    csv_filename = fname
                    comment_line_idx = sql[:curr_offset].count("\n")
                    break
            curr_offset += len(line)

        results.append({
            "statement_index": select_idx,
            "raw_statement": raw_stmt,
            "first_line_idx": first_line_idx,
            "code_line_idx": first_line_idx,
            "comment_line_idx": comment_line_idx,
            "csv_filename": csv_filename,
        })
        select_idx += 1

    return results


def sync_query_csv_comments(file_path: Path, existing_csv_names: Optional[Iterable[str]] = None) -> List[str]:
    """Inspect standalone SELECT statements in a .sql file.

    If output table comment is missing, inserts '# output: table_NN.csv' at the top of the SQL statement.
    Returns list of CSV filenames for standalone SELECT statements in this file.
    """
    if not file_path.exists():
        return []

    sql = file_path.read_text(encoding="utf-8", errors="replace")
    statements = find_standalone_select_statements(sql)
    if not statements:
        return []

    lines = sql.splitlines(keepends=True)
    known_csvs = set(existing_csv_names or [])

    for s in statements:
        if s["csv_filename"]:
            known_csvs.add(s["csv_filename"])

    modified = False
    insertions: List[Tuple[int, str]] = []
    result_csvs: List[str] = []

    for s in statements:
        if s["csv_filename"]:
            result_csvs.append(s["csv_filename"])
        else:
            next_csv = get_next_available_table_csv(known_csvs)
            known_csvs.add(next_csv)
            result_csvs.append(next_csv)
            insert_idx = s.get("first_line_idx", s.get("code_line_idx", 0))
            insertions.append((insert_idx, f"# output: {next_csv}\n"))
            modified = True

    if modified:
        for line_idx, comment_str in sorted(insertions, key=lambda x: x[0], reverse=True):
            lines.insert(line_idx, comment_str)
        file_path.write_text("".join(lines), encoding="utf-8")

    return result_csvs


def update_query_csv_comment(file_path: Path, select_index: int, new_filename: str) -> None:
    """Update or insert the output table comment for the specified standalone SELECT statement in a .sql file."""
    if not file_path.exists():
        return

    sql = file_path.read_text(encoding="utf-8", errors="replace")
    statements = find_standalone_select_statements(sql)
    if select_index < 0 or select_index >= len(statements):
        return

    stmt = statements[select_index]
    lines = sql.splitlines(keepends=True)

    clean_name = Path(new_filename.strip()).name
    if not clean_name.lower().endswith(".csv"):
        clean_name = f"{clean_name}.csv"

    if stmt["comment_line_idx"] is not None and stmt["comment_line_idx"] < len(lines):
        orig_line = lines[stmt["comment_line_idx"]]
        prefix = "--" if orig_line.lstrip().startswith("--") else "#"
        lines[stmt["comment_line_idx"]] = f"{prefix} output: {clean_name}\n"
    else:
        insert_idx = stmt.get("first_line_idx", stmt.get("code_line_idx", 0))
        lines.insert(insert_idx, f"# output: {clean_name}\n")

    file_path.write_text("".join(lines), encoding="utf-8")


def scan_select_output_tables(sql: str) -> List[str]:
    """Scan SQL text for output tables referenced in SELECT statements that export to CSV."""
    statements = find_standalone_select_statements(sql)
    if not statements:
        return []

    csv_tables: List[str] = []
    seen: Set[str] = set()

    for stmt_info in statements:
        if stmt_info.get("csv_filename"):
            fname = stmt_info["csv_filename"]
            if fname not in seen:
                seen.add(fname)
                csv_tables.append(fname)
        else:
            raw = stmt_info["raw_statement"]
            primary_table = None
            try:
                parsed = [s for s in sqlglot.parse(raw, dialect="bigquery") if s is not None]
                if parsed:
                    from_clause = parsed[0].find(exp.From)
                    if from_clause:
                        tbl = from_clause.find(exp.Table)
                        if tbl:
                            primary_table = clean_table_name(tbl.sql(dialect="bigquery"))
            except Exception:
                pass

            name = primary_table or "output"
            if name not in seen:
                seen.add(name)
                csv_tables.append(name)

    return csv_tables


def extract_project_id_from_sql(sql: str) -> Optional[str]:
    """Extract BigQuery project_id based on the first 3-part table address (project.dataset.table)."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="bigquery") if s is not None]
    except Exception:
        statements = []

    for stmt in statements:
        # Check FROM clauses first
        for from_clause in stmt.find_all(exp.From):
            table = from_clause.find(exp.Table)
            if table and table.catalog:
                return clean_table_name(table.catalog)

        # Then check any table referenced in the statement
        for table in stmt.find_all(exp.Table):
            if table.catalog:
                return clean_table_name(table.catalog)

    return None


def scan_query_tables(sql: str) -> Tuple[List[str], List[str], List[str]]:
    """Determine input, output, and CSV output tables from BigQuery SQL using sqlglot.

    Input tables: tables referenced in FROM and JOIN clauses (excluding CTEs).
    Output tables: targets of CREATE TABLE, INSERT INTO, and MERGE statements.
    Output CSV tables: standalone SELECT statements exporting to CSV.

    Returns:
        Tuple of (input_tables, output_tables, output_csv_tables)
    """
    input_tables: List[str] = []
    output_tables: List[str] = []
    seen_inputs: Set[str] = set()
    seen_outputs: Set[str] = set()

    try:
        statements = [s for s in sqlglot.parse(sql, dialect="bigquery") if s is not None]
    except Exception:
        statements = []

    for stmt in statements:
        # Collect CTE names defined in this statement to exclude from external inputs
        cte_names = {clean_table_name(cte.alias_or_name) for cte in stmt.ctes}

        # Output tables: CREATE, INSERT, MERGE
        if isinstance(stmt, (exp.Create, exp.Insert, exp.Merge)):
            target = stmt.find(exp.Table)
            if target:
                out_name = clean_table_name(target.sql(dialect="bigquery"))
                if out_name and out_name not in seen_outputs:
                    seen_outputs.add(out_name)
                    output_tables.append(out_name)

        # Input tables: FROM and JOIN clauses
        for from_or_join in stmt.find_all(exp.From, exp.Join):
            table = from_or_join.find(exp.Table)
            if table:
                in_name = clean_table_name(table.sql(dialect="bigquery"))
                if (
                    in_name
                    and in_name not in cte_names
                    and in_name not in seen_inputs
                    and in_name not in seen_outputs
                ):
                    seen_inputs.add(in_name)
                    input_tables.append(in_name)

    output_csv_tables = scan_select_output_tables(sql)
    return input_tables, output_tables, output_csv_tables

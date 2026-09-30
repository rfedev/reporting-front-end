import logging
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp, TokenType

# Suppress sqlglot fallback warning logs for procedural and unsupported constructs
logging.getLogger("sqlglot").setLevel(logging.ERROR)


# Regex for parameter extraction {parameter_name} or :parameter_name
_PARAM_RE = re.compile(r"\{([^{}]+)\}|(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)(?!:)")


def _clean_table_address(tbl: exp.Table) -> str:
    """Format an exp.Table AST node into a clean table name without backticks or aliases."""
    parts = [p for p in (tbl.catalog, tbl.db, tbl.name) if p]
    return ".".join(parts) if parts else tbl.name


def _to_parseable_sql(sql: str) -> str:
    """Convert template parameters like {param} to valid SQL identifiers for sqlglot parsing."""
    return _PARAM_RE.sub("__param__", sql)


def strip_comments(sql: str) -> str:
    """Remove SQL comments (-- ..., /* ... */, and # ...) using sqlglot."""
    placeholder_map = {}

    def repl(m):
        key = f"__param_{len(placeholder_map)}__"
        placeholder_map[key] = m.group(0)
        return key

    parseable = _PARAM_RE.sub(repl, sql)
    try:
        parsed = [s for s in sqlglot.parse(parseable, dialect="bigquery") if s is not None]
        if not parsed:
            return ""
        out = ";\n".join(s.sql(dialect="bigquery", comments=False) for s in parsed)
        if not out.rstrip().endswith(";"):
            out += ";"
        for k, v in placeholder_map.items():
            out = out.replace(k, v)
        return out
    except Exception:
        clean = re.sub(r"--[^\n]*|#[^\n]*", "", sql)
        clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
        return clean


def clean_table_name(table_ref: str) -> str:
    """Clean backticks and whitespace from table reference using sqlglot Table parsing."""
    cleaned = table_ref.strip()
    if not cleaned:
        return ""
    try:
        tbl = sqlglot.to_table(cleaned, dialect="bigquery")
        return _clean_table_address(tbl)
    except Exception:
        return cleaned.replace("`", "").strip()


def scan_query_parameters(sql: str) -> List[str]:
    """Scan SQL text for parameters like {startDate} or :param using sqlglot tokenization.

    Returns a list of unique parameter names preserving appearance order.
    Comments are ignored automatically by the tokenizer.
    """
    tokens = sqlglot.tokenize(sql, dialect="bigquery")
    found: List[str] = []
    seen: Set[str] = set()

    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        # Unquoted {param} token sequence: '{', 'param', '}'
        if t.text == "{" and i + 2 < n and tokens[i + 2].text == "}":
            param = tokens[i + 1].text.strip()
            if param and param not in seen:
                seen.add(param)
                found.append(param)
            i += 3
            continue

        # Inside string literal '{param}' or colon parameter ':param'
        for m in _PARAM_RE.finditer(t.text):
            param = (m.group(1) or m.group(2) or "").strip()
            if param and param not in seen:
                seen.add(param)
                found.append(param)
        i += 1

    return found


def extract_csv_filename_from_comment(line: str) -> Optional[str]:
    """Extract CSV filename from a SQL comment (full-line or inline).

    Supports #, --, and /* ... */ comments.
    Matches explicit 'output: filename.csv' (or 'ouput:') as well as comments
    naming a .csv file directly.
    """
    clean_line = line.strip()

    # 1. Search for a comment containing a .csv filename
    m = re.search(r"(?:#|--|/\*|\*)\s*(?:out?put\s*:\s*)?([^\s;\*'\"]+\.csv)", clean_line, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("'\"*").rstrip("*/").strip()

    # 2. Search for explicit output: / ouput: comment without .csv extension
    m2 = re.search(r"(?:#|--|/\*)\s*out?put\s*:\s*([^\s;\*]+)", clean_line, re.IGNORECASE)
    if m2:
        fname = m2.group(1).strip().strip("'\"*").rstrip("*/").strip()
        if not fname.lower().endswith(".csv"):
            fname = f"{fname}.csv"
        return fname

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
    """Split SQL into individual statements with start/end character offsets using sqlglot tokenizer.

    Maintains block depth so semicolons inside procedural blocks (IF...END IF, WHILE...END WHILE,
    BEGIN...END) do not break statements into invalid fragments.
    """
    tokens = sqlglot.tokenize(sql, dialect="bigquery")
    statements: List[Tuple[int, int, str]] = []
    start = 0
    block_depth = 0

    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        txt = t.text.upper()
        next_tok = tokens[i + 1].text.upper() if i + 1 < n else ""
        prev_tok = tokens[i - 1].text.upper() if i > 0 else ""

        # Track start of procedural blocks (excluding DDL 'IF [NOT] EXISTS' and 'BEGIN TRANSACTION')
        if (txt == "IF" and next_tok not in ("NOT", "EXISTS") and prev_tok != "END") or \
           (txt in ("WHILE", "LOOP") and prev_tok != "END") or \
           (txt == "BEGIN" and next_tok != "TRANSACTION"):
            block_depth += 1
        elif txt == "END":
            block_depth = max(0, block_depth - 1)
        elif t.token_type == TokenType.SEMICOLON and block_depth == 0:
            end_idx = t.end + 1
            rest = sql[end_idx:]
            nl = rest.find("\n")
            same_line = rest[:nl] if nl != -1 else rest
            if re.match(r"^[ \t]*(?:--|#|/\*)", same_line):
                end_idx += len(same_line)
            chunk = sql[start:end_idx].strip()
            if chunk:
                statements.append((start, end_idx, sql[start:end_idx]))
            start = end_idx
        i += 1

    if start < len(sql):
        chunk = sql[start:].strip()
        if chunk:
            statements.append((start, len(sql), sql[start:]))

    return statements


def find_standalone_select_statements(sql: str) -> List[dict]:
    """Find all standalone SELECT statements in a SQL script using sqlglot AST expression matching.

    Classifies statement as standalone SELECT if the parsed root AST node is an exp.Select or exp.Union,
    and is not a CREATE TABLE ... AS SELECT or INSERT INTO ... SELECT.
    """
    raw_statements = split_sql_statements(sql)
    results: List[dict] = []
    select_idx = 0

    for start_char, end_char, raw_stmt in raw_statements:
        parseable_stmt = _to_parseable_sql(raw_stmt)
        try:
            parsed_stmts = [s for s in sqlglot.parse(parseable_stmt, dialect="bigquery") if s is not None and not isinstance(s, exp.Semicolon)]
            parsed = parsed_stmts[0] if parsed_stmts else None
        except Exception:
            parsed = None

        if not parsed or not isinstance(parsed, (exp.Select, exp.Union)):
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
    """Scan SQL text for output tables referenced in SELECT statements that export to CSV using sqlglot AST."""
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
            parseable = _to_parseable_sql(raw)
            try:
                parsed_stmts = [s for s in sqlglot.parse(parseable, dialect="bigquery") if s is not None and not isinstance(s, exp.Semicolon)]
                parsed = parsed_stmts[0] if parsed_stmts else None
                if parsed:
                    ctes = {c.alias_or_name.lower() for c in getattr(parsed, "ctes", [])}
                    from_clause = parsed.find(exp.From)
                    tbl = from_clause.find(exp.Table) if from_clause else parsed.find(exp.Table)
                    if tbl and tbl.name.lower() in ctes:
                        for candidate in parsed.find_all(exp.Table):
                            if candidate.name.lower() not in ctes:
                                tbl = candidate
                                break
                    name = _clean_table_address(tbl) if tbl else "output"
                else:
                    name = "output"
            except Exception:
                name = "output"

            if name not in seen:
                seen.add(name)
                csv_tables.append(name)

    return csv_tables


def extract_project_id_from_sql(sql: str) -> Optional[str]:
    """Extract BigQuery project_id based on the first 3-part table address (project.dataset.table) using sqlglot."""
    parseable = _to_parseable_sql(sql)
    try:
        for expr in sqlglot.parse(parseable, dialect="bigquery"):
            if not expr:
                continue
            for tbl in expr.find_all(exp.Table):
                if tbl.catalog:
                    return tbl.catalog
    except Exception:
        pass
    return None


def scan_query_tables(sql: str) -> Tuple[List[str], List[str], List[str]]:
    """Determine input, output, and CSV output tables from BigQuery SQL using sqlglot AST.

    Input tables: tables referenced in FROM and JOIN clauses (excluding tables created by a CTE).
    Output tables: targets of CREATE TABLE, INSERT INTO, and MERGE statements.
    Output CSV tables: standalone SELECT statements exporting to CSV.

    Returns:
        Tuple of (input_tables, output_tables, output_csv_tables)
    """
    parseable = _to_parseable_sql(sql)
    try:
        statements = [s for s in sqlglot.parse(parseable, dialect="bigquery") if s is not None]
    except Exception:
        statements = []

    cte_names: Set[str] = set()
    output_tables: List[str] = []
    seen_outputs: Set[str] = set()

    # 1. Discover CTEs and Output Target Tables from AST
    for stmt in statements:
        for cte in getattr(stmt, "ctes", []):
            cte_names.add(cte.alias_or_name.lower())

        target_tbl = None
        if isinstance(stmt, (exp.Create, exp.Insert, exp.Merge, exp.Update, exp.Delete, exp.Alter)):
            if hasattr(stmt, "this") and stmt.this is not None:
                target_tbl = stmt.this.find(exp.Table) or (stmt.this if isinstance(stmt.this, exp.Table) else None)

        if target_tbl:
            name = _clean_table_address(target_tbl)
            if name and name.upper() not in {"SELECT", "UNNEST"}:
                if name not in seen_outputs:
                    seen_outputs.add(name)
                    output_tables.append(name)

    # 2. Discover Input Tables from AST (excluding CTEs, target tables, and DROP statements)
    input_tables: List[str] = []
    seen_inputs: Set[str] = set()

    for stmt in statements:
        if isinstance(stmt, exp.Drop):
            continue

        stmt_target_tbl = None
        if isinstance(stmt, (exp.Create, exp.Insert, exp.Merge, exp.Update, exp.Delete, exp.Alter)):
            if hasattr(stmt, "this") and stmt.this is not None:
                stmt_target_tbl = stmt.this.find(exp.Table) or (stmt.this if isinstance(stmt.this, exp.Table) else None)

        for tbl in stmt.find_all(exp.Table):
            if stmt_target_tbl and tbl is stmt_target_tbl:
                continue
            name = _clean_table_address(tbl)
            if not name or name.upper() in {"SELECT", "UNNEST", "LATERAL"}:
                continue
            if tbl.name.lower() in cte_names:
                continue
            if name not in seen_inputs and name not in seen_outputs:
                seen_inputs.add(name)
                input_tables.append(name)

    # 3. CSV output tables from standalone SELECT statements
    output_csv_tables = scan_select_output_tables(sql)

    return input_tables, output_tables, output_csv_tables

"""SQL parsing utilities for BigQuery SQL files.

Extracts:
1. Query Parameters wrapped in curly brackets: {parameter-name}
2. Input tables from FROM and JOIN statements
3. Output tables from CREATE TABLE and INSERT INTO statements
"""

from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple


# Regex for removing single line and multi-line comments
_COMMENT_RE = re.compile(
    r"(--[^\n]*)|(/\*.*?\*/)",
    re.DOTALL | re.MULTILINE
)

# Regex for {parameter_name} - anything inside curly brackets (ignoring double curly brackets if any)
_PARAM_RE = re.compile(r"\{([^{}]+)\}")

# Table name token pattern (allows backticks, dots, hyphens, alphanumeric, underscores)
# e.g., `my-project.my_dataset.my_table` or dataset.table or table
_TABLE_TOKEN = r"(?:`[a-zA-Z0-9_\-\.]+`|[a-zA-Z0-9_\-\.]+)"

# Regex for FROM and JOIN clauses
_INPUT_TABLE_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+({_TABLE_TOKEN})",
    re.IGNORECASE
)

# Regex for CREATE TABLE and INSERT INTO statements
_OUTPUT_TABLE_RE = re.compile(
    rf"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+|TEMPORARY\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT(?:\s+INTO)?|MERGE(?:\s+INTO)?)\s+({_TABLE_TOKEN})",
    re.IGNORECASE
)

# Regex for Common Table Expressions (WITH cte AS (...) or , cte AS (...))
_CTE_RE = re.compile(
    r"(?:\bWITH|,)\s*([a-zA-Z0-9_\-\.]+)\s+AS\s*\(",
    re.IGNORECASE
)


def strip_comments(sql: str) -> str:
    """Remove SQL comments to avoid false positives."""
    return _COMMENT_RE.sub("", sql)


def clean_table_name(table_ref: str) -> str:
    """Clean backticks and whitespace from table reference."""
    clean = table_ref.strip()
    if clean.startswith("`") and clean.endswith("`"):
        clean = clean[1:-1].strip()
    return clean


def scan_query_parameters(sql: str) -> List[str]:
    """Scan SQL text for parameters in curly brackets like {startDate} or {repDate}.

    Returns a list of unique parameter names preserving appearance order.
    """
    cleaned_sql = strip_comments(sql)
    found: List[str] = []
    seen: Set[str] = set()

    for match in _PARAM_RE.finditer(cleaned_sql):
        param = match.group(1).strip()
        if param and param not in seen:
            seen.add(param)
            found.append(param)

    return found


_CSV_COMMENT_RE = re.compile(
    r"^\s*(?:#|--)\s*out?put\s*:\s*([^\s;]+)",
    re.IGNORECASE
)


def extract_csv_filename_from_comment(line: str) -> Optional[str]:
    """Extract filename from # ouput: filename.csv or # output: filename.csv line.

    Handles with/without spaces, case-insensitive, with/without quotes.
    Automatically appends .csv if omitted.
    """
    m = _CSV_COMMENT_RE.match(line)
    if not m:
        return None
    fname = m.group(1).strip().strip("'\"")
    if not fname.lower().endswith(".csv"):
        fname = f"{fname}.csv"
    return fname


def get_next_available_table_csv(existing_names: Iterable[str]) -> str:
    r"""Determine the next available table_NN.csv filename.

    Finds existing numbers from names matching table_(\d+) and increments from the maximum.
    If table_05.csv exists, returns table_06.csv. Defaults to table_01.csv.
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
    """Split SQL into individual statements with start/end character offsets."""
    statements: List[Tuple[int, int, str]] = []
    start = 0
    i = 0
    n = len(sql)

    in_single_quote = False
    in_double_quote = False
    in_triple_single = False
    in_triple_double = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        if not in_line_comment and not in_block_comment:
            if not in_single_quote and not in_double_quote:
                if sql[i:i+3] == "'''":
                    in_triple_single = not in_triple_single
                    i += 3
                    continue
                elif sql[i:i+3] == '"""':
                    in_triple_double = not in_triple_double
                    i += 3
                    continue

            if not in_triple_single and not in_triple_double:
                if sql[i] == "'" and not in_double_quote:
                    if in_single_quote and i + 1 < n and sql[i+1] == "'":
                        i += 2
                        continue
                    in_single_quote = not in_single_quote
                    i += 1
                    continue
                elif sql[i] == '"' and not in_single_quote:
                    if in_double_quote and i + 1 < n and sql[i+1] == '"':
                        i += 2
                        continue
                    in_double_quote = not in_double_quote
                    i += 1
                    continue

        if not in_single_quote and not in_double_quote and not in_triple_single and not in_triple_double:
            if not in_line_comment and not in_block_comment:
                if sql[i:i+2] == "--" or sql[i] == "#":
                    in_line_comment = True
                    i += 1
                    continue
                elif sql[i:i+2] == "/*":
                    in_block_comment = True
                    i += 2
                    continue
                elif sql[i] == ";":
                    stmt = sql[start:i].strip()
                    if stmt:
                        statements.append((start, i, sql[start:i]))
                    start = i + 1
                    i += 1
                    continue
            elif in_line_comment:
                if sql[i] == "\n":
                    in_line_comment = False
            elif in_block_comment:
                if sql[i:i+2] == "*/":
                    in_block_comment = False
                    i += 2
                    continue
        i += 1

    remaining = sql[start:].strip()
    if remaining:
        statements.append((start, n, sql[start:]))

    return statements


def find_standalone_select_statements(sql: str) -> List[dict]:
    """Find all standalone SELECT statements in a SQL script."""
    statements = split_sql_statements(sql)
    lines = sql.splitlines(keepends=True)
    results = []

    for idx, (start_char, end_char, raw_stmt) in enumerate(statements):
        clean = strip_comments(raw_stmt)
        clean = re.sub(r"#[^\n]*", "", clean).strip()
        if not clean:
            continue

        if _OUTPUT_TABLE_RE.search(clean):
            continue
        if re.search(r"\b(?:UPDATE|DELETE\s+FROM)\b", clean, re.IGNORECASE):
            continue
        if not re.search(r"\bSELECT\b", clean, re.IGNORECASE):
            continue

        # Found standalone select statement
        m_token = re.search(r"\b(?:WITH|SELECT)\b", raw_stmt, re.IGNORECASE)
        code_char_offset = start_char + (m_token.start() if m_token else 0)
        code_line_idx = sql[:code_char_offset].count("\n")

        comment_line_idx = None
        csv_filename = None

        # 1. Check lines immediately preceding code_line_idx (skipping blanks and other comments up to 5 lines)
        cur = code_line_idx - 1
        while cur >= 0:
            line_str = lines[cur].strip()
            if not line_str:
                cur -= 1
                continue
            fname = extract_csv_filename_from_comment(line_str)
            if fname:
                comment_line_idx = cur
                csv_filename = fname
                break
            if line_str.startswith(("--", "/*", "*", "#")) and (code_line_idx - cur) <= 5:
                cur -= 1
                continue
            break

        # 2. If not found before code_line_idx, check within raw_stmt lines before token
        if csv_filename is None and m_token and m_token.start() > 0:
            prefix = raw_stmt[:m_token.start()]
            for line_in_prefix in prefix.splitlines():
                fname = extract_csv_filename_from_comment(line_in_prefix.strip())
                if fname:
                    csv_filename = fname
                    for l_idx, l_content in enumerate(lines):
                        if l_content.strip() == line_in_prefix.strip():
                            comment_line_idx = l_idx
                            break
                    break

        results.append({
            "statement_index": idx,
            "raw_statement": raw_stmt,
            "code_line_idx": code_line_idx,
            "comment_line_idx": comment_line_idx,
            "csv_filename": csv_filename,
        })

    return results


def sync_query_csv_comments(file_path: Path, existing_csv_names: Optional[Iterable[str]] = None) -> List[str]:
    """Inspect standalone SELECT statements in a .sql file.

    If preceding # output: (or -- output:) comment is missing, inserts # output: table_NN.csv above the select.
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
    insertions = []
    result_csvs = []

    for s in statements:
        if s["csv_filename"]:
            result_csvs.append(s["csv_filename"])
        else:
            next_csv = get_next_available_table_csv(known_csvs)
            known_csvs.add(next_csv)
            result_csvs.append(next_csv)
            insertions.append((s["code_line_idx"], f"# output: {next_csv}\n"))
            modified = True

    if modified:
        for line_idx, comment_str in sorted(insertions, key=lambda x: x[0], reverse=True):
            lines.insert(line_idx, comment_str)
        file_path.write_text("".join(lines), encoding="utf-8")

    return result_csvs


def update_query_csv_comment(file_path: Path, select_index: int, new_filename: str) -> None:
    """Update the # output: (or -- output:) comment for the specified standalone SELECT statement in a .sql file."""
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
        # If original comment used '--', preserve '--', otherwise use default '#'
        prefix = "--" if orig_line.lstrip().startswith("--") else "#"
        lines[stmt["comment_line_idx"]] = f"{prefix} output: {clean_name}\n"
    else:
        lines.insert(stmt["code_line_idx"], f"# output: {clean_name}\n")

    file_path.write_text("".join(lines), encoding="utf-8")


def scan_select_output_tables(sql: str) -> List[str]:
    """Scan SQL text for output tables referenced in SELECT statements that export to CSV.

    If # output: (or # ouput:, -- output:) comment exists directly before the select statement, uses the comment's filename.
    Otherwise falls back to extracting primary source table(s) from FROM clauses.
    """
    cleaned_sql = strip_comments(sql).strip()
    if not cleaned_sql:
        return []

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
            stmt = strip_comments(stmt_info["raw_statement"]).strip()
            found = False
            for match in _INPUT_TABLE_RE.finditer(stmt):
                raw_name = match.group(1)
                name = clean_table_name(raw_name)
                if name.upper() not in {"SELECT", "UNNEST", "LATERAL"} and name not in seen:
                    seen.add(name)
                    csv_tables.append(name)
                    found = True
                    break

            if not found:
                name = "output"
                if name not in seen:
                    seen.add(name)
                    csv_tables.append(name)

    return csv_tables


def extract_project_id_from_sql(sql: str) -> Optional[str]:
    """Extract BigQuery project_id based on the first 'from' statement table address.

    Per instructions: All tables have the name format 'project-id.dataset_id.table_id'.
    Returns the project-id string if found, else None.
    """
    cleaned_sql = strip_comments(sql)
    for match in _INPUT_TABLE_RE.finditer(cleaned_sql):
        raw_match = match.group(0)
        # Check that it's a FROM clause
        if re.match(r"\bFROM\b", raw_match, re.IGNORECASE):
            raw_name = match.group(1)
            name = clean_table_name(raw_name)
            if name.upper() not in {"SELECT", "UNNEST", "LATERAL"}:
                parts = name.split(".")
                if len(parts) >= 3:
                    return parts[0]
                elif len(parts) == 2:
                    return None

    # Fallback: check any FROM or JOIN table address with 3 parts
    for match in _INPUT_TABLE_RE.finditer(cleaned_sql):
        name = clean_table_name(match.group(1))
        if name.upper() not in {"SELECT", "UNNEST", "LATERAL"}:
            parts = name.split(".")
            if len(parts) >= 3:
                return parts[0]

    # Fallback: check output table address
    for match in _OUTPUT_TABLE_RE.finditer(cleaned_sql):
        name = clean_table_name(match.group(1))
        if name.upper() not in {"SELECT", "UNNEST"}:
            parts = name.split(".")
            if len(parts) >= 3:
                return parts[0]

    return None


def scan_query_tables(sql: str) -> Tuple[List[str], List[str], List[str]]:
    """Determine input, output, and CSV output tables from BigQuery SQL.

    Input tables: extracted from FROM and JOIN clauses (excluding CTEs and created tables).
    Output tables: extracted from CREATE TABLE, INSERT INTO, and MERGE statements.
    Output CSV tables: extracted from standalone SELECT statements exporting to CSV.

    Returns:
        Tuple of (input_tables, output_tables, output_csv_tables)
    """
    cleaned_sql = strip_comments(sql)

    # Detect Common Table Expressions (CTEs) so they aren't marked as external input tables
    cte_names: Set[str] = set()
    for match in _CTE_RE.finditer(cleaned_sql):
        cte_name = clean_table_name(match.group(1))
        cte_names.add(cte_name)

    input_tables: List[str] = []
    seen_inputs: Set[str] = set()

    output_tables: List[str] = []
    seen_outputs: Set[str] = set()

    # Extract output tables first
    for match in _OUTPUT_TABLE_RE.finditer(cleaned_sql):
        raw_name = match.group(1)
        name = clean_table_name(raw_name)
        # Skip subqueries / keywords
        if name.upper() in {"SELECT", "UNNEST"}:
            continue
        if name and name not in seen_outputs:
            seen_outputs.add(name)
            output_tables.append(name)

    # Extract input tables
    for match in _INPUT_TABLE_RE.finditer(cleaned_sql):
        raw_name = match.group(1)
        name = clean_table_name(raw_name)
        # Skip subqueries / special BigQuery keywords / CTEs
        if name.upper() in {"SELECT", "UNNEST", "LATERAL"}:
            continue
        if name in cte_names:
            continue
        if name and name not in seen_inputs and name not in seen_outputs:
            seen_inputs.add(name)
            input_tables.append(name)

    output_csv_tables = scan_select_output_tables(cleaned_sql)

    return input_tables, output_tables, output_csv_tables

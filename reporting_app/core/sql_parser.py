"""SQL parsing utilities for BigQuery SQL files.

Extracts:
1. Query Parameters wrapped in curly brackets: {parameter-name}
2. Input tables from FROM and JOIN statements
3. Output tables from CREATE TABLE and INSERT INTO statements
"""

import re
from typing import List, Set, Tuple


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
    rf"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+|TEMPORARY\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+INTO)\s+({_TABLE_TOKEN})",
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


def scan_select_output_tables(sql: str) -> List[str]:
    """Scan SQL text for output tables referenced in SELECT statements that export to CSV.

    If the query has one or more SELECT statements not saving into a table
    via CREATE TABLE or INSERT INTO, extracts the primary source table(s)
    from their FROM clauses. If multiple SELECT statements exist, extracts
    the table for each SELECT statement.
    """
    cleaned_sql = strip_comments(sql).strip()
    if not cleaned_sql:
        return []

    # Split into individual SQL statements by semicolon
    statements = [s.strip() for s in cleaned_sql.split(";") if s.strip()]
    csv_tables: List[str] = []
    seen: Set[str] = set()

    for stmt in statements:
        # If this statement creates or modifies a table, it is not a CSV output
        if _OUTPUT_TABLE_RE.search(stmt):
            continue

        # If it contains a SELECT statement
        if re.search(r"\bSELECT\b", stmt, re.IGNORECASE):
            found = False
            for match in _INPUT_TABLE_RE.finditer(stmt):
                raw_name = match.group(1)
                name = clean_table_name(raw_name)
                if name.upper() not in {"SELECT", "UNNEST", "LATERAL"} and name not in seen:
                    seen.add(name)
                    csv_tables.append(name)
                    found = True
                    break  # Take primary FROM table for this SELECT statement

            if not found:
                # If no FROM table found (e.g. SELECT 1;), add a placeholder
                name = "output"
                if name not in seen:
                    seen.add(name)
                    csv_tables.append(name)

    return csv_tables


def scan_query_tables(sql: str) -> Tuple[List[str], List[str], List[str]]:
    """Determine input, output, and CSV output tables from BigQuery SQL.

    Input tables: extracted from FROM and JOIN clauses.
    Output tables: extracted from CREATE TABLE and INSERT INTO statements.
    Output CSV tables: extracted from standalone SELECT statements exporting to CSV.

    Returns:
        Tuple of (input_tables, output_tables, output_csv_tables)
    """
    cleaned_sql = strip_comments(sql)

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
        # Skip subqueries / special BigQuery keywords
        if name.upper() in {"SELECT", "UNNEST", "LATERAL"}:
            continue
        if name and name not in seen_inputs and name not in seen_outputs:
            seen_inputs.add(name)
            input_tables.append(name)

    output_csv_tables = scan_select_output_tables(cleaned_sql)

    return input_tables, output_tables, output_csv_tables

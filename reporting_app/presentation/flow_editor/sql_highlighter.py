"""BigQuery SQL syntax highlighter and debounced sqlglot syntax validator."""

from dataclasses import dataclass
import re
from typing import List, Set, Tuple

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp, TokenType

from reporting_app.core.sql_parser import _PARAM_RE, split_sql_statements


@dataclass
class SqlSyntaxErrorInfo:
    """Structured syntax error details reported by sqlglot."""

    message: str
    line: int = 1
    col: int = 1


_LINE_COL_RE = re.compile(r"Line\s+(\d+)\s*,\s*Col:\s*(\d+)", re.IGNORECASE)

_VALID_TOP_LEVEL_TYPES = tuple(
    t for t in (
        getattr(exp, "Query", None),
        getattr(exp, "Select", None),
        getattr(exp, "SetOperation", None),
        getattr(exp, "Create", None),
        getattr(exp, "Insert", None),
        getattr(exp, "Update", None),
        getattr(exp, "Delete", None),
        getattr(exp, "Merge", None),
        getattr(exp, "Drop", None),
        getattr(exp, "Alter", None),
        getattr(exp, "TruncateTable", None),
        getattr(exp, "Declare", None),
        getattr(exp, "Set", None),
        getattr(exp, "Transaction", None),
        getattr(exp, "Commit", None),
        getattr(exp, "Rollback", None),
        getattr(exp, "Call", None),
        getattr(exp, "Use", None),
        getattr(exp, "Analyze", None),
        getattr(exp, "Export", None),
        getattr(exp, "LoadData", None),
        getattr(exp, "Grant", None),
        getattr(exp, "Revoke", None),
    )
    if t is not None
)

_ALLOWED_COMMAND_PREFIXES = (
    "EXECUTE IMMEDIATE",
    "ASSERT",
    "EXPORT DATA",
    "RAISE",
    "RETURN",
    "CALL",
    "LOOP",
    "END",
    "LEAVE",
    "ITERATE",
    "BREAK",
    "CONTINUE",
    "FOR",
    "REPEAT",
)

_MISSPELLED_CLAUSE_KEYWORDS: Set[str] = {
    "WHER", "WERE", "WHRE", "FORM", "FOM", "GRUP", "GROP", "ORDR",
    "ODER", "LIMT", "LIMI", "HAVNG", "QUALFY", "UNON", "JION", "SELCT", "SELET",
}

_NEWLINE_ALIAS_EXCLUDED_PREV: Set[str] = {
    "AS", "FROM", "JOIN", "TABLE", "VIEW", "INTO", "UPDATE", "MERGE",
    "USING", "PROCEDURE", "FUNCTION", "DECLARE", "FOR", "IN", "CALL",
    "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "NATURAL",
    "SELECT", "WHERE", "ON", "AND", "OR", "NOT", "GROUP", "ORDER", "BY",
    "HAVING", "QUALIFY", "LIMIT", "OFFSET", "UNION", "EXCEPT", "INTERSECT",
    "CASE", "WHEN", "THEN", "ELSE", "SET", "VALUES", "OVER", "PARTITION",
    "CREATE", "REPLACE", "TEMP", "TEMPORARY", "DROP", "ALTER", "IF",
    "EXISTS", "BEGIN", "END", "WHILE", "LOOP", "DISTINCT", "ALL",
}


def _strip_leading_comments(raw: str) -> str:
    """Strip leading SQL comments and whitespace from a statement snippet."""
    idx = 0
    n = len(raw)
    while idx < n:
        while idx < n and raw[idx].isspace():
            idx += 1
        if idx + 1 < n and raw[idx:idx + 2] == "--":
            nl = raw.find("\n", idx)
            idx = n if nl == -1 else nl + 1
        elif idx < n and raw[idx] == "#":
            nl = raw.find("\n", idx)
            idx = n if nl == -1 else nl + 1
        elif idx + 1 < n and raw[idx:idx + 2] == "/*":
            end_c = raw.find("*/", idx + 2)
            idx = n if end_c == -1 else end_c + 2
        else:
            break
    return raw[idx:].strip()


def validate_bigquery_sql(sql: str) -> List[SqlSyntaxErrorInfo]:
    """Validate BigQuery SQL syntax using sqlglot plus strict AST and token rules."""
    if not sql or not sql.strip():
        return []

    parseable = _PARAM_RE.sub("__param__", sql)
    results: List[SqlSyntaxErrorInfo] = []

    raw_statements = split_sql_statements(parseable)
    if not raw_statements:
        raw_statements = [(0, len(parseable), parseable)]

    for start_char, _end_char, raw_stmt in raw_statements:
        code_body = _strip_leading_comments(raw_stmt)
        if not code_body:
            continue

        lead_ws = len(raw_stmt) - len(_strip_leading_comments(raw_stmt))
        # Calculate 1-based line number where the actual SQL code starts
        code_offset = raw_stmt.find(code_body)
        stmt_start_line = parseable[:start_char + max(0, code_offset)].count("\n") + 1

        try:
            parsed_stmts = [
                s for s in sqlglot.parse(code_body, read="bigquery", error_level=sqlglot.ErrorLevel.RAISE)
                if s is not None and not isinstance(s, exp.Semicolon)
            ]
        except (sqlglot_errors.ParseError, sqlglot_errors.TokenError) as exc:
            raw_errors = getattr(exc, "errors", None) or []
            added_any = False
            for err in raw_errors:
                if not isinstance(err, dict):
                    continue
                rel_line = max(1, int(err.get("line") or 1))
                col = max(1, int(err.get("col") or 1))
                abs_line = stmt_start_line + rel_line - 1
                desc = (err.get("description") or "Syntax error").strip()
                highlight = (err.get("highlight") or "").strip()
                msg = f"{desc} near '{highlight}'" if highlight else desc
                results.append(SqlSyntaxErrorInfo(message=msg, line=abs_line, col=col))
                added_any = True

            if not added_any:
                text_msg = str(exc).strip() or "Syntax error"
                m = _LINE_COL_RE.search(text_msg)
                rel_line = max(1, int(m.group(1))) if m else 1
                col = max(1, int(m.group(2))) if m else 1
                abs_line = stmt_start_line + rel_line - 1
                results.append(SqlSyntaxErrorInfo(message=text_msg, line=abs_line, col=col))
            continue
        except sqlglot_errors.SqlglotError as exc:
            results.append(SqlSyntaxErrorInfo(message=str(exc).strip() or "Syntax error", line=stmt_start_line, col=1))
            continue

        try:
            stmt_tokens = sqlglot.tokenize(code_body, read="bigquery")
        except Exception:
            stmt_tokens = []

        for stmt in parsed_stmts:
            if isinstance(stmt, exp.Command):
                upper_body = code_body.upper()
                if not any(upper_body.startswith(prefix) for prefix in _ALLOWED_COMMAND_PREFIXES):
                    results.append(
                        SqlSyntaxErrorInfo(
                            message=f"Invalid or unrecognized SQL statement near '{stmt.this}'",
                            line=stmt_start_line,
                            col=1,
                        )
                    )
                continue

            if not isinstance(stmt, _VALID_TOP_LEVEL_TYPES):
                preview = stmt.sql("bigquery")[:40]
                results.append(
                    SqlSyntaxErrorInfo(
                        message=f"Unexpected expression '{preview}' (not a valid SQL statement)",
                        line=stmt_start_line,
                        col=1,
                    )
                )
                continue

            # AST clause completeness checks
            for node in stmt.walk():
                if isinstance(node, exp.Select) and not node.expressions:
                    results.append(
                        SqlSyntaxErrorInfo(
                            message="SELECT statement is missing select expressions",
                            line=stmt_start_line,
                            col=1,
                        )
                    )
                elif isinstance(node, exp.Alias) and (
                    isinstance(node.this, exp.Star)
                    or (isinstance(node.this, exp.Column) and isinstance(node.this.this, exp.Star))
                ):
                    results.append(
                        SqlSyntaxErrorInfo(
                            message=f"Unexpected identifier '{node.alias}' after '*'",
                            line=stmt_start_line,
                            col=1,
                        )
                    )
                elif isinstance(node, exp.Group) and not node.expressions and not any(
                    node.args.get(k) for k in ("grouping_sets", "cube", "rollup")
                ):
                    results.append(
                        SqlSyntaxErrorInfo(
                            message="GROUP BY clause is missing grouping expressions",
                            line=stmt_start_line,
                            col=1,
                        )
                    )
                elif isinstance(node, exp.Order) and not node.expressions:
                    results.append(
                        SqlSyntaxErrorInfo(
                            message="ORDER BY clause is missing ordering expressions",
                            line=stmt_start_line,
                            col=1,
                        )
                    )
                elif isinstance(node, exp.Join):
                    kind = (node.kind or "").upper()
                    if (
                        kind != "CROSS"
                        and not isinstance(node.this, exp.Unnest)
                        and not node.args.get("on")
                        and not node.args.get("using")
                    ):
                        results.append(
                            SqlSyntaxErrorInfo(
                                message="JOIN clause is missing ON or USING condition",
                                line=stmt_start_line,
                                col=1,
                            )
                        )
                elif isinstance(node, (exp.TableAlias, exp.Alias)):
                    alias_name = ""
                    if isinstance(node, exp.TableAlias):
                        alias_name = (node.name or "").strip()
                    elif isinstance(node, exp.Alias):
                        alias_name = (node.alias or "").strip()
                    if alias_name and alias_name.upper() in _MISSPELLED_CLAUSE_KEYWORDS:
                        tok_line, tok_col = stmt_start_line, 1
                        for tok in stmt_tokens:
                            if tok.text.upper() == alias_name.upper():
                                tok_line = stmt_start_line + tok.line - 1
                                tok_col = max(1, tok.col)
                                break
                        results.append(
                            SqlSyntaxErrorInfo(
                                message=f"Unexpected token '{alias_name}' (possible misspelled SQL keyword)",
                                line=tok_line,
                                col=tok_col,
                            )
                        )

            # Token-level check for implicit aliases across newlines
            n_toks = len(stmt_tokens)
            for idx in range(1, n_toks):
                prev_tok = stmt_tokens[idx - 1]
                curr_tok = stmt_tokens[idx]
                if curr_tok.line <= prev_tok.line:
                    continue
                prev_ok = prev_tok.token_type in (
                    TokenType.IDENTIFIER,
                    TokenType.VAR,
                    TokenType.NUMBER,
                    TokenType.STRING,
                    TokenType.STAR,
                    TokenType.R_PAREN,
                ) and prev_tok.text.upper() not in _NEWLINE_ALIAS_EXCLUDED_PREV
                curr_ok = (
                    curr_tok.token_type in (TokenType.VAR, TokenType.IDENTIFIER)
                    and curr_tok.text.upper() not in _NEWLINE_ALIAS_EXCLUDED_PREV
                )
                if prev_ok and curr_ok:
                    results.append(
                        SqlSyntaxErrorInfo(
                            message=f"Unexpected identifier '{curr_tok.text}' on new line (missing comma or SQL keyword)",
                            line=stmt_start_line + curr_tok.line - 1,
                            col=max(1, curr_tok.col),
                        )
                    )

    return results


def _make_format(color_hex: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color_hex))
    if bold:
        fmt.setFontWeight(QFont.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


_SQL_KEYWORDS = [
    "ALL", "ALTER", "AND", "ANY", "ARRAY", "AS", "ASC", "AT", "BEGIN", "BETWEEN",
    "BREAK", "BY", "CALL", "CASE", "CAST", "CLUSTER", "COLLATE", "COMMIT",
    "CONTINUE", "CREATE", "CROSS", "CUBE", "CURRENT", "DECLARE", "DEFAULT",
    "DELETE", "DESC", "DISTINCT", "DO", "DROP", "ELSE", "ELSEIF", "END",
    "EXCEPT", "EXCEPTION", "EXECUTE", "EXISTS", "EXPORT", "EXTERNAL", "EXTRACT",
    "FALSE", "FETCH", "FIRST", "FOLLOWING", "FOR", "FROM", "FULL", "FUNCTION",
    "GROUP", "GROUPING", "GROUPS", "HAVING", "IF", "IGNORE", "ILIKE", "IMMEDIATE",
    "IN", "INNER", "INSERT", "INTERSECT", "INTERVAL", "INTO", "IS", "ITERATE",
    "JOIN", "LAST", "LEAVE", "LEFT", "LIKE", "LIMIT", "LOOP", "MATCHED",
    "MERGE", "NATURAL", "NEXT", "NOT", "NULL", "NULLS", "OFFSET", "ON", "OPEN",
    "OPTIONS", "OR", "ORDER", "OUTER", "OVER", "PARTITION", "PIVOT", "PRECEDING",
    "PROCEDURE", "QUALIFY", "RAISE", "RANGE", "RECURSIVE", "REPLACE", "RESPECT",
    "RETURN", "RIGHT", "ROLLBACK", "ROLLUP", "ROW", "ROWS", "SAFE_CAST",
    "SELECT", "SET", "SETS", "SOME", "SOURCE", "STRUCT", "SYSTEM", "TABLE",
    "TARGET", "TEMP", "TEMPORARY", "THEN", "TO", "TRANSACTION", "TRUE", "TRUNCATE",
    "UNBOUNDED", "UNION", "UNNEST", "UNPIVOT", "UPDATE", "USING", "VALUES", "VIEW",
    "WHEN", "WHERE", "WHILE", "WINDOW", "WITH",
]

_SQL_FUNCTIONS_AND_TYPES = [
    "ABS", "ANY_VALUE", "APPROX_COUNT_DISTINCT", "APPROX_QUANTILES", "ARRAY_AGG",
    "ARRAY_CONCAT", "ARRAY_LENGTH", "ARRAY_TO_STRING", "AVG", "BIGINT",
    "BIGNUMERIC", "BOOL", "BOOLEAN", "BYTES", "CEIL", "CEILING", "COALESCE",
    "CONCAT", "CONTAINS_SUBSTR", "COUNT", "COUNTIF", "CURRENT_DATE",
    "CURRENT_DATETIME", "CURRENT_TIME", "CURRENT_TIMESTAMP", "DATE", "DATE_ADD",
    "DATE_DIFF", "DATE_FROM_UNIX_DATE", "DATE_SUB", "DATE_TRUNC", "DATETIME",
    "DATETIME_ADD", "DATETIME_DIFF", "DATETIME_SUB", "DATETIME_TRUNC", "DECIMAL",
    "DENSE_RANK", "DIV", "ENDS_WITH", "FARM_FINGERPRINT", "FIRST_VALUE",
    "FLOAT64", "FLOOR", "FORMAT", "FORMAT_DATE", "FORMAT_DATETIME",
    "FORMAT_TIMESTAMP", "GENERATE_ARRAY", "GENERATE_DATE_ARRAY", "GEOGRAPHY",
    "GREATEST", "IFNULL", "INT", "INT64", "INTEGER", "IS_INF", "IS_NAN", "JSON",
    "JSON_EXTRACT", "JSON_EXTRACT_SCALAR", "JSON_QUERY", "JSON_VALUE", "LAG",
    "LAST_DAY", "LAST_VALUE", "LEAD", "LEAST", "LEFT", "LENGTH", "LOG", "LOG10",
    "LOWER", "LPAD", "LTRIM", "MAX", "MIN", "MOD", "NTILE", "NULLIF", "NUMERIC",
    "PARSE_DATE", "PARSE_DATETIME", "PARSE_TIMESTAMP", "PERCENT_RANK", "POW",
    "POWER", "RAND", "RANK", "REGEXP_CONTAINS", "REGEXP_EXTRACT",
    "REGEXP_EXTRACT_ALL", "REGEXP_REPLACE", "REPEAT", "REVERSE", "RIGHT",
    "ROUND", "ROW_NUMBER", "RPAD", "RTRIM", "SAFE_DIVIDE", "SAFE_OFFSET",
    "SAFE_ORDINAL", "SMALLINT", "SPLIT", "SQRT", "STARTS_WITH", "STDDEV",
    "STRING", "STRING_AGG", "STRPOS", "SUBSTR", "SUBSTRING", "SUM", "TIME",
    "TIMESTAMP", "TIMESTAMP_ADD", "TIMESTAMP_DIFF", "TIMESTAMP_MICROS",
    "TIMESTAMP_MILLIS", "TIMESTAMP_SECONDS", "TIMESTAMP_SUB", "TIMESTAMP_TRUNC",
    "TO_JSON_STRING", "TRIM", "UPPER", "VARIANCE", "ZEROIFNULL",
]


class SqlSyntaxHighlighter(QSyntaxHighlighter):
    """Dark-mode BigQuery SQL syntax highlighter for QPlainTextEdit documents."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._rules: List[Tuple[QRegularExpression, QTextCharFormat]] = []

        keyword_fmt = _make_format("#569cd6", bold=True)
        kw_pattern = r"\b(?:" + "|".join(_SQL_KEYWORDS) + r")\b"
        self._rules.append((
            QRegularExpression(kw_pattern, QRegularExpression.CaseInsensitiveOption),
            keyword_fmt,
        ))

        func_fmt = _make_format("#dcdcaa")
        fn_pattern = r"\b(?:" + "|".join(_SQL_FUNCTIONS_AND_TYPES) + r")\b"
        self._rules.append((
            QRegularExpression(fn_pattern, QRegularExpression.CaseInsensitiveOption),
            func_fmt,
        ))

        # Numeric literals
        number_fmt = _make_format("#b5cea8")
        self._rules.append((QRegularExpression(r"\b\d+(?:\.\d+)?\b"), number_fmt))

        # Backticked identifiers: `project.dataset.table`
        ident_fmt = _make_format("#4ec9b0")
        self._rules.append((QRegularExpression(r"`[^`]*`"), ident_fmt))

        # Quoted string literals
        string_fmt = _make_format("#ce9178")
        self._rules.append((QRegularExpression(r"'(?:[^'\\]|\\.)*'"), string_fmt))
        self._rules.append((QRegularExpression(r'"(?:[^"\\]|\\.)*"'), string_fmt))

        # Template parameters: {param} or :param (highlighting inside strings as well)
        param_fmt = _make_format("#c586c0", bold=True)
        self._rules.append((QRegularExpression(r"\{[^{}\n]+\}"), param_fmt))
        self._rules.append((QRegularExpression(r"(?<!:):[a-zA-Z_][a-zA-Z0-9_]*(?!:)"), param_fmt))

        # Single-line comments: -- ... and # ...
        self._comment_fmt = _make_format("#6a9955", italic=True)
        self._rules.append((QRegularExpression(r"--[^\n]*"), self._comment_fmt))
        self._rules.append((QRegularExpression(r"#[^\n]*"), self._comment_fmt))

        self._comment_start_re = QRegularExpression(r"/\*")
        self._comment_end_re = QRegularExpression(r"\*/")

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # Multi-line /* ... */ comment state tracking across blocks
        self.setCurrentBlockState(0)
        start_idx = 0
        if self.previousBlockState() != 1:
            start_match = self._comment_start_re.match(text)
            start_idx = start_match.capturedStart() if start_match.hasMatch() else -1

        while start_idx >= 0:
            end_match = self._comment_end_re.match(text, start_idx + (0 if self.previousBlockState() == 1 and start_idx == 0 else 2))
            if end_match.hasMatch():
                comment_len = end_match.capturedEnd() - start_idx
                self.setFormat(start_idx, comment_len, self._comment_fmt)
                next_match = self._comment_start_re.match(text, start_idx + comment_len)
                start_idx = next_match.capturedStart() if next_match.hasMatch() else -1
            else:
                self.setCurrentBlockState(1)
                self.setFormat(start_idx, len(text) - start_idx, self._comment_fmt)
                break

"""Date helper functions for dynamic date parameters in queries and process flows."""

from datetime import date, timedelta
from typing import Optional


DATE_OPTIONS = [
    "pick date",
    "prev week",
    "prev month",
    "prev quarter",
    "prev year",
    "prev half year",
]

IMPORT_DATE_OPTIONS = [
    "Flow Date",
    "None",
    "Prev Week • Mon",
    "Prev Week • Last Workday",
    "Prev Week • Sun",
    "Prev Month • 1st",
    "Prev Month • 1st Workday",
    "Prev Month • Last Workday",
    "Prev Month • End",
    "Prev Qtr • 1st",
    "Prev Qtr • 1st Workday",
    "Prev Qtr • Last Workday",
    "Prev Qtr • End",
]


def _first_workday(d: date) -> date:
    """Return d if it's Mon-Fri, otherwise the following Monday."""
    if d.weekday() == 5:  # Saturday
        return d + timedelta(days=2)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def _last_workday(d: date) -> date:
    """Return d if it's Mon-Fri, otherwise the preceding Friday."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d - timedelta(days=2)
    return d


def calculate_import_file_date(
    date_option: Optional[str],
    flow_date: Optional[str] = None,
    ref_date: Optional[date] = None,
) -> str:
    """Resolve the YYYY-MM-DD date string for an imported file based on its per-file date option.

    Relative options ('Prev Week • ...', 'Prev Month • ...', 'Prev Qtr • ...') are calculated
    relative to ref_date (defaults to today).
    """
    if date_option is not None and str(date_option).strip().lower() == "none":
        return ""

    opt = (date_option or "Flow Date").strip()
    if not opt or opt.lower() == "flow date":
        return (flow_date or "").strip()

    if ref_date is None:
        ref_date = date.today()

    import calendar

    # Previous Week options (ISO week: Monday = 0, Sunday = 6)
    prev_week_mon = ref_date - timedelta(days=ref_date.weekday() + 7)
    if opt == "Prev Week • Mon":
        return prev_week_mon.strftime("%Y-%m-%d")
    if opt == "Prev Week • Last Workday":
        return (prev_week_mon + timedelta(days=4)).strftime("%Y-%m-%d")
    if opt == "Prev Week • Sun":
        return (prev_week_mon + timedelta(days=6)).strftime("%Y-%m-%d")

    # Previous Month options
    prev_m_year = ref_date.year
    prev_m_month = ref_date.month - 1
    if prev_m_month == 0:
        prev_m_month = 12
        prev_m_year -= 1
    prev_m_last_day = calendar.monthrange(prev_m_year, prev_m_month)[1]
    prev_m_start = date(prev_m_year, prev_m_month, 1)
    prev_m_end = date(prev_m_year, prev_m_month, prev_m_last_day)

    if opt == "Prev Month • 1st":
        return prev_m_start.strftime("%Y-%m-%d")
    if opt == "Prev Month • 1st Workday":
        return _first_workday(prev_m_start).strftime("%Y-%m-%d")
    if opt == "Prev Month • Last Workday":
        return _last_workday(prev_m_end).strftime("%Y-%m-%d")
    if opt == "Prev Month • End":
        return prev_m_end.strftime("%Y-%m-%d")

    # Previous Quarter options
    curr_q = (ref_date.month - 1) // 3 + 1
    if curr_q == 1:
        prev_q = 4
        prev_q_year = ref_date.year - 1
    else:
        prev_q = curr_q - 1
        prev_q_year = ref_date.year

    q_start_month = (prev_q - 1) * 3 + 1
    q_end_month = prev_q * 3
    q_end_last_day = calendar.monthrange(prev_q_year, q_end_month)[1]
    prev_q_start = date(prev_q_year, q_start_month, 1)
    prev_q_end = date(prev_q_year, q_end_month, q_end_last_day)

    if opt == "Prev Qtr • 1st":
        return prev_q_start.strftime("%Y-%m-%d")
    if opt == "Prev Qtr • 1st Workday":
        return _first_workday(prev_q_start).strftime("%Y-%m-%d")
    if opt == "Prev Qtr • Last Workday":
        return _last_workday(prev_q_end).strftime("%Y-%m-%d")
    if opt == "Prev Qtr • End":
        return prev_q_end.strftime("%Y-%m-%d")

    return (flow_date or "").strip()


def calculate_date_for_option(option: str, ref_date: Optional[date] = None) -> str:
    """Calculate date string in YYYY-MM-DD format based on selected date option."""
    if ref_date is None:
        ref_date = date.today()

    opt = option.lower().strip()

    if opt == "prev week":
        res = ref_date - timedelta(days=7)
    elif opt == "prev month":
        year = ref_date.year
        month = ref_date.month - 1
        if month == 0:
            month = 12
            year -= 1
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(ref_date.day, max_day)
        res = date(year, month, day)
    elif opt == "prev quarter":
        year = ref_date.year
        month = ref_date.month - 3
        while month <= 0:
            month += 12
            year -= 1
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(ref_date.day, max_day)
        res = date(year, month, day)
    elif opt == "prev half year":
        year = ref_date.year
        month = ref_date.month - 6
        while month <= 0:
            month += 12
            year -= 1
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(ref_date.day, max_day)
        res = date(year, month, day)
    elif opt == "prev year":
        try:
            res = ref_date.replace(year=ref_date.year - 1)
        except ValueError:
            res = date(ref_date.year - 1, 2, 28)
    else:
        res = ref_date

    return res.strftime("%Y-%m-%d")


def is_date_param(param_name: str) -> bool:
    """Check if a parameter name contains the word 'date' (case-insensitive)."""
    return "date" in param_name.lower()


def format_filename_with_date(filename: str, filename_date: Optional[str]) -> str:
    """Translate date patterns between % signs in a filename using filename_date (YYYY-MM-DD).

    Example patterns:
    %YYYYMMDD% -> 20260225
    %YYYY_Qn%  -> 2026_Q1
    %YYYYMM%   -> 202602
    %YYYY-MM-DD% -> 2026-02-25
    %YYYY%     -> 2026
    %MM%       -> 02
    %DD%       -> 25
    %Qn%       -> Q1
    """
    if not filename or "%" not in filename or not filename_date:
        return filename

    from datetime import datetime
    try:
        clean_date_str = str(filename_date).strip()
        # Handle YYYY-MM-DD or YYYYMMDD
        if len(clean_date_str) == 8 and clean_date_str.isdigit():
            dt = datetime.strptime(clean_date_str, "%Y%m%d").date()
        else:
            dt = datetime.strptime(clean_date_str[:10], "%Y-%m-%d").date()
    except Exception:
        return filename

    year_str = dt.strftime("%Y")
    yy_str = dt.strftime("%y")
    month_str = dt.strftime("%m")
    day_str = dt.strftime("%d")
    quarter = (dt.month - 1) // 3 + 1
    quarter_str = f"Q{quarter}"

    import re

    def replace_pattern(match):
        pat = match.group(1)
        res = pat
        # Replace sub-tokens inside the %...% block
        res = res.replace("YYYY", year_str)
        res = res.replace("YY", yy_str)
        res = res.replace("MM", month_str)
        res = res.replace("DD", day_str)
        res = res.replace("Qn", quarter_str)
        res = res.replace("QQ", quarter_str)
        res = res.replace("qn", quarter_str.lower())
        res = res.replace("qq", quarter_str.lower())
        return res

    # Matches anything between %...%
    return re.sub(r"%([^%]+)%", replace_pattern, filename)


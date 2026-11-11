"""Date helper functions for dynamic date parameters in queries and process flows."""

import calendar
from datetime import date, timedelta
from typing import Optional, List, Tuple


# Days of week names for week submenus
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_INDEX_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}

# Month items
MONTH_ITEMS = ["1st of Month", "1st Workday", "Last Workday", "Month End"]

# Quarter items
QUARTER_ITEMS = ["1st of Qtr", "1st Workday", "Last Workday", "Quarter End"]

# Backward-compatible constant aliases
IMPORT_DATE_OPTIONS = [
    "Flow Date",
    "None",
    "Current • Today",
    "Current • Week • Mon",
    "Current • Week • Sun",
    "Current • Month • 1st of Month",
    "Current • Month • 1st Workday",
    "Current • Month • Last Workday",
    "Current • Month • Month End",
    "Previous • Week • Mon",
    "Previous • Week • Sun",
    "Previous • Month • 1st of Month",
    "Previous • Month • 1st Workday",
    "Previous • Month • Last Workday",
    "Previous • Month • Month End",
    "Previous • Quarter • 1st of Qtr",
    "Previous • Quarter • 1st Workday",
    "Previous • Quarter • Last Workday",
    "Previous • Quarter • Quarter End",
]

DATE_OPTIONS = ["Pick Date", "Current • Month • Month End", "Previous • Month • Month End"]


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


def evaluate_date_preset(
    preset: Optional[str],
    ref_date: Optional[date] = None,
    flow_date: Optional[str] = None,
) -> str:
    """Resolve a YYYY-MM-DD date string based on the hierarchical date preset.

    Supported hierarchies:
    - "Pick Date" -> returns today's date (or ref_date)
    - "Flow Date" -> returns flow_date
    - "None" -> returns ""
    - "Current • Today" -> returns today's date (or ref_date)
    - "Current • Week • <Mon..Sun>"
    - "Previous • Week • <Mon..Sun>"
    - "Current • Month • <1st of Month | 1st Workday | Last Workday | Month End>"
    - "Previous • Month • <1st of Month | 1st Workday | Last Workday | Month End>"
    - "Current • Quarter • <1st of Qtr | 1st Workday | Last Workday | Quarter End>"
    - "Previous • Quarter • <1st of Qtr | 1st Workday | Last Workday | Quarter End>"
    """
    if not preset:
        return ""

    raw_opt = preset.strip()
    opt_lower = raw_opt.lower()

    if opt_lower == "none":
        return ""

    if opt_lower == "flow date":
        return (flow_date or "").strip()

    if opt_lower in ("pick date", "pick_date", "today", "current • today", "current->today"):
        return (ref_date or date.today()).strftime("%Y-%m-%d")

    if ref_date is None:
        ref_date = date.today()

    # Split hierarchical parts separated by "•", "->", or ":"
    clean = raw_opt.replace("->", "•").replace(":", "•")
    parts = [p.strip() for p in clean.split("•")]

    if len(parts) == 2 and parts[0].lower() == "current" and parts[1].lower() == "today":
        return (ref_date or date.today()).strftime("%Y-%m-%d")

    if len(parts) >= 3:
        period_type = parts[0].lower()  # "current" or "previous" / "prev"
        cadence = parts[1].lower()      # "week", "month", or "quarter" / "qtr"
        item = parts[2].lower()         # specific cadence sub-item

        is_prev = period_type in ("previous", "prev")

        # --- WEEK CADENCE ---
        if cadence == "week":
            # Monday of current week (ISO Monday is 0)
            curr_week_mon = ref_date - timedelta(days=ref_date.weekday())
            base_mon = curr_week_mon - timedelta(days=7) if is_prev else curr_week_mon

            # Check if item matches a day name
            target_idx = None
            for d_name, idx in DAY_INDEX_MAP.items():
                if item == d_name or item.startswith(d_name):
                    target_idx = idx
                    break

            if target_idx is not None:
                return (base_mon + timedelta(days=target_idx)).strftime("%Y-%m-%d")
            if "last workday" in item:
                return (base_mon + timedelta(days=4)).strftime("%Y-%m-%d")

        # --- MONTH CADENCE ---
        elif cadence == "month":
            if is_prev:
                y = ref_date.year
                m = ref_date.month - 1
                if m == 0:
                    m = 12
                    y -= 1
            else:
                y = ref_date.year
                m = ref_date.month

            last_day = calendar.monthrange(y, m)[1]
            m_start = date(y, m, 1)
            m_end = date(y, m, last_day)

            if "1st workday" in item:
                return _first_workday(m_start).strftime("%Y-%m-%d")
            if "1st" in item:
                return m_start.strftime("%Y-%m-%d")
            if "last workday" in item:
                return _last_workday(m_end).strftime("%Y-%m-%d")
            if "end" in item or "last" in item:
                return m_end.strftime("%Y-%m-%d")

        # --- QUARTER CADENCE ---
        elif cadence in ("quarter", "qtr"):
            curr_q = (ref_date.month - 1) // 3 + 1
            if is_prev:
                if curr_q == 1:
                    target_q = 4
                    target_y = ref_date.year - 1
                else:
                    target_q = curr_q - 1
                    target_y = ref_date.year
            else:
                target_q = curr_q
                target_y = ref_date.year

            q_start_month = (target_q - 1) * 3 + 1
            q_end_month = target_q * 3
            q_last_day = calendar.monthrange(target_y, q_end_month)[1]
            q_start = date(target_y, q_start_month, 1)
            q_end = date(target_y, q_end_month, q_last_day)

            if "1st workday" in item:
                return _first_workday(q_start).strftime("%Y-%m-%d")
            if "1st" in item:
                return q_start.strftime("%Y-%m-%d")
            if "last workday" in item:
                return _last_workday(q_end).strftime("%Y-%m-%d")
            if "end" in item or "last" in item:
                return q_end.strftime("%Y-%m-%d")

    # If unrecognized, cleanly default to Pick Date (ref_date or today)
    return (ref_date or date.today()).strftime("%Y-%m-%d")


def calculate_import_file_date(
    date_option: Optional[str],
    flow_date: Optional[str] = None,
    ref_date: Optional[date] = None,
) -> str:
    """Wrapper for calculate_import_file_date using evaluate_date_preset."""
    return evaluate_date_preset(date_option, ref_date=ref_date, flow_date=flow_date)


def calculate_date_for_option(option: str, ref_date: Optional[date] = None) -> str:
    """Wrapper for calculate_date_for_option using evaluate_date_preset."""
    return evaluate_date_preset(option, ref_date=ref_date)


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


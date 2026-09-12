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
        res = res.replace("qn", quarter_str.lower())
        return res

    # Matches anything between %...%
    return re.sub(r"%([^%]+)%", replace_pattern, filename)


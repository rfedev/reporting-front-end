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

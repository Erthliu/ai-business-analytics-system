"""Deterministic natural-language period resolution."""
from datetime import date, timedelta
import calendar
import re
from contracts.analysis_request import DateRange


def resolve_time(expression: str, current_date: date) -> DateRange | None:
    """Resolve supported relative periods; never infer unavailable coverage."""
    value = expression.lower()
    if "last 30 days" in value:
        return DateRange(start=current_date - timedelta(days=29), end=current_date, source="DETERMINISTIC_RELATIVE")
    if "last month" in value or "previous month" in value:
        end = current_date.replace(day=1) - timedelta(days=1)
        return DateRange(start=end.replace(day=1), end=end, source="DETERMINISTIC_RELATIVE")
    if "this month" in value:
        return DateRange(start=current_date.replace(day=1), end=current_date, source="DETERMINISTIC_RELATIVE")
    if "this year" in value:
        return DateRange(start=date(current_date.year, 1, 1), end=current_date, source="DETERMINISTIC_RELATIVE")
    if "previous year" in value:
        return DateRange(start=date(current_date.year - 1, 1, 1), end=date(current_date.year - 1, 12, 31), source="DETERMINISTIC_RELATIVE")
    quarter = (current_date.month - 1) // 3
    if "this quarter" in value:
        return DateRange(start=date(current_date.year, quarter * 3 + 1, 1), end=current_date, source="DETERMINISTIC_RELATIVE")
    if "previous quarter" in value:
        year, q = (current_date.year - 1, 3) if quarter == 0 else (current_date.year, quarter - 1)
        start = date(year, q * 3 + 1, 1)
        end = date(year + (q == 3), 1 if q == 3 else q * 3 + 4, 1) - timedelta(days=1)
        return DateRange(start=start, end=end, source="DETERMINISTIC_RELATIVE")
    return None


def resolve_named_months(expression: str, current_date: date) -> list[DateRange]:
    """Resolve named months in their written order using most-recent occurrence."""
    names = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
    pattern = "|".join(names)
    matches = list(re.finditer(rf"\b({pattern})(?:\s+(\d{{4}}))?\b", expression.lower()))
    periods: list[DateRange] = []
    for match in matches[:2]:
        month = names[match.group(1)]
        year = int(match.group(2)) if match.group(2) else current_date.year
        if not match.group(2) and month > current_date.month:
            year -= 1
        end = calendar.monthrange(year, month)[1]
        periods.append(DateRange(start=date(year, month, 1), end=date(year, month, end), source="DETERMINISTIC_NAMED_MONTH"))
    return periods

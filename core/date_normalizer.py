import re
from datetime import datetime, timedelta
from typing import Optional, Union

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9, "sept": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

TEXT_MONTH_REGEX = re.compile(
    r"(?P<first>\d{1,2})[\s\-\/\.](?P<month>[A-Za-z]{3,9})[\s\-\/\.](?P<year>\d{2,4})|"
    r"(?P<month_alt>[A-Za-z]{3,9})[\s\-\/\.](?P<first_alt>\d{1,2})[\s\-\/\.,]+(?P<year_alt>\d{2,4})"
)

NUMERIC_DATE_REGEX = re.compile(
    r"^(?P<p1>\d{1,4})[\s\-\/\.](?P<p2>\d{1,2})[\s\-\/\.](?P<p3>\d{1,4})$"
)


def _expand_year(year_str: str) -> int:
    val = int(year_str)
    if len(year_str) == 2:
        return 2000 + val if val <= 69 else 1900 + val
    return val


def parse_date_intelligent(
    date_val: Optional[Union[str, int, float]], 
    output_format: str = "%d/%b/%Y",
    default_format: str = "DMY"
) -> str:
    """
    Intelligently identifies date formats and standardizes to 'DD/Mon/YYYY' (e.g. '24/Oct/2025').
    """
    if date_val is None or str(date_val).strip() in ("", "None", "null", "undefined"):
        return ""

    raw = str(date_val).strip()

    # 1. Pure Integer / Float Timestamps & Excel Serial Dates
    if re.match(r"^\d{5,13}$", raw):
        num = int(raw)
        if len(raw) == 13:
            return datetime.fromtimestamp(num / 1000.0).strftime(output_format)
        elif len(raw) == 10 and num > 1000000000:
            return datetime.fromtimestamp(num).strftime(output_format)
        elif len(raw) == 5 and 30000 <= num <= 65000:
            return (datetime(1899, 12, 30) + timedelta(days=num)).strftime(output_format)

    # Clean residual punctuation and timestamps
    cleaned = re.split(r"[\sT]\d{1,2}:\d{2}", raw)[0].strip()
    cleaned = re.sub(r"[,\'\"]", "", cleaned)

    # 2. Text Month Formats ('1-Aug-2026', '31 Mar 2026')
    text_match = TEXT_MONTH_REGEX.search(cleaned)
    if text_match:
        data = text_match.groupdict()
        if data.get("first") and data.get("month"):
            day_str = data["first"]
            month_token = data["month"].lower()
            year_str = data["year"]
        else:
            day_str = data["first_alt"]
            month_token = data["month_alt"].lower()
            year_str = data["year_alt"]

        if month_token in MONTH_MAP:
            day = int(day_str)
            month = MONTH_MAP[month_token]
            year = _expand_year(year_str)
            if 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day).strftime(output_format)

    # 3. Numeric Delimited Dates (DD-MM-YYYY, YYYY-MM-DD, DD/MM/YY)
    num_match = NUMERIC_DATE_REGEX.match(cleaned)
    if num_match:
        p1, p2, p3 = num_match.group("p1"), num_match.group("p2"), num_match.group("p3")

        # ISO Format (YYYY-MM-DD)
        if len(p1) == 4:
            year, month, day = int(p1), int(p2), int(p3)
            if 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day).strftime(output_format)

        # Day/Month/Year or Month/Day/Year
        v1, v2 = int(p1), int(p2)
        year = _expand_year(p3)

        if v1 > 12 and 1 <= v2 <= 12:
            day, month = v1, v2
        elif v2 > 12 and 1 <= v1 <= 12:
            day, month = v2, v1
        else:
            if default_format.upper() == "MDY":
                day, month = v2, v1
            else:
                day, month = v1, v2

        if 1 <= month <= 12 and 1 <= day <= 31:
            return datetime(year, month, day).strftime(output_format)

    # 4. Standard Datetime Fallbacks
    fallback_formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d %b %Y", "%d-%b-%Y", "%d %B %Y",
        "%b %d %Y", "%B %d %Y"
    ]

    for fmt in fallback_formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime(output_format)
        except ValueError:
            continue

    return raw
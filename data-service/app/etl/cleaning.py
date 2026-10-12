"""Value-level cleaning / standardisation functions.

Every parser returns either a clean value or raises CleanError with a short reason.
They are deliberately small and pure so they can be unit-tested in isolation.
"""
from __future__ import annotations

import re
from datetime import date, datetime

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nil", "-", "--", "nan", "?"}

EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")

DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y",
    "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%b %d, %Y",
)
DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M",
)

# Historic / colloquial city names seen in source systems -> canonical names in the cities table
CITY_SYNONYMS = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "bombay": "Mumbai", "gurgaon": "Gurugram",
    "calcutta": "Kolkata", "madras": "Chennai", "cochin": "Kochi", "poona": "Pune",
    "new delhi": "Delhi", "delhi ncr": "Delhi", "trivandrum": "Thiruvananthapuram",
}


class CleanError(ValueError):
    """A value that cannot be cleaned. `rule` is the machine-readable reason."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


def is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return str(value).strip().lower() in MISSING_TOKENS


def clean_text(value, max_len: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > max_len:
        raise CleanError("too_long", f"longer than {max_len} characters")
    return text


def clean_name(value) -> str:
    text = clean_text(value, 80)
    # "rAHUL  sharma" -> "Rahul Sharma"; keeps already mixed-case names like "McDonald" intact
    if text.islower() or text.isupper() or any(w[:1].islower() for w in text.split()):
        text = " ".join(w[:1].upper() + w[1:].lower() for w in text.split())
    return text


def clean_email(value) -> str:
    email = str(value).strip().lower()
    if not EMAIL_RE.match(email):
        raise CleanError("invalid_email", "not a valid e-mail address")
    return email


def clean_code(value, pattern: str | None = None) -> str:
    code = re.sub(r"\s+", "", str(value)).upper()
    if pattern and not re.fullmatch(pattern, code):
        raise CleanError("invalid_format", f"does not match expected format {pattern}")
    return code


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    text = text.split("T")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", text) else text
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CleanError("invalid_date", "unrecognised or impossible date")


def parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # date only -> midnight
    try:
        d = parse_date(text)
        return datetime(d.year, d.month, d.day)
    except CleanError:
        raise CleanError("invalid_datetime", "unrecognised or impossible date/time") from None


def parse_money(value) -> float:
    """'₹1,23,456.50', 'Rs. 4500', 'INR 999' -> float."""
    text = str(value).strip()
    text = re.sub(r"(?i)^(₹|rs\.?|inr)\s*", "", text)
    text = text.replace(",", "").replace(" ", "").replace("₹", "")
    try:
        number = float(text)
    except ValueError:
        raise CleanError("invalid_number", "not a number") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise CleanError("invalid_number", "not a finite number")
    return number


def parse_int(value) -> int:
    number = parse_money(value)
    if abs(number - round(number)) > 1e-9:
        raise CleanError("invalid_integer", "expected a whole number")
    return int(round(number))


def parse_fraction(value) -> float:
    """Discounts arrive as 0.1, 10, or '10%'. Values > 1 are treated as percentages."""
    text = str(value).strip()
    is_pct = text.endswith("%")
    number = parse_money(text.rstrip("%"))
    if is_pct or number > 1:
        number = number / 100.0
    return round(number, 4)


def parse_bool(value) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1", "active"}:
        return True
    if text in {"false", "f", "no", "n", "0", "inactive"}:
        return False
    raise CleanError("invalid_boolean", "expected yes/no")


def normalize_enum(value, allowed: dict[str, str]) -> str:
    """Case/whitespace-insensitive lookup in a synonym map {lower-case variant: canonical}."""
    key = re.sub(r"[\s_\-]+", " ", str(value).strip().lower())
    if key in allowed:
        return allowed[key]
    raise CleanError("invalid_value", f"'{value}' is not one of {sorted(set(allowed.values()))}")


def canonical_city(value, known_cities: set[str]) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    lowered = text.lower()
    if lowered in CITY_SYNONYMS and CITY_SYNONYMS[lowered] in known_cities:
        return CITY_SYNONYMS[lowered]
    for city in known_cities:
        if city.lower() == lowered:
            return city
    raise CleanError("unknown_city", f"'{text}' is not a city we operate in")


def normalize_header(name: str) -> str:
    text = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower()).strip("_")
    return text

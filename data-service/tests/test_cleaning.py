from datetime import date, datetime

import pytest

from app.etl import cleaning as C
from app.etl.specs import CHANNELS, SEGMENTS, detect_entity


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-05", date(2026, 10, 5)), ("05/10/2026", date(2026, 10, 5)), ("05-10-2026", date(2026, 10, 5)),
    ("05-Oct-2026", date(2026, 10, 5)), ("2026-10-05T10:00:00", date(2026, 10, 5)),
])
def test_parse_date_formats(raw, expected):
    assert C.parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/09/2026", "2026-13-01", "yesterday", "32-01-2026"])
def test_parse_date_rejects_impossible(raw):
    with pytest.raises(C.CleanError) as err:
        C.parse_date(raw)
    assert err.value.rule == "invalid_date"


def test_parse_datetime():
    assert C.parse_datetime("2026-10-05 14:30") == datetime(2026, 10, 5, 14, 30)
    assert C.parse_datetime("2026-10-05") == datetime(2026, 10, 5)


@pytest.mark.parametrize("raw,expected", [("₹1,23,456.50", 123456.5), ("Rs. 4500", 4500.0), ("INR 999", 999.0), (" 12 ", 12.0)])
def test_parse_money(raw, expected):
    assert C.parse_money(raw) == expected


@pytest.mark.parametrize("raw,expected", [("10%", 0.10), ("0.1", 0.10), ("15", 0.15), ("0", 0.0)])
def test_parse_fraction(raw, expected):
    assert C.parse_fraction(raw) == pytest.approx(expected)


def test_parse_int_rejects_fractions():
    assert C.parse_int("3") == 3
    with pytest.raises(C.CleanError):
        C.parse_int("2.5")


def test_enums_and_synonyms():
    assert C.normalize_enum(" small business ", SEGMENTS) == "SMB"
    assert C.normalize_enum("ENT", SEGMENTS) == "Enterprise"
    assert C.normalize_enum("web", CHANNELS) == "Online"
    with pytest.raises(C.CleanError):
        C.normalize_enum("Carrier Pigeon", CHANNELS)


def test_city_synonyms():
    known = {"Bengaluru", "Mumbai", "Delhi"}
    assert C.canonical_city("Bangalore", known) == "Bengaluru"
    assert C.canonical_city("  mumbai ", known) == "Mumbai"
    with pytest.raises(C.CleanError):
        C.canonical_city("Atlantis", known)


def test_names_and_emails():
    assert C.clean_name("  rAHUL   sharma ") == "Rahul Sharma"
    assert C.clean_email(" Rahul.S@Example.COM ") == "rahul.s@example.com"
    with pytest.raises(C.CleanError):
        C.clean_email("not-an-email")


def test_missing_tokens():
    for token in ["", " ", "NA", "n/a", "null", "-", None, float("nan")]:
        assert C.is_missing(token)
    assert not C.is_missing("0")


def test_detect_entity():
    assert detect_entity([], "october_order_items.csv") == "order_items"
    assert detect_entity([], "orders.csv") == "orders"
    assert detect_entity(["ticket_id", "priority", "email"], "export.csv") == "tickets"
    assert detect_entity(["sku", "qty", "order_id"], "lines.csv") == "order_items"
    assert detect_entity(["foo"], "x.csv") is None

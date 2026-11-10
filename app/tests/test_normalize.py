"""Normalisation: SSNs, dates, names, addresses. Inline examples only - no documents."""

from datetime import date

import pytest

from pcs_tool.engine.normalize import (
    Address, addresses_match, clean_person_name, find_ssns, format_date, names_match, normalize_street,
    parse_city_state_zip, parse_date, parse_ssn, safe_filename, ssn_compatible,
)


class TestSSN:
    @pytest.mark.parametrize("raw,last4,full", [
        ("123-45-6789", "6789", "123456789"),
        ("123 45 6789", "6789", "123456789"),
        ("123456789", "6789", "123456789"),
        ("XXX-XX-6789", "6789", None),
        ("***-**-6789", "6789", None),
        ("xxx-xx-6789", "6789", None),
    ])
    def test_formats(self, raw, last4, full):
        s = parse_ssn(raw)
        assert s is not None and s.last4 == last4 and s.full == full

    @pytest.mark.parametrize("raw", ["12-3456789",   # employer ID number (EIN), not an SSN
                                     "000-12-3456", "666-12-3456", "912-34-5678", "123-00-4567", "123-45-0000"])
    def test_rejects_invalid(self, raw):
        assert parse_ssn(raw) is None

    def test_ocr_letter_o_read_as_zero(self):
        assert parse_ssn("1O2-45-6789").full == "102456789"

    def test_find_in_text_skips_ein_and_phone(self):
        text = "EIN 27-0406438  Phone (713) 555-0100  Employee SSN XXX-XX-4079"
        found = find_ssns(text)
        assert [s.last4 for s in found] == ["4079"]

    def test_masked_compatible_with_full(self):
        assert ssn_compatible(parse_ssn("123-45-6789"), parse_ssn("XXX-XX-6789"))
        assert not ssn_compatible(parse_ssn("123-45-6789"), parse_ssn("223-45-6789"))

    def test_mask_property_never_shows_full_number(self):
        assert parse_ssn("123-45-6789").masked == "***-**-6789"


class TestDates:
    @pytest.mark.parametrize("raw,expected", [
        ("11/03/1992", date(1992, 11, 3)),
        ("9/5/92", date(1992, 9, 5)),
        ("5-5-26", date(2026, 5, 5)),
        ("01-09-26", date(2026, 1, 9)),
        ("Jan 5, 2026", date(2026, 1, 5)),
        ("January 5 2026", date(2026, 1, 5)),
        ("05 JAN 2026", date(2026, 1, 5)),
        ("Statement Date: 04/02/2026", date(2026, 4, 2)),
    ])
    def test_parse(self, raw, expected):
        assert parse_date(raw) == expected

    def test_invalid_date_skipped(self):
        assert parse_date("13/45/2020") is None

    @pytest.mark.parametrize("style,expected", [
        ("M-D-YY", "5-5-26"), ("MM-DD-YY", "05-05-26"), ("Month", "May"), ("Month YYYY", "May 2026"),
    ])
    def test_label_styles(self, style, expected):
        assert format_date(date(2026, 5, 5), style) == expected


class TestNames:
    @pytest.mark.parametrize("a,b", [
        ("Jane Q Doe", "JANE DOE"),                 # middle initial
        ("Jane Quinn Doe", "Jane Doe"),             # middle name
        ("DOE, JANE", "Jane Doe"),                  # last-name-first
        ("Jane Doe", "Jane Doee"),                  # small OCR error
        ("Mary Ann Smith-Jones", "Mary Smith-Jones"),
    ])
    def test_same_person(self, a, b):
        assert names_match(a, b)

    @pytest.mark.parametrize("a,b", [
        ("Jane Doe", "John Doe"),                   # different first name (spouse)
        ("Jane Doe", "Jane Smith"),                 # different last name
    ])
    def test_different_person(self, a, b):
        assert not names_match(a, b)

    def test_clean(self):
        assert clean_person_name("DOE, JANE Q") == "Jane Q Doe"
        assert clean_person_name("  ANNE-MARIE   o'neil ") == "Anne-Marie O'neil"


class TestAddresses:
    @pytest.mark.parametrize("a,b", [
        ("123 Main Street", "123 MAIN ST"),
        ("45 Oak Drive", "45 Oak Dr."),
        ("10 North Elm Road Apt 5", "10 N ELM RD UNIT 5"),
        ("10 Elm Rd #5", "10 Elm Road Apartment 5"),
        ("77 Lakeview Boulevard", "77 Lakeview Blvd"),
    ])
    def test_street_abbreviations_match(self, a, b):
        assert addresses_match(Address(a, zip5="77001"), Address(b, zip5="77001"))

    def test_different_house_number(self):
        assert not addresses_match(Address("123 Main St", zip5="77001"), Address("125 Main St", zip5="77001"))

    def test_different_zip(self):
        assert not addresses_match(Address("123 Main St", zip5="77001"), Address("123 Main St", zip5="77002"))

    def test_normalize_street(self):
        assert normalize_street("123 North Main Street Apt. 4") == "123 n main st unit 4"

    @pytest.mark.parametrize("line,expected", [
        ("HOUSTON TX 77002", ("Houston", "TX", "77002")),
        ("Houston, TX 77002-1234", ("Houston", "TX", "77002")),
        ("THE WOODLANDS, TX 77382", ("The Woodlands", "TX", "77382")),
        ("State ZIP code TX 77002", ("", "TX", "77002")),
    ])
    def test_city_state_zip(self, line, expected):
        assert parse_city_state_zip(line) == expected

    def test_not_a_state(self):
        assert parse_city_state_zip("BOX 12 ZZ 12345") is None


def test_safe_filename_removes_windows_forbidden_characters():
    assert safe_filename('Jane - INCOME - PAYSTUB - Example Clinic / Springfield - 10-28-25') == \
        "Jane - INCOME - PAYSTUB - Example Clinic - Springfield - 10-28-25"
    assert safe_filename('a:b*c?"d<e>f|g') == "a-b-c-d-e-f-g"

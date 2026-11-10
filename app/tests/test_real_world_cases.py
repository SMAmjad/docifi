"""Regression tests for problems found while auditing the real files (all values here are invented).

Each test names the real-world situation it protects against.
"""

from datetime import date

import pytest
from PIL import Image, ImageDraw

from pcs_tool.config import load_config
from pcs_tool.engine.cards import find_cards
from pcs_tool.engine.classify import classify_page
from pcs_tool.engine.extract import read_company, read_id_address, read_id_dob, read_id_name, read_ssn, strip_street
from pcs_tool.engine.normalize import Address, addresses_match, find_ssns, parse_city_state_zip, parse_ssn
from pcs_tool.engine.verify import verify
from pcs_tool.models import Document, Page, PersonRecord, Status


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# --------------------------------------------------------------------------- ID cards read from photos

def test_dob_with_missing_slash_is_not_confused_with_issue_date():
    # photo reader output with the second slash lost: "3.DoB:03/221988 4a.Iss: 05/11/2024"
    assert read_id_dob(["3.DoB:03/221988 4a.Iss: 05/11/2024"], {})[0] == date(1988, 3, 22)
    assert read_id_dob(["3.Doe04/12/1979 4a.Iss: 04/12/2023"], {})[0] == date(1979, 4, 12)


def test_dob_reader_rejects_impossible_birth_years():
    assert read_id_dob(["DOB 01/01/2024"], {})[0] is None


def test_licence_name_without_field_numbers():
    lines = ["Texas", "DRIVER LICENSE", "3.DOB:02/14/1980 4b.EXp: 02/14/2030", "4a.Iss: 01/05/2022",
             "DOE", "JANE MARIE", "8.123MAINST", "HOUSTON.TX77001"]
    assert read_id_name(lines, {})[0] == "Jane Marie Doe"


def test_licence_name_when_only_field_two_keeps_its_number():
    lines = ["3.DoB:03/22/1988", "SMITH", "2JOHN", "8.4500ELMRD"]
    assert read_id_name(lines, {})[0] == "John Smith"


def test_squashed_licence_address():
    addr, _ = read_id_address(["8.2600OAKST", "HOUSTONTXT7O02"], {})
    assert addr.street == "2600 OAKST" and addr.state == "TX" and addr.zip5 == "77002"
    assert addresses_match(addr, Address("2600 Oak Street", zip5="77002"))


def test_squashed_city_state_zip():
    assert parse_city_state_zip("HOUSTON.TX77001") == ("Houston", "TX", "77001")


def test_two_cards_on_one_sheet_are_found():
    sheet = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(sheet)
    d.rectangle([150, 120, 800, 530], fill=(205, 190, 175))    # card 1 (85.6 x 54 mm proportions)
    d.rectangle([800, 120, 1450, 530], fill=(185, 200, 215))   # card 2, photocopied edge to edge
    boxes = find_cards(sheet)
    assert len(boxes) == 2 and boxes[0][0] < boxes[1][0]


# --------------------------------------------------------------------------- SSN safety

def test_employer_id_without_dash_is_never_an_ssn():
    # OCR dropped the dash in the employer ID number "45-1234567"
    lines = ["a Employee's social security number", "451234567 4166 197"]
    assert read_ssn(lines, {"anchors": ["employee's social security number"], "lines_after": 2,
                            "search_all": True})[0] is None


def test_barcode_digits_are_never_an_ssn():
    assert find_ssns("0042-55512345-0000000020-00009990012345678901 123456789", strict=True) == []


def test_ssn_majority_wins_over_one_misread_copy():
    lines = ["a Employee's SSN 528-45-6789", "a Employee's SSN 523-45-6789", "a Employee's SSN 523-45-6789"]
    s, _ = read_ssn(lines, {"anchors": ["employee's ssn"], "lines_after": 1, "search_all": True})
    assert s.full == "523456789"


def _doc(doc_id, type_id, *persons, scanned=False, year=None):
    d = Document(doc_id, type_id, type_id, [Page("F001", 0, method="ocr" if scanned else "text layer")])
    d.persons = list(persons)
    if year:
        d.fields["year"] = year
    return d


def test_one_digit_ssn_difference_on_a_scan_is_check_manually_not_issue(cfg):
    docs = [_doc("D001", "personal_tax_return", PersonRecord("primary", "John Doe", parse_ssn("523-45-6789")), year=2025),
            _doc("D002", "w2", PersonRecord("employee", "John Q Doe", parse_ssn("528-45-6789"), None,
                                            Address("1 MAIN ST", zip5="77001")), scanned=True)]
    people = verify(docs, cfg)
    assert len(people) == 1                                   # still recognised as the same person
    ssn = {c.field: c for c in docs[1].checks}["SSN"]
    assert ssn.status == Status.CHECK and "one digit" in ssn.reason


# --------------------------------------------------------------------------- reference values

def test_id_card_beats_two_tax_returns_for_date_of_birth(cfg):
    docs = [_doc("D001", "personal_tax_return", PersonRecord("spouse", "Jane Doe", parse_ssn("123-45-6789"), date(1985, 4, 11)), year=2024),
            _doc("D002", "personal_tax_return", PersonRecord("spouse", "Jane Doe", parse_ssn("123-45-6789"), date(1985, 4, 11)), year=2025),
            _doc("D003", "drivers_license", PersonRecord("holder", "Jane S Doe", None, date(1985, 4, 12)))]
    people = verify(docs, cfg)
    assert people[0].reference["dob"] == date(1985, 4, 12)
    assert all({c.field: c for c in d.checks}["DOB"].status == Status.ISSUE for d in docs[:2])


# --------------------------------------------------------------------------- page recognition

def test_w2_back_page_mentioning_form_1040_stays_with_the_w2(cfg):
    text = ("Instructions for Employee\nBox 1. Enter this amount on the wages line of your tax return.\n"
            "See the Form 1040 instructions to determine if you are required to complete Form 8959.")
    c = classify_page(text, cfg)
    assert c.type_id is None and c.continuation_of == "w2"


def test_employer_name_with_amounts_on_the_same_line():
    lines = ["c Employer'sname,address,andZiPcode 3 Social securitywages", "EXAMPLE CARE LLC 17822 1105",
             "2O4O MAPLE FOREST DRIVE Medicare wages"]
    assert read_company(lines, {"anchors": ["employer's name, address, and zip code"], "lines_after": 3})[0] \
        == "EXAMPLE CARE LLC"
    assert strip_street("2O4O MAPLE FOREST DRIVE Medicare wages").startswith("2040 MAPLE FOREST DRIVE")

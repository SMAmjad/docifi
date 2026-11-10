"""Field reading, splitting and cross-document verification, using small inline examples."""

from datetime import date

import pytest

from pcs_tool.config import load_config
from pcs_tool.engine.assemble import assemble_documents
from pcs_tool.engine.extract import extract_tax_return, read_w2_employee, read_w2_employer, strip_street
from pcs_tool.engine.normalize import SSN, Address, parse_ssn
from pcs_tool.engine.verify import verify
from pcs_tool.models import Document, Page, PersonRecord, SourceFile, Status


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# --------------------------------------------------------------------------- reading

@pytest.mark.parametrize("raw,expected", [
    ("4821 MAPLE CT 3 FE EE", "4821 MAPLE CT"),
    ("1700 RIVER END DR APT 12 1D ingles", "1700 RIVER END DR APT 12"),
    ("300 COUNTY RD 47", "300 COUNTY RD 47"),
    ("P.O. Box 68", "P.O. BOX 68"),
])
def test_strip_street(raw, expected):
    assert strip_street(raw) == expected


W2_LINES = [
    "c Employer's name, address, and ZIP code",
    "EXAMPLE DENTAL OD PA",
    "100 COMMERCE ST",
    "AUSTIN TX 78701",
    "e Employee's name, address, and ZIP code",
    "JANE Q DOE",
    "45 OAK DR",
    "HOUSTON TX 77001",
]


def test_w2_employee_and_employer_are_not_confused():
    (name, _), (addr, _) = read_w2_employee(W2_LINES)
    employer, _ = read_w2_employer(W2_LINES)
    assert name == "Jane Q Doe"
    assert addr.street == "45 OAK DR" and addr.zip5 == "77001"
    assert employer == "EXAMPLE DENTAL OD PA"   # 'OD PA' marks a business even though it looks like a name


def _page(text, idx=0, fid="F001"):
    return Page(file_id=fid, index=idx, text=text, method="text layer")


def test_tax_return_reads_joint_names_cover_sheet_and_dob():
    cover = _page("TAX YEAR: 2025 PROCESS DATE: 04/14/2026\n"
                  "CLIENT : 123-45-6789 JANE DOE BIRTH DATE : 09/15/1990 Age:35\n"
                  "SPOUSE : 223-45-6780 JOHN DOE BIRTH DATE : 06/17/1989 Age:36\n"
                  "ADDRESS : 45 OAK DR PREPARER 1\n: HOUSTON TX 77001")
    sched = _page("JANE DOE & JOHN DOE 123-45-6789\nSchedule 1 (Form 1040) 2025 Page 2", idx=1)
    out = extract_tax_return([cover, sched])
    assert out["primary_name"][0] == "Jane Doe" and out["spouse_name"][0] == "John Doe"
    assert out["primary_ssn"][0].full == "123456789" and out["spouse_ssn"][0].last4 == "6780"
    assert out["primary_dob"][0] == date(1990, 9, 15) and out["spouse_dob"][0] == date(1989, 6, 17)
    assert out["home_address"][0].street == "45 OAK DR" and out["home_address"][0].zip5 == "77001"


def test_tax_return_shared_last_name_line():
    p = _page("Form 8962 (2024)\nJOHN K and JANE S DOE\n123-45-6789\nPage 2")
    out = extract_tax_return([p])
    assert out["primary_name"][0] == "John K Doe" and out["spouse_name"][0] == "Jane S Doe"


# --------------------------------------------------------------------------- splitting

def _cls(cfg, pages):
    from pcs_tool.engine.classify import classify_page
    for p in pages:
        c = classify_page(p.text, cfg)
        p.type_id, p.type_confidence = c.type_id, c.confidence
        if c.continuation_of:
            p.role, p.continuation_of = "continuation", c.continuation_of
    return pages


def test_shuffled_tax_return_pages_are_regrouped_and_reordered(cfg):
    files = {f: SourceFile(f, None, f, "Borrower A", ".pdf") for f in ("F001", "F002")}
    pages = _cls(cfg, [
        _page("Form 1040 (2025) Page 2\nTax and Credits", 0, "F001"),
        _page("Form W-2 Wage and Tax Statement 2025\nc Employer's name, address, and ZIP code\nOMB No. 1545-0008", 1, "F001"),
        _page("Instructions for Employee\nBox 1. Enter this amount", 2, "F001"),
        _page("1040 U.S. Individual Income Tax Return 2025 OMB No. 1545-0074\nYour first name and middle initial", 0, "F002"),
        _page("SCHEDULE 1 (Form 1040) 2024 Additional Income OMB No. 1545-0074", 1, "F002"),
    ])
    docs = assemble_documents(pages, files, cfg)
    tax = sorted((d for d in docs if d.type_id == "personal_tax_return"), key=lambda d: d.fields["group_year"])
    assert [d.fields["group_year"] for d in tax] == [2024, 2025]
    assert [p.ref for p in tax[1].pages] == ["F002-p1", "F001-p1"]   # page 1 first, even though scanned later
    w2 = [d for d in docs if d.type_id == "w2"]
    assert len(w2) == 1 and [p.ref for p in w2[0].pages] == ["F001-p2", "F001-p3"]  # back page attached


def test_unrecognised_page_goes_to_review(cfg):
    files = {"F001": SourceFile("F001", None, "x", "", ".pdf")}
    docs = assemble_documents(_cls(cfg, [_page("A shopping list with apples, bread and milk for the weekend")]), files, cfg)
    assert docs[0].type_id is None and docs[0].review_reasons


# --------------------------------------------------------------------------- verification

def _doc(doc_id, type_id, *persons, year=None):
    d = Document(doc_id=doc_id, type_id=type_id, type_name=type_id, pages=[Page("F001", 0, method="text layer")])
    d.persons = list(persons)
    if year:
        d.fields["year"] = year
    return d


def test_consistent_documents_are_correct(cfg):
    home = Address("45 OAK DR", "Houston", "TX", "77001")
    docs = [
        _doc("D001", "personal_tax_return", PersonRecord("primary", "Jane Q Doe", parse_ssn("123-45-6789"),
                                                         date(1990, 9, 15), home), year=2025),
        _doc("D002", "w2", PersonRecord("employee", "JANE DOE", SSN("6789"), None, Address("45 Oak Drive", zip5="77001"))),
        _doc("D003", "drivers_license", PersonRecord("holder", "Jane Quinn Doe", None, date(1990, 9, 15), home)),
    ]
    people = verify(docs, cfg)
    assert len(people) == 1
    statuses = {c.status for d in docs for c in d.checks}
    assert Status.ISSUE not in statuses and Status.CHECK not in statuses


def test_mismatches_are_reported_with_the_document_to_review(cfg):
    docs = [
        _doc("D001", "personal_tax_return", PersonRecord("primary", "Jane Doe", parse_ssn("123-45-6789"),
                                                         date(1990, 9, 15), Address("45 OAK DR", zip5="77001")), year=2025),
        _doc("D002", "ssn_card", PersonRecord("holder", "Jane Doe", parse_ssn("123-45-6789"))),
        _doc("D003", "w2", PersonRecord("employee", "Jane Doe", parse_ssn("123-45-9999"), None,
                                        Address("9 ELM ST", zip5="77002")), year=2024),
        _doc("D004", "drivers_license", PersonRecord("holder", "Jane Doe", None, date(1990, 9, 16),
                                                     Address("45 Oak Drive", zip5="77001"))),
    ]
    verify(docs, cfg)
    w2 = {c.field: c for c in docs[2].checks}
    assert w2["SSN"].status == Status.ISSUE and "9999" in w2["SSN"].reason and w2["SSN"].review_doc == "D002"
    assert w2["Address"].status == Status.ISSUE and "previous address" in w2["Address"].reason
    assert docs[2].result == Status.ISSUE
    # The ID card is the trusted source for date of birth, so the tax return is the one flagged.
    tax = {c.field: c for c in docs[0].checks}
    assert tax["DOB"].status == Status.ISSUE and tax["DOB"].review_doc == "D004"
    assert {c.field: c for c in docs[3].checks}["DOB"].status == Status.CORRECT


def test_unreadable_value_is_check_manually_not_issue(cfg):
    docs = [
        _doc("D001", "ssn_card", PersonRecord("holder", "Jane Doe", parse_ssn("123-45-6789"))),
        _doc("D002", "w2", PersonRecord("employee", "Jane Doe", None, None, Address("45 OAK DR", zip5="77001"))),
    ]
    verify(docs, cfg)
    assert {c.field: c.status for c in docs[1].checks}["SSN"] == Status.CHECK
    assert docs[1].result == Status.CHECK


def test_spouses_with_same_last_name_are_two_people(cfg):
    docs = [_doc("D001", "personal_tax_return",
                 PersonRecord("primary", "John K Doe", parse_ssn("123-45-6789")),
                 PersonRecord("spouse", "Jane S Doe", parse_ssn("223-45-6780")))]
    assert len(verify(docs, cfg)) == 2


def test_document_without_dob_is_not_applicable_not_review(cfg):
    docs = [_doc("D001", "w2", PersonRecord("employee", "Jane Doe", parse_ssn("123-45-6789"), None,
                                            Address("45 OAK DR", zip5="77001")))]
    verify(docs, cfg)
    assert {c.field: c.status for c in docs[0].checks}["DOB"] == Status.NOT_APPLICABLE

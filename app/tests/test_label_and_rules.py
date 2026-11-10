"""Label templates, configuration and classification rules (inline text snippets only)."""

from datetime import date

import pytest

from pcs_tool.config import load_config
from pcs_tool.engine.classify import classify_page, detect_year, order_index
from pcs_tool.engine.label import render_label, template_regex
from pcs_tool.engine.normalize import Address


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_config_loads_all_guide_rules(cfg):
    rules = sorted(t.guide_rule for t in cfg.types.values() if t.guide_rule)
    assert rules == list(range(1, 17)), "every Guide 1 rule (1-16) must have a configured type"


@pytest.mark.parametrize("type_id,fields,expected", [
    ("homeowners_insurance", {"property_address": Address("4821 MAPLE HOLLOW")}, "4821 Maple Hollow - HOI"),
    ("mortgage_statement", {"property_address": Address("310 Pine St"), "statement_date": date(2026, 5, 5)},
     "310 Pine St - MTG STMT - 5-5-26"),
    ("bank_statement", {"bank_name": "Chase", "account_last4": "xxxx4321", "statement_month": date(2026, 6, 30)},
     "ASSET - Chase - 4321 - June"),
    ("drivers_license", {"holder_name": "Jane Q Doe"}, "Jane - OTHER - DL"),
    ("paystub", {"employee_name": "Jane Doe", "employer": "Example Marketing LP", "pay_date": date(2026, 2, 13)},
     "Jane - INCOME - PAYSTUB - Example Marketing LP - 02-13-26"),
    ("business_tax_return", {"form": "1120S", "business_name": "EXAMPLE HOLDINGS", "year": 2024},
     "INCOME - 1120S - EXAMPLE HOLDINGS - 2024"),
    ("personal_tax_return", {"primary_name": "Jane Doe", "year": 2025}, "INCOME - 1040 - Jane - 2025"),
    ("w2", {"employee_name": "Jane Doe", "employer": "EXAMPLE LLC", "year": 2024}, "Jane - INCOME - W2 - EXAMPLE LLC - 2024"),
    ("contract_addendum", {"addendum_name": "THIRD PARTY FINANCING ADDENDUM",
                           "property_address": Address("900 Example Way")},
     "CONTRACT - Third Party Financing Addendum - 900 Example Way"),
])
def test_label_rendering_matches_guide_style(cfg, type_id, fields, expected):
    label, complete, missing = render_label(cfg.types[type_id].label, fields)
    assert (label, complete, missing) == (expected, True, [])


def test_missing_field_is_visible_and_flagged(cfg):
    label, complete, missing = render_label(cfg.types["w2"].label, {"employee_name": "Jane Doe", "year": 2024})
    assert not complete and missing == ["employer"]
    assert "[UNKNOWN EMPLOYER]" in label


def test_template_regex_parses_labels_back(cfg):
    m = template_regex(cfg.types["w2"].label).match("Jane - INCOME - W2 - EXAMPLE LLC - 2024")
    assert m and m.group("employer") == "EXAMPLE LLC" and m.group("year") == "2024"
    assert template_regex(cfg.types["w2"].label).match("Jane - income - W2 - X - 2024") is None
    assert template_regex(cfg.types["w2"].label, lenient=1).match("Jane - income - W2 - X - 2024")


# --------------------------------------------------------------------------- classification

W2_SNIPPET = """Form W-2 Wage and Tax Statement 2025
c Employer's name, address, and ZIP code
EXAMPLE CO LLC
e Employee's name, address, and ZIP code
OMB No. 1545-0008 Copy B"""

F1040_SNIPPET = """1040 U.S. Individual Income Tax Return 2025 OMB No. 1545-0074
Your first name and middle initial Last name Your social security number"""

SCHED_SE_MENTIONS_K1 = """SCHEDULE SE (Form 1040) Self-Employment Tax 2025 OMB No. 1545-0074
Name(s) shown on Form 1040 ... see Schedule K-1 (Form 1065), box 14"""

W2_BACK = """Instructions for Employee
Box 1. Enter this amount on the wages line of your tax return."""


@pytest.mark.parametrize("text,expected", [
    (W2_SNIPPET, "w2"),
    (F1040_SNIPPET, "personal_tax_return"),
    (SCHED_SE_MENTIONS_K1, "personal_tax_return"),   # a schedule that mentions K-1 is not a K-1
    ("THIS NUMBER HAS BEEN ESTABLISHED FOR\nJANE DOE\nSOCIAL SECURITY", "ssn_card"),
    ("TEXAS DRIVER LICENSE\n4a. Iss: 01/01/2020 4b. Exp: 01/01/2028\nDOB 01/01/1990", "drivers_license"),
    ("EMPLOYMENT AUTHORIZATION CARD\nUSCIS Category C09", "ead_card"),
    ("Beginning Balance $1.00 Ending Balance $2.00\nStatement Period 05/01/2026 - 05/31/2026\nAccount Number xxxx1234 Deposits Withdrawals", "bank_statement"),
])
def test_classification(cfg, text, expected):
    assert classify_page(text, cfg).type_id == expected


def test_w2_back_page_is_a_continuation(cfg):
    c = classify_page(W2_BACK, cfg)
    assert c.type_id is None and c.continuation_of == "w2"


def test_unknown_text_is_not_forced_into_a_type(cfg):
    assert classify_page("Grocery list: milk, eggs, bread, apples and some coffee", cfg).type_id is None


def test_tax_year_and_page_order(cfg):
    dt = cfg.types["personal_tax_return"]
    assert detect_year(dt, "Schedule 2 (Form 1040) 2024 Page 2") == 2024
    assert detect_year(dt, "TAX YEAR: 2025 PROCESS DATE: 04/14/2026") == 2025
    page1 = order_index(dt, F1040_SNIPPET)
    page2 = order_index(dt, "Form 1040 (2025) Page 2")
    sched_c = order_index(dt, "SCHEDULE C (Form 1040) Profit or Loss From Business")
    assert page1 < page2 < sched_c

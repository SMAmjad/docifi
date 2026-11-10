"""Step 7 - Cross-check Name, Date of birth, SSN and Address for every person.

How it works, in plain terms:
1. Every document says who it is about (one or more people).
2. People are matched across documents: same SSN, or same first + last name.
   Middle names, initials and small spelling differences are tolerated.
3. For each person and each field, the tool decides the "reference value":
   the value printed on the most trustworthy document (e.g. SSN card for SSN,
   ID for date of birth, the latest tax return for the home address).
4. Every document is compared with the reference. A difference becomes
   "Review" with the exact reason and the document to look at.

Residence addresses (where the person lives) and property addresses (the
house being financed or insured) are checked separately on purpose: they are
allowed to differ.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..config import Config
from ..models import Check, Document, PersonRecord, Status
from rapidfuzz import fuzz

from .normalize import SSN, Address, addresses_match, name_tokens, names_match

# How much each document type is trusted as the source of a field (higher = more trusted).
SOURCE_WEIGHT = {
    "ssn": {"ssn_card": 5, "personal_tax_return": 3, "w2": 2, "form_1099": 2, "k1": 2, "paystub": 1},
    "dob": {"drivers_license": 5, "passport": 5, "green_card": 5, "ead_card": 5, "personal_tax_return": 3},
    "address": {"personal_tax_return": 4, "drivers_license": 3, "w2": 2, "paystub": 2, "form_1099": 2,
                "k1": 2, "bank_statement": 2},
    "name": {"passport": 5, "drivers_license": 5, "ssn_card": 5, "green_card": 5, "ead_card": 5,
             "personal_tax_return": 4, "w2": 3},
}


@dataclass
class Person:
    person_id: str
    names: list[str] = field(default_factory=list)
    records: list[tuple[Document, PersonRecord]] = field(default_factory=list)
    reference: dict[str, Any] = field(default_factory=dict)
    reference_doc: dict[str, str] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """The name from the most trustworthy, best-read document (digital > clear scan > poor photo)."""
        best, best_score = "", -1.0
        for d, r in self.records:
            if not r.name:
                continue
            quality = min((p.ocr_confidence for p in d.pages), default=0) / 100
            score = SOURCE_WEIGHT["name"].get(d.type_id or "", 1) * quality + len(name_tokens(r.name)) * 0.01
            if score > best_score:
                best, best_score = r.name, score
        return best or "Unknown person"

    def ssns(self) -> list[SSN]:
        return [r.ssn for _, r in self.records if r.ssn]


def _same_person(p: Person, rec: PersonRecord, threshold: float) -> bool:
    for _, other in p.records:
        if rec.ssn and other.ssn:
            if rec.ssn.full and other.ssn.full:
                if rec.ssn.full == other.ssn.full:
                    return True
                one_digit_apart = sum(x != y for x, y in zip(rec.ssn.full, other.ssn.full)) == 1
                if one_digit_apart and rec.name and other.name and names_match(rec.name, other.name, threshold):
                    return True  # same person; one copy's SSN was misread (flagged later as 'check manually')
                continue
            if rec.ssn.last4 == other.ssn.last4 and rec.name and other.name:
                fa, fb = name_tokens(rec.name)[:1], name_tokens(other.name)[:1]
                # same last 4 digits + same first name (OCR may glue words: 'JANEIIJOHN')
                if fa and fb and (fa == fb or fuzz.partial_ratio(fa[0], fb[0]) >= 90):
                    return True
        if rec.name and other.name and names_match(rec.name, other.name, threshold):
            return True
    return False


def group_people(docs: list[Document], cfg: Config) -> list[Person]:
    threshold = float(cfg.settings.get("verification.name_match_threshold", 85))
    people: list[Person] = []
    # trusted documents first so that reference people are formed from the best data
    order = sorted(((d, r) for d in docs for r in d.persons),
                   key=lambda dr: -SOURCE_WEIGHT["name"].get(dr[0].type_id or "", 1))
    for d, rec in order:
        target = next((p for p in people if _same_person(p, rec, threshold)), None)
        if target is None:
            target = Person(person_id=f"P{len(people) + 1}")
            people.append(target)
        target.records.append((d, rec))
        if rec.name:
            target.names.append(rec.name)
        rec.person_id = target.person_id
    # stable, readable numbering: by first document
    people.sort(key=lambda p: min(d.doc_id for d, _ in p.records))
    for i, p in enumerate(people, start=1):
        p.person_id = f"P{i}"
        for _, rec in p.records:
            rec.person_id = p.person_id
    return people


def _weighted_choice(items: list[tuple[Any, Document]], kind: str, key) -> tuple[Any, Document] | None:
    """The reference value = the one printed on the most trustworthy document.

    Trust is decided by the *kind* of document (an ID card beats a tax return for date of
    birth), not by how many documents repeat a value: two copies of the same worksheet do not
    outvote a government-issued ID. Ties: more documents, then the most recent year.
    """
    if not items:
        return None
    best: dict[Any, tuple] = {}
    for val, d in items:
        k = key(val)
        weight = SOURCE_WEIGHT[kind].get(d.type_id or "", 1)
        year = d.fields.get("year") or d.fields.get("group_year") or 0
        w, n, y, pick = best.get(k, (0, 0, 0, (val, d)))
        if weight > w or (weight == w and year > y):
            pick = (val, d)
        best[k] = (max(w, weight), n + 1, max(y, year), pick)
    return max(best.values(), key=lambda t: (t[0], t[1], t[2]))[3]


def verify(docs: list[Document], cfg: Config) -> list[Person]:
    people = group_people(docs, cfg)
    addr_threshold = float(cfg.settings.get("verification.address_match_threshold", 90))
    name_threshold = float(cfg.settings.get("verification.name_match_threshold", 85))

    for p in people:
        recs = p.records
        ssn_ref = _weighted_choice([(r.ssn, d) for d, r in recs if r.ssn and r.ssn.full], "ssn", lambda s: s.full) \
            or _weighted_choice([(r.ssn, d) for d, r in recs if r.ssn], "ssn", lambda s: s.last4)
        dob_ref = _weighted_choice([(r.dob, d) for d, r in recs if r.dob], "dob", lambda x: x)
        addr_items = [(r.address, d) for d, r in recs if r.address and r.address.street and
                      cfg.types[d.type_id].verify.get("address") == "residence"]
        addr_ref = _weighted_choice(addr_items, "address", lambda a: a.key())
        name_ref = p.display_name
        p.reference = {"name": name_ref, "ssn": ssn_ref[0] if ssn_ref else None,
                       "dob": dob_ref[0] if dob_ref else None, "address": addr_ref[0] if addr_ref else None}
        p.reference_doc = {"ssn": ssn_ref[1].doc_id if ssn_ref else "", "dob": dob_ref[1].doc_id if dob_ref else "",
                           "address": addr_ref[1].doc_id if addr_ref else ""}

        for d, r in recs:
            v = cfg.types[d.type_id].verify
            who = f"{p.person_id}"
            for c in (_check_name(r, name_ref, name_threshold, who),
                      _check_ssn(r, p, v.get("ssn"), d),
                      _check_dob(r, p, v.get("dob"), d),
                      _check_address(r, p, v.get("address"), d, addr_threshold)):
                c.person = p.person_id
                d.checks.append(c)

    _check_property_addresses(docs, addr_threshold)
    return people


def _expected(flag: Any) -> str:
    """'required' | 'optional' | 'no'"""
    if flag in (True, "required", "residence", "property"):
        return "required"
    if flag == "optional":
        return "optional"
    return "no"


# Reason texts mention other documents by ID (D001); the report replaces the ID with the file name.

def _check_name(r: PersonRecord, ref: str, threshold: float, who: str) -> Check:
    if not r.name:
        return Check("Name", Status.CHECK, "Name not readable on this scan.")
    if names_match(r.name, ref, threshold):
        return Check("Name", Status.CORRECT)
    ta, tb = name_tokens(r.name), name_tokens(ref)
    if ta[:1] == tb[:1]:
        return Check("Name", Status.ISSUE, "Last name differs from other documents (maiden or married name?).")
    return Check("Name", Status.ISSUE, "Name differs from the person's other documents.")


def _check_ssn(r: PersonRecord, p: Person, flag: Any, d: Document) -> Check:
    exp = _expected(flag)
    if exp == "no":
        return Check("SSN", Status.NOT_APPLICABLE)
    ref: SSN | None = p.reference.get("ssn")
    if not r.ssn:
        if exp == "required":
            return Check("SSN", Status.CHECK, "SSN not readable on this scan (or not printed).")
        return Check("SSN", Status.NOT_APPLICABLE)
    if ref is None:
        return Check("SSN", Status.CORRECT)
    same = (r.ssn.full == ref.full) if (r.ssn.full and ref.full) else (r.ssn.last4 == ref.last4)
    if same:
        return Check("SSN", Status.CORRECT, "" if r.ssn.full else "Only last 4 digits printed; matched on last 4.")
    src = p.reference_doc["ssn"]
    scanned = any(pg.method == "ocr" for pg in d.pages)
    if r.ssn.full and ref.full and scanned and sum(x != y for x, y in zip(r.ssn.full, ref.full)) == 1:
        # One digit apart on a scan is far more likely a misread than a different number.
        return Check("SSN", Status.CHECK, f"SSN on this scan differs from {src} by one digit - "
                                          "probably a misread; confirm by eye.", src)
    if r.ssn.last4 == ref.last4:
        return Check("SSN", Status.ISSUE, f"SSN differs from {src} in the first 5 digits (same last 4).", src)
    return Check("SSN", Status.ISSUE, f"SSN ends in {r.ssn.last4} here but {ref.last4} on {src}.", src)


def _check_dob(r: PersonRecord, p: Person, flag: Any, d: Document) -> Check:
    exp = _expected(flag)
    if exp == "no":
        return Check("DOB", Status.NOT_APPLICABLE)
    ref: date | None = p.reference.get("dob")
    if not r.dob:
        if exp == "required":
            return Check("DOB", Status.CHECK, "Date of birth not readable on this scan.")
        return Check("DOB", Status.NOT_APPLICABLE)
    if ref is None or r.dob == ref:
        return Check("DOB", Status.CORRECT)
    src = p.reference_doc["dob"]
    if (r.dob.day, r.dob.month) == (ref.month, ref.day):
        return Check("DOB", Status.ISSUE, f"Date of birth has day and month swapped compared with {src}.", src)
    return Check("DOB", Status.ISSUE, f"Date of birth differs from {src}.", src)


def _check_address(r: PersonRecord, p: Person, flag: Any, d: Document, threshold: float) -> Check:
    if flag != "residence":
        return Check("Address", Status.NOT_APPLICABLE)
    ref: Address | None = p.reference.get("address")
    if not r.address or not r.address.street:
        return Check("Address", Status.CHECK, "Home address not readable on this scan.")
    if ref is None or addresses_match(r.address, ref, threshold):
        return Check("Address", Status.CORRECT)
    src = p.reference_doc["address"]
    return Check("Address", Status.ISSUE,
                 f"Home address differs from {src} (current address). May be a previous address - confirm.", src)


def _check_property_addresses(docs: list[Document], threshold: float) -> None:
    """Property documents in the same folder should describe the same subject property."""
    by_folder: dict[str, list[Document]] = defaultdict(list)
    for d in docs:
        if d.property_address and d.property_address.street:
            by_folder[d.folder].append(d)
    for group in by_folder.values():
        if len(group) < 2:
            continue
        keys = Counter(d.property_address.key() for d in group)  # type: ignore[union-attr]
        ref_key = keys.most_common(1)[0][0]
        ref_doc = next(d for d in group if d.property_address.key() == ref_key)  # type: ignore[union-attr]
        for d in group:
            if d is ref_doc or addresses_match(d.property_address, ref_doc.property_address, threshold):  # type: ignore[arg-type]
                d.checks.append(Check("Property address", Status.CORRECT))
            else:
                d.checks.append(Check("Property address", Status.ISSUE,
                                      f"Property address differs from {ref_doc.doc_id} (possibly a second property).",
                                      ref_doc.doc_id))

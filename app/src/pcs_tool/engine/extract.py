"""Step 5 - Read the facts we need from each document.

Two kinds of facts are read:
* label facts  - what the file name needs (bank, last 4 digits, employer, year, ...)
* check facts  - who the document is about: Name, Date of birth, SSN, Address

Fields are described in document_types.yaml as a *kind* plus *anchors*
(the printed caption to look near, e.g. "Employee's name"). This module holds
one small reader per kind. Readers return (value, confidence) and never raise.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any
from collections.abc import Callable

from ..config import Config, DocType
from ..models import Document, Page, PersonRecord
from .normalize import (
    SSN, Address, clean_person_name, clean_text, find_ssns, find_years, is_street_line,
    lines_of, parse_city_state_zip, parse_date, search_text,
)
from .ocr import SPARSE_MARKER
from .ocr_second import SECOND_MARKER

Value = tuple[Any, float]
NONE: Value = (None, 0.0)

COMPANY_WORDS = {
    "llc", "inc", "inc.", "corp", "corporation", "co", "company", "ltd", "lp", "llp", "pa", "pc", "pllc",
    "isd", "i.s.d.", "district", "school", "university", "hospital", "clinic", "services", "service",
    "group", "associates", "interests", "holdings", "partners", "bank", "trust", "enterprises",
    "international", "industries", "solutions", "systems", "technologies", "management", "foundation",
    "county", "city", "state", "department", "health", "medical", "dental", "od", "md", "dds", "store", "stores",
}
LABEL_WORDS = {
    "name", "address", "zip", "code", "employer", "employee", "employee's", "employer's", "number", "social",
    "security", "wages", "tips", "compensation", "federal", "withheld", "medicare", "control", "statement",
    "form", "omb", "copy", "state", "local", "income", "tax", "box", "instructions", "department", "treasury",
    "internal", "revenue", "service", "void", "see", "allocated", "dependent", "benefits", "nonqualified",
    "plans", "statutory", "retirement", "plan", "third-party", "sick", "pay", "other", "locality", "suff",
    "last", "first", "initial", "middle", "apt", "no", "spouse", "spouse's", "your", "joint", "return",
    "signature", "established", "this", "has", "been", "for", "the", "and", "of", "if", "city", "town",
    "post", "office", "foreign", "country", "province", "county", "filing", "status", "check", "here",
    "single", "married", "head", "household", "date", "birth", "dob", "exp", "iss", "class", "sex",
    "hgt", "eyes", "driver", "license", "director", "rest", "to", "be", "filed", "with",
    "premium", "ot", "care", "furnished", "information", "being", "required",
}


# =========================================================================== line helpers

def _main_lines(text: str) -> list[str]:
    """Lines of the main reading (the second reading is used only as a fallback)."""
    return lines_of(re.split(f"{re.escape(SPARSE_MARKER)}|{re.escape(SECOND_MARKER)}", text)[0])


def _all_lines(text: str) -> list[str]:
    return lines_of(text.replace(SPARSE_MARKER, "").replace(SECOND_MARKER, ""))


def _anchor_positions(lines: list[str], anchors: list[str]) -> list[tuple[int, int]]:
    """(line index, char end of anchor) for every line containing any anchor (case-insensitive)."""
    out = []
    for i, ln in enumerate(lines):
        low = ln.lower().replace("’", "'")
        for a in anchors:
            j = low.find(a.lower())
            if j >= 0:
                out.append((i, j + len(a)))
                break
            end = _squashed_find(low, a.lower())  # photo readers may drop spaces: "employer'sname,address"
            if end is not None:
                out.append((i, end))
                break
    return out


def _squashed_find(text: str, anchor: str) -> int | None:
    """Find anchor ignoring spaces; return the index in text just after the match."""
    a = anchor.replace(" ", "")
    if len(a) < 8:
        return None
    chars = [(k, c) for k, c in enumerate(text) if c != " "]
    squashed = "".join(c for _, c in chars)
    j = squashed.find(a)
    if j < 0:
        return None
    return chars[j + len(a) - 1][0] + 1


def _tokens(line: str) -> list[str]:
    return [t for t in re.split(r"\s+", line.strip()) if t]


def strip_noise_tail(line: str) -> str:
    """Remove trailing OCR junk such as ' j', ' ic 17', ' a', ' | | CL] 3'."""
    toks = _tokens(re.sub(r"[|\[\]{}()<>@©®~=_\"“”‘’`]+", " ", line))
    while toks and (len(re.sub(r"[^A-Za-z]", "", toks[-1])) <= 2 or re.fullmatch(r"[\d.,:;/-]+", toks[-1])):
        toks.pop()
    while toks and len(re.sub(r"[^A-Za-z]", "", toks[0])) == 0:
        toks.pop(0)
    return " ".join(toks)


def is_company_like(line: str) -> bool:
    words = {w.strip(".,").lower() for w in _tokens(line)} | {w.replace(".", "").strip(",").lower() for w in _tokens(line)}
    return bool(words & COMPANY_WORDS)


def is_name_like(line: str, allow_company: bool = False) -> bool:
    """A line that looks like a person's name: 2-5 alphabetic words, no digits, not a caption."""
    line = strip_noise_tail(line)
    toks = _tokens(line)
    if not 2 <= len(toks) <= 5 or re.search(r"\d", line):
        return False
    alpha = [re.sub(r"[^A-Za-z'-]", "", t) for t in toks]
    if any(not a for a in alpha):
        return False
    # real name words have a vowel and a consonant ('EEE', 'XX' are OCR noise)
    if any(len(a) > 1 and not (re.search(r"[aeiouyAEIOUY]", a) and re.search(r"[^aeiouyAEIOUY'-]", a)) for a in alpha):
        return False
    lows = {a.lower() for a in alpha}
    if lows & LABEL_WORDS and len(lows & LABEL_WORDS) >= max(1, len(lows) // 2):
        return False
    if not allow_company and is_company_like(line):
        return False
    long_words = [a for a in alpha if len(a) >= 2]
    return len(long_words) >= 2


def _is_value_line(line: str) -> bool:
    """Not a printed caption (captions are mostly label words)."""
    words = [w.strip(".,:;'()").lower() for w in _tokens(line)]
    words = [w for w in words if len(w) >= 2 and not w.isdigit()]
    if not words:
        return False
    label_share = sum(1 for w in words if w in LABEL_WORDS) / len(words)
    return label_share < 0.5


def _address_after(lines: list[str], start: int, window: int) -> Address | None:
    """Find 'street' then 'city state zip' within a window of lines."""
    for i in range(start, min(len(lines), start + window)):
        cand = strip_street(lines[i])
        if cand and is_street_line(cand):
            unit = ""
            for k in range(i + 1, min(len(lines), i + 4)):
                nxt = lines[k].strip()
                if re.fullmatch(r"#?\d{1,5}[A-Za-z]?", nxt) and not re.fullmatch(r"\d{5}", nxt):
                    unit = f" UNIT {nxt.lstrip('#')}"
                    continue
                csz = parse_city_state_zip(lines[k])
                if csz:
                    return Address(street=(cand + unit).upper(), city=csz[0], state=csz[1], zip5=csz[2])
            # city / state / zip printed on separate lines (digital forms)
            joined = " ".join(lines[i + 1:i + 14])
            csz = parse_city_state_zip(joined)
            if csz:
                city = csz[0].split()[-1] if csz[0] else ""
                return Address(street=(cand + unit).upper(), city=city.title(), state=csz[1], zip5=csz[2])
            return Address(street=(cand + unit).upper())
    return None


_NUMBERED_ROADS = {"rd", "road", "hwy", "highway", "route", "rte", "fm", "cr", "sh", "us", "box"}
_UNIT_WORDS = {"apt", "apartment", "unit", "ste", "suite", "#", "no", "bldg"}


def strip_street(line: str) -> str:
    """Keep the street part of a line that may carry neighbouring box text.

    '4821 MAPLE CT 3 FE EE'              -> '4821 MAPLE CT'
    '1700 RIVER END DR APT 12 1D ...'    -> '1700 RIVER END DR APT 12'
    '300 COUNTY RD 47'                    -> '300 COUNTY RD 47'
    """
    line = clean_text(line).strip().strip("\"'“”‘’`")
    # house number misread with letter O / l: '1O1O1 WOODLOCH' -> '10101 WOODLOCH'
    line = re.sub(r"^([0-9OolI]{2,6})(?=\s|[A-Z])", lambda m: m.group(1).translate(str.maketrans("OolI", "0011"))
                  if sum(c.isdigit() for c in m.group(1)) >= 2 else m.group(1), line)
    po = re.match(r"^\s*P\s*[.,]?\s*O\s*[.,:]?\s*BOX\s*(\d+)", line, re.IGNORECASE)
    if po:
        return f"P.O. BOX {po.group(1)}"
    m = re.match(r"^\s*(\d{1,6}[A-Za-z]?\s+(?:[A-Za-z0-9#.'-]+\s?){1,9})", line)
    if not m:
        return ""
    street = m.group(1).strip()
    street = re.split(r"\s(?:PREPARER|the U\.S\.|Check|Apt\. no)\b", street)[0]
    toks = _tokens(street)
    from .normalize import STREET_SUFFIX
    last_suffix = max((i for i, t in enumerate(toks) if i >= 1 and t.lower().strip(".,") in STREET_SUFFIX), default=-1)
    if last_suffix >= 1:
        keep = toks[: last_suffix + 1]
        rest = toks[last_suffix + 1:]
        if rest and toks[last_suffix].lower().strip(".") in _NUMBERED_ROADS and re.fullmatch(r"\d{1,4}", rest[0]):
            keep.append(rest.pop(0))
        if rest and rest[0].lower().strip(".") in _UNIT_WORDS and len(rest) > 1 and re.fullmatch(r"#?\w{1,5}", rest[1]):
            keep += rest[:2]
        elif rest and re.fullmatch(r"#\w{1,5}", rest[0]):
            keep.append(rest[0])
        toks = keep
    while toks and len(toks) > 2 and (len(re.sub(r"[^A-Za-z0-9]", "", toks[-1])) <= 1 or toks[-1].lower() in LABEL_WORDS):
        toks.pop()
    return " ".join(toks)


# =========================================================================== name/address blocks

def name_address_blocks(lines: list[str]) -> list[tuple[int, str, Address]]:
    """Every 'NAME / street / city state zip' block on a page: (line index, name, address)."""
    blocks = []
    for i in range(len(lines) - 1):
        original = re.sub(r"[|\[\]{}<>@©®~=_\"“”‘’`]+", " ", lines[i]).strip(" .,;:-")
        original = re.sub(r"(\s+[\d.,$]+)+\s*$", "", original)  # box amounts printed on the same line
        raw = strip_noise_tail(lines[i])
        if len(_tokens(original)) > 6 or len(original) > 50 or re.search(r"\d", original):
            continue  # names are short; sentences and box text are not names
        company = is_company_like(original)  # judge on the full line: 'SMITH OD PA' is a business
        if not (is_name_like(raw, allow_company=True) or (company and _is_value_line(original))):
            continue
        st = strip_street(lines[i + 1])  # the street is printed directly under the name
        if not (st and is_street_line(st)) or _label_word_count(st) >= 2:
            continue
        addr = _address_after(lines, i + 1, 4)
        if addr and addr.zip5:
            blocks.append((i, original if company else raw, addr))
    return blocks


def _label_word_count(s: str) -> int:
    return sum(1 for w in _tokens(s) if w.lower().strip(".,") in LABEL_WORDS)


# =========================================================================== kind readers

def _majority_ssn(found: list[SSN], text: str) -> SSN:
    """Forms print the SSN on every copy; if OCR misreads one copy, the most frequent reading wins."""
    if len(found) == 1:
        return found[0]
    counts = Counter()
    for s in found:
        key = s.full or s.last4
        counts[s] = sum(1 for m in re.finditer(r"\d{3}[- ]?\d{2}[- ]?\d{4}", text)
                        if re.sub(r"\D", "", m.group(0)) == key) + (0.5 if s.full else 0)
    return max(found, key=lambda s: counts[s])


def read_ssn(lines: list[str], spec: dict) -> Value:
    anchors = spec.get("anchors") or []
    if anchors:
        for i, end in _anchor_positions(lines, anchors):
            window = "\n".join([lines[i][end:]] + lines[i + 1:i + 1 + int(spec.get("lines_after", 2))])
            found = find_ssns(window, strict=spec.get("strict", True))  # EINs read without a dash look like SSNs
            if found:
                page = "\n".join(lines)
                same_family = [s for s in find_ssns(page, strict=True) if s.last4 == found[0].last4] or found
                return _majority_ssn(same_family, page), 0.9
    if spec.get("search_all") or not anchors:
        # Away from a caption only properly formatted SSNs count (123-45-6789 / XXX-XX-6789), so that
        # barcodes, control numbers and account numbers are never taken for an SSN.
        text = "\n".join(lines)
        found = find_ssns(text, strict=spec.get("strict", True))
        if found:
            return _majority_ssn(found, text), 0.75
        if spec.get("search_all") and spec.get("strict", True) is False:
            # SSN cards only (the card carries no other number): if the number is broken by
            # OCR, keep the last 4 digits of the long digit run near the top.
            for ln in lines[:8]:
                digits = re.sub(r"\D", "", ln)
                if 8 <= len(digits) <= 10 and not re.search(r"[a-z]{3}", ln.lower()):
                    return SSN(last4=digits[-4:]), 0.5
    return NONE


def read_person_name(lines: list[str], spec: dict) -> Value:
    for i, end in _anchor_positions(lines, spec.get("anchors") or []):
        rest = strip_noise_tail(lines[i][end:].strip(" :.-"))
        if rest and is_name_like(rest):
            return clean_person_name(rest), 0.85
        n_join = int(spec.get("join_lines", 1))
        for k in range(i + 1, min(len(lines), i + 1 + int(spec.get("lines_after", 2)))):
            cand = strip_noise_tail(" ".join(lines[k:k + n_join]))
            cand = re.sub(r"[^A-Za-z' \-]", " ", cand)
            if is_name_like(cand):
                return clean_person_name(cand), 0.8
            single = strip_noise_tail(lines[k])
            if is_name_like(single):
                return clean_person_name(single), 0.8
    return NONE


def read_company(lines: list[str], spec: dict) -> Value:
    for i, end in _anchor_positions(lines, spec.get("anchors") or []):
        rest = lines[i][end:].strip(" :.-|")
        if rest and _is_value_line(rest) and not re.fullmatch(r"[\d\W]+", rest) and _looks_printed(rest):
            return _tidy_company(rest), 0.8
        for k in range(i + 1, min(len(lines), i + 1 + int(spec.get("lines_after", 2)))):
            ln = strip_noise_tail(lines[k])
            if ln and _is_value_line(ln) and not is_street_line(ln) and not re.search(r"\d{3,}", ln) \
                    and _looks_printed(ln):
                return _tidy_company(ln), 0.75
    top = int(spec.get("top_lines", 0))
    for ln in lines[:top]:
        if is_company_like(ln):
            return _tidy_company(ln), 0.6
    return NONE


def _looks_printed(s: str) -> bool:
    """Business names on forms are printed in capitals or contain a business word; OCR noise is mixed case."""
    letters = re.sub(r"[^A-Za-z]", "", s)
    return bool(letters) and (is_company_like(s) or sum(c.isupper() for c in letters) / len(letters) >= 0.8)


def _tidy_company(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" ,.;:-|")
    return s


def read_address(lines: list[str], spec: dict) -> Value:
    for i, end in _anchor_positions(lines, spec.get("anchors") or []):
        same = lines[i][end:].strip(" :.-")
        if same:
            st = strip_street(same)
            if st and is_street_line(st):
                csz = parse_city_state_zip(same)
                if csz:
                    return Address(st.upper(), csz[0], csz[1], csz[2]), 0.85
                tail = _address_after([same] + lines[i + 1:], 0, int(spec.get("lines_after", 3)) + 1)
                if tail:
                    return tail, 0.8
        addr = _address_after(lines, i + 1, int(spec.get("lines_after", 3)) + 1)
        if addr:
            return addr, 0.8
    if spec.get("search_all"):
        blocks = name_address_blocks(lines)
        if blocks:
            return blocks[0][2], 0.6
    return NONE


def read_date(lines: list[str], spec: dict) -> Value:
    pos = _anchor_positions(lines, spec.get("anchors") or [])
    if spec.get("prefer_top"):
        pos = sorted(pos, key=lambda x: x[0])
    for i, end in pos:
        window = " ".join([lines[i][end:]] + lines[i + 1:i + 1 + int(spec.get("lines_after", 1))])
        d = parse_date(window)
        if d:
            return d, 0.85
    return NONE


def read_year(lines: list[str], spec: dict) -> Value:
    def plausible(y: int) -> bool:  # documents for a current loan are rarely older than 15 years
        return date.today().year - 15 <= y <= date.today().year + 1

    for i, end in _anchor_positions(lines, spec.get("anchors") or []):
        window = " ".join([lines[i][end:]] + lines[i + 1:i + 1 + int(spec.get("lines_after", 1))])
        window = re.sub(r"\b(2)\s?(0)\s?(\d)\s?(\d)\b", r"\1\2\3\4", window)  # "2 024" -> "2024"
        ys = [y for y in find_years(window) if plausible(y)]
        if ys:
            return ys[0], 0.85
    if spec.get("search_all"):
        text = re.sub(r"\b(2)\s?(0)\s?(\d)\s?(\d)\b", r"\1\2\3\4", " ".join(lines))
        text = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b", " ", text)  # ignore full dates
        ys = [y for y in find_years(text) if plausible(y)]
        if ys:
            return Counter(ys).most_common(1)[0][0], 0.6
    return NONE


def read_last4(lines: list[str], spec: dict) -> Value:
    for i, end in _anchor_positions(lines, spec.get("anchors") or []):
        window = " ".join([lines[i][end:]] + lines[i + 1:i + 1 + int(spec.get("lines_after", 1))])
        m = re.search(r"(?:ending in|x{2,}|\*{2,}|#)?\s*[\dXx*\- ]*?(\d{4})\b", window)
        if m:
            return m.group(1), 0.8
    return NONE


def read_bank(lines: list[str], spec: dict, cfg: Config) -> Value:
    top = search_text("\n".join(lines[: int(spec.get("top_lines", 8))]))
    for key in sorted(cfg.bank_aliases, key=len, reverse=True):
        if key in top:
            return cfg.bank_aliases[key], 0.85
    full = search_text("\n".join(lines))
    for key in sorted(cfg.bank_aliases, key=len, reverse=True):
        if key in full:
            return cfg.bank_aliases[key], 0.6
    for ln in lines[:6]:
        if "bank" in ln.lower() or "credit union" in ln.lower():
            return _tidy_company(ln), 0.5
    return NONE


def read_period_month(lines: list[str], spec: dict) -> Value:
    text = " ".join(lines)
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4})\s*(?:-|to|through|thru)\s*"
                  r"(\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4})", text)
    if m:
        d = parse_date(m.group(2))
        if d:
            return d, 0.85
    return read_date(lines, spec)


def read_heading(lines: list[str], spec: dict) -> Value:
    for ln in lines[: int(spec.get("top_lines", 8))]:
        low = ln.lower()
        if any(k in low for k in spec.get("keywords") or []):
            return re.sub(r"\s+", " ", ln).strip(" .:-"), 0.8
    return NONE


def read_form_number(lines: list[str], spec: dict) -> Value:
    full = search_text("\n".join(lines))
    for form, phrases in (spec.get("options") or {}).items():
        if any(p in full for p in phrases):
            return form, 0.9
    return NONE


def read_id_name(lines: list[str], spec: dict) -> Value:
    """Names on ID cards: passport MRZ, 'Surname/Given names', or US licence fields 1 (last) and 2 (first)."""
    text = "\n".join(lines)
    mrz = re.search(r"P<[A-Z]{3}([A-Z]+)<<([A-Z<]+)", text.replace(" ", ""))
    if mrz:
        given = mrz.group(2).replace("<", " ").strip()
        return clean_person_name(f"{given} {mrz.group(1)}"), 0.8
    given = read_person_name(lines, {"anchors": ["given names", "given name", "first name", "fn"], "lines_after": 1})
    sur = read_person_name(lines, {"anchors": ["surname", "last name", "ln"], "lines_after": 1})
    if given[0]:
        g = given[0]
        return (f"{g} {sur[0]}" if sur[0] else g), 0.7
    # US driver's licences number their fields: "1 LAST NAME", "2 FIRST MIDDLE" (OCR may drop the dot or space)
    last = first = None
    for j, ln in enumerate(lines):
        m1 = re.match(r"^\s*1\s*[.,]?\s*([A-Z][A-Z'-]{1,30}(?:\s[A-Z][A-Z'-]{1,30})?)\s*$", ln)
        m2 = re.match(r"^\s*2\s*[.,]?\s*([A-Z][A-Z'-]{1,30}(?:\s[A-Z][A-Z'-]{1,30}){0,2})\s*$", ln)
        if m1 and not last:
            last = m1.group(1)
        if m2 and not first:
            first = m2.group(1)
            if not last and j > 0:  # field 1 (last name) sits right above field 2, its "1." often lost
                prev = re.sub(r"^\s*1\s*[.,]?\s*", "", lines[j - 1].strip())
                if re.fullmatch(r"[A-Z][A-Z'-]{1,30}(?:\s[A-Z][A-Z'-]{1,30})?", prev) and prev.lower() not in LABEL_WORDS:
                    last = prev
    if first:
        return clean_person_name(f"{first} {last or ''}"), 0.75
    # Unnumbered (photo readers often drop the "1." / "2."): the two capitalised name lines
    # that follow the date-of-birth line and come before the address (field 8).
    for i, ln in enumerate(lines):
        if _DOB_LABEL.search(ln):
            names = []
            for nxt in lines[i + 1:i + 8]:
                s = re.sub(r"^\s*[12]\s*[.,]?\s*(?=[A-Z])", "", nxt.strip())
                if re.match(r"^\s*8\s*[.,:]?\s*\d", nxt):
                    break
                if re.fullmatch(r"[A-Z][A-Z'-]+(?:\s[A-Z][A-Z'-]+){0,2}", s) and s.lower() not in LABEL_WORDS \
                        and not re.search(r"LICENSE|DRIVER|TEXAS|DIRECTOR|CLASS", s):
                    names.append(s)
                if len(names) == 2:
                    return clean_person_name(f"{names[1]} {names[0]}"), 0.65
    return NONE


_DOB_LABEL = re.compile(r"(?<![A-Za-z])D\s*[O0]\s*[BE8P](?![A-Za-z])|date of birth|birth date", re.IGNORECASE)


def read_id_dob(lines: list[str], spec: dict) -> Value:
    """Date of birth on an ID: the date written right after 'DOB' (OCR may drop a slash: 06/171991)."""
    for ln in lines:
        m = _DOB_LABEL.search(ln)
        if not m:
            continue
        tail = ln[m.end():]
        d = re.match(r"[^0-9]{0,4}(\d{1,2})\s*[/.-]?\s*(\d{1,2})\s*[/.-]?\s*(\d{4})", tail)
        if d:
            try:
                dob = date(int(d.group(3)), int(d.group(1)), int(d.group(2)))
            except ValueError:
                continue
            if 1900 <= dob.year <= date.today().year - 14:
                return dob, 0.8
    return NONE


def read_id_address(lines: list[str], spec: dict) -> Value:
    """Licence field 8: '8 12437 MAIN ST' (OCR may squash it to '8.12437MAINST'), then 'CITY, ST 12345'."""
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*8\s*[.,:]?\s*(\d{1,6})\s*([A-Za-z].*)$", ln.strip())
        if not m:
            continue
        street = f"{m.group(1)} {m.group(2)}".strip()
        for nxt in lines[i + 1:i + 3]:
            csz = parse_city_state_zip(nxt)
            if csz:
                return Address(street.upper(), csz[0], csz[1], csz[2]), 0.7
        return Address(street.upper()), 0.6
    return NONE


def read_w2_employee(lines: list[str]) -> tuple[Value, Value]:
    """(name, address) of the employee on a W-2.

    First choice: a complete 'person name / street / city state zip' block (most reliable on
    multi-copy W-2s). Fallback: the line under the "Employee's name" caption.
    """
    blocks = [b for b in name_address_blocks(lines) if not is_company_like(b[1]) and is_name_like(b[1])]
    if blocks:
        _, nm, addr = blocks[0]
        return (clean_person_name(nm), 0.85), (addr, 0.85)
    anchors = ["employee's first name", "employee's name", "employee name", "e/f employee"]
    for i, _ in _anchor_positions(lines, anchors):
        for k in range(i + 1, min(len(lines), i + 4)):
            cand = strip_noise_tail(lines[k])
            if is_name_like(cand) and not is_company_like(cand):
                addr = _address_after(lines, k + 1, 4)
                return (clean_person_name(cand), 0.75), ((addr, 0.7) if addr else NONE)
    return NONE, NONE


def read_w2_employer(lines: list[str]) -> Value:
    blocks = [b for b in name_address_blocks(lines) if is_company_like(b[1])]
    if blocks:
        return _tidy_company(blocks[0][1]), 0.8
    return read_company(lines, {"anchors": ["employer's name, address", "employer's name"], "lines_after": 3})


# =========================================================================== tax return (multi-page)

_JOINT = re.compile(r"^([A-Z][A-Z'.-]+(?:\s+[A-Z][A-Z'.-]*){0,3}?)\s+(?:&|AND|and)\s+([A-Z][A-Z'.-]+(?:\s+[A-Z][A-Z'.-]*){0,3}?)"
                    r"(?=\s+(?:\d{3}-\d{2}-\d{4}|LINK|Link)|\s*$)")


def _split_joint(line: str) -> tuple[str, str] | None:
    """'JANE SMITH & JOHN DOE 123-...' or 'JOHN Q and JANE R DOE' -> two full names."""
    line = re.sub(r"[_]+", " ", line).strip()
    m = _JOINT.match(line)
    if not m:
        return None
    a, b = m.group(1).strip(), m.group(2).strip()
    a_t, b_t = a.split(), b.split()
    if len(a_t) <= 2 and len(a_t[-1]) <= 1 or (len(a_t) == 1):
        a = f"{a} {b_t[-1]}"  # shared last name: 'JOHN Q and JANE R DOE'
    if any(w.lower() in LABEL_WORDS for w in (a + " " + b).split() if len(w) > 2):
        return None
    return clean_person_name(a), clean_person_name(b)


def extract_tax_return(pages: list[Page]) -> dict[str, Value]:
    out: dict[str, Value] = {}
    ssn_counter: Counter[str] = Counter()
    ssn_obj: dict[str, SSN] = {}
    for p in pages:
        for s in find_ssns(p.text):
            if s.full:
                ssn_counter[s.full] += 1
                ssn_obj[s.full] = s
        lines = _main_lines(p.text)
        low = search_text(p.text)

        # 1) tax preparer cover sheet: CLIENT / SPOUSE lines with SSN, name and DOB
        for ln in lines:
            m = re.match(r"^(CLIENT|SPOUSE)\s*:?\s*(?:(\d{3}-\d{2}-\d{4})\s+)?([A-Z][A-Z' .-]+?)\s*"
                         r"(?=BIRTH DATE|\d{3}-\d{2}-\d{4}|$)", ln.strip())
            if not m:
                continue
            role = "primary" if m.group(1) == "CLIENT" else "spouse"
            name = clean_person_name(m.group(3))
            if len(name.split()) >= 2:
                out.setdefault(f"{role}_name", (name, 0.9))
            ssns = find_ssns(ln)
            if ssns:
                out.setdefault(f"{role}_ssn", (ssns[0], 0.9))
            bd = re.search(r"BIRTH DATE\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", ln)
            if bd and parse_date(bd.group(1)):
                out.setdefault(f"{role}_dob", (parse_date(bd.group(1)), 0.9))
        if "address" in low and "tax year:" in low:
            for i, ln in enumerate(lines):
                if ln.upper().startswith("ADDRESS"):
                    addr = _address_after([ln.split(":", 1)[-1]] + lines[i + 1:i + 3], 0, 3)
                    if addr and addr.zip5:
                        out.setdefault("home_address", (addr, 0.85))

        # 2) software worksheet: "Date of birth" followed by taxpayer and spouse dates
        if "individual filer information" in low:
            for i, ln in enumerate(lines):
                if ln.lower().startswith("date of birth"):
                    dates = [parse_date(x) for x in lines[i + 1:i + 4]]
                    dates = [d for d in dates if d]
                    if dates:
                        out.setdefault("primary_dob", (dates[0], 0.8))
                    if len(dates) > 1:
                        out.setdefault("spouse_dob", (dates[1], 0.8))
                    break

        # 3) "Name(s) shown on return" style joint name lines
        for ln in lines:
            j = _split_joint(ln)
            if j:
                out.setdefault("_joint_primary", (j[0], 0.8))
                out.setdefault("_joint_spouse", (j[1], 0.8))
                break

        # 4) Form 1040 page 1
        if "your first name and middle initial" in low:
            pf = read_person_name(lines, {"anchors": ["your first name and middle initial"], "lines_after": 2})
            sf = read_person_name(lines, {"anchors": ["spouse's first name and middle initial", "spouse’s first name and middle initial"], "lines_after": 2})
            first_vals = _first_values_after(lines, "your first name and middle initial")
            spouse_vals = _first_values_after(lines, "spouse's first name and middle initial")
            if pf[0]:
                out.setdefault("_p1_primary", pf)
            elif first_vals:
                out.setdefault("_p1_primary", (clean_person_name(first_vals), 0.7))
            if sf[0]:
                out.setdefault("_p1_spouse", sf)
            elif spouse_vals:
                out.setdefault("_p1_spouse", (clean_person_name(spouse_vals), 0.7))
            ssns = find_ssns(p.text)
            if ssns:
                out.setdefault("_p1_primary_ssn", (ssns[0], 0.85))
            if len(ssns) > 1:
                out.setdefault("_p1_spouse_ssn", (ssns[1], 0.85))
            for i, _ in _anchor_positions(lines, ["home address (number and street)"]):
                addr = _address_after(lines, i + 1, 8)
                if addr:
                    out.setdefault("home_address", (addr, 0.85))
                    break

    # combine sources (strongest first)
    for role in ("primary", "spouse"):
        if f"{role}_name" not in out:
            for k in (f"_joint_{role}", f"_p1_{role}"):
                if k in out and len(str(out[k][0]).split()) >= 2:
                    out[f"{role}_name"] = out[k]
                    break
            else:
                if f"_p1_{role}" in out:
                    out[f"{role}_name"] = (out[f"_p1_{role}"][0], 0.6)
        if f"{role}_ssn" not in out and f"_p1_{role}_ssn" in out:
            out[f"{role}_ssn"] = out[f"_p1_{role}_ssn"]
    if "primary_ssn" not in out and ssn_counter:
        out["primary_ssn"] = (ssn_obj[ssn_counter.most_common(1)[0][0]], 0.7)
    return {k: v for k, v in out.items() if not k.startswith("_")}


def _first_values_after(lines: list[str], anchor: str) -> str:
    """First name value under a caption when columns are read separately.

    Digital 1040s print captions first ('Last name', 'Your social security number') and the
    values ('JOHN', 'Q', 'DOE') a few lines later; scanned ones put 'JANE' right below.
    """
    for i, _ in _anchor_positions(lines, [anchor, anchor.replace("'", "’")]):
        for ln in lines[i + 1:i + 7]:
            s = ln.strip()
            if re.fullmatch(r"[A-Z][A-Z'-]{1,20}(?:\s+[A-Z])?", s) and s.lower() not in LABEL_WORDS:
                return s
    return ""


# =========================================================================== driver

def _reader(kind: str, cfg: Config) -> Callable[[list[str], dict], Value] | None:
    return {
        "ssn": read_ssn, "person_name": read_person_name, "company": read_company, "address": read_address,
        "date": read_date, "year": read_year, "last4": read_last4, "period_month": read_period_month,
        "heading": read_heading, "form_number": read_form_number, "id_name": read_id_name,
        "id_address": read_id_address, "id_dob": read_id_dob,
        "bank": lambda lines, spec: read_bank(lines, spec, cfg),
        "text": read_heading,
    }.get(kind)


def extract_document(doc: Document, cfg: Config) -> None:
    """Fill doc.fields, doc.field_confidence, doc.persons and doc.property_address."""
    if not doc.type_id:
        return
    dt: DocType = cfg.types[doc.type_id]
    values: dict[str, Value] = {}

    if dt.mode == "grouped" and any(s.get("kind", "").startswith("tax_") for s in dt.fields.values()):
        tax = extract_tax_return(doc.pages)
        for fname, spec in dt.fields.items():
            kind = spec["kind"]
            if kind == "group_year":
                y = doc.fields.get("group_year")
                values[fname] = (y, 0.9) if y else NONE
            elif kind.startswith("tax_"):
                key = kind.replace("tax_", "").replace("address", "home_address")
                values[fname] = tax.get(key, NONE)
    else:
        for fname, spec in dt.fields.items():
            kind = spec["kind"]
            best: Value = NONE
            for p in doc.pages:
                main, allx = _main_lines(p.text), _all_lines(p.text)
                if dt.assembly.get("second_reading_first") and SECOND_MARKER in p.text:
                    # Photo IDs: the deep-learning reader is far more reliable than Tesseract on
                    # security backgrounds, so its reading is consulted first (set per type in the config).
                    main = lines_of(p.text.split(SECOND_MARKER, 1)[1])
                if kind in ("w2_employee_name", "w2_employee_address"):
                    name_v, addr_v = read_w2_employee(main)
                    if not name_v[0]:
                        name_v, addr_v = read_w2_employee(allx)
                    v = name_v if kind == "w2_employee_name" else addr_v
                elif kind == "company" and dt.id == "w2":
                    v = read_w2_employer(main)
                    if not v[0] and allx != main:
                        v = read_w2_employer(allx)
                else:
                    reader = _reader(kind, cfg)
                    v = reader(main, spec) if reader else NONE
                    if not v[0] and reader and allx != main:
                        v = reader(allx, spec)
                        v = (v[0], v[1] * 0.7) if v[0] else v
                if v[0] is not None and v[1] > best[1]:
                    best = v
                    if v[1] >= 0.85:
                        break
            if best[0] is not None and doc.pages:
                q = min(p.ocr_confidence for p in doc.pages) / 100
                best = (best[0], round(best[1] * (0.6 + 0.4 * min(1.0, q / 0.8)), 3))
            values[fname] = best

    doc.fields.update({k: v[0] for k, v in values.items()})
    doc.field_confidence.update({k: v[1] for k, v in values.items()})

    for pspec in dt.persons:
        vals = {k: (doc.fields.get(pspec[k]) if pspec.get(k) else None) for k in ("name", "ssn", "dob", "address")}
        if not vals["name"] and not vals["ssn"]:
            continue
        doc.persons.append(PersonRecord(
            role=pspec.get("role", "person"), name=vals["name"] or "", ssn=vals["ssn"],
            dob=vals["dob"], address=vals["address"]))
    if dt.property_address:
        pa = doc.fields.get(dt.property_address)
        doc.property_address = pa if isinstance(pa, Address) else None

"""Step 6 - Build the file name ("label") from the type's template.

Template syntax (document_types.yaml):  "{employee_name|first} - INCOME - W2 - {employer} - {year}"
Filters:  first  - first name only        street - street part of an address (Title Case)
          title  - Title Case             upper  - UPPER CASE
          date:<style> - M-D-YY | MM-DD-YY | Month | Month YYYY
A field that could not be read becomes "[UNKNOWN ...]" and the document goes to review.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .normalize import MONTH_NAMES, Address, first_name, format_date, parse_date

_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)(?:\|([a-z0-9]+)(?::([A-Za-z\- ]+))?)?\}")
_SMALL = {"of", "and", "or", "in", "to", "for", "the", "a", "an", "at", "by", "on"}
_KEEP_UPPER = {"llc", "lp", "llp", "pa", "pc", "inc", "hoa", "usa", "ne", "nw", "se", "sw", "ii", "iii", "isd"}


def smart_title(s: str) -> str:
    words = s.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _KEEP_UPPER:
            out.append(w.upper())
        elif i > 0 and lw in _SMALL:
            out.append(lw)
        elif re.match(r"^\d", w):
            out.append(w.upper())
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(out)


def apply_filter(value: Any, flt: str | None, arg: str | None) -> str | None:
    if value is None or value == "":
        return None
    if flt == "first":
        return first_name(str(value)) or None
    if flt == "street":
        street = value.street if isinstance(value, Address) else str(value)
        return smart_title(street) if street else None
    if flt == "date":
        d = value if isinstance(value, date) else parse_date(str(value))
        if d:
            return format_date(d, arg or "MM-DD-YY")
        if arg and arg.startswith("Month"):  # value is already a month name, e.g. "April" or "Aug 2025"
            m = re.match(r"^([A-Za-z]{3,9})\b", str(value).strip())
            for full in MONTH_NAMES:
                if m and full.lower().startswith(m.group(1).lower()[:3]):
                    return full
        return None
    if flt == "title":
        return smart_title(str(value))
    if flt == "last4":
        digits = re.sub(r"\D", "", str(value))
        return digits[-4:] if len(digits) >= 4 else None
    if flt == "upper":
        return str(value).upper()
    if isinstance(value, Address):
        return value.display()
    if isinstance(value, date):
        return format_date(value, "MM-DD-YY")
    return re.sub(r"\s+", " ", str(value)).strip()


def render_label(template: str, fields: dict[str, Any]) -> tuple[str, bool, list[str]]:
    """Return (label, complete, missing_field_names)."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        name, flt, arg = m.group(1), m.group(2), m.group(3)
        val = apply_filter(fields.get(name), flt, arg)
        if not val:
            missing.append(name)
            return f"[UNKNOWN {name.replace('_', ' ').upper()}]"
        return val

    label = _PLACEHOLDER.sub(sub, template)
    return re.sub(r"\s+", " ", label).strip(), not missing, missing


def template_fields(template: str) -> list[tuple[str, str | None, str | None]]:
    return [(m.group(1), m.group(2), m.group(3)) for m in _PLACEHOLDER.finditer(template)]


def template_regex(template: str, lenient: bool | int = False) -> re.Pattern:
    """Regex that parses a label back into its fields (used by the Guide 1 self-test).

    lenient: 0/False = exact; 1/True = any spacing and capitals; 2 = also tolerate a missing dash.
    """
    level = int(lenient)
    parts = []
    pos = 0
    for m in _PLACEHOLDER.finditer(template):
        literal = template[pos:m.start()]
        parts.append(_literal(literal, level))
        name = m.group(1)
        if name == "year" or name.endswith("_year"):
            body = r"\d{4}"
        elif m.group(2) == "last4":
            body = r"\d+"
        else:
            body = ".+?"
        parts.append(f"(?P<{name}>{body})")
        pos = m.end()
    parts.append(_literal(template[pos:], level))
    flags = re.IGNORECASE if level else 0
    return re.compile("^" + "".join(parts) + "$", flags)


def _literal(text: str, level: int) -> str:
    if not level:
        return re.escape(text)
    out = []
    for chunk in re.split(r"(\s*-\s*)", text):
        if re.fullmatch(r"\s*-\s*", chunk or "x"):
            out.append(r"\s*-\s*" if level == 1 else r"(?:\s*-\s*|\s+)")
        elif chunk:
            out.append(r"\s*".join(re.escape(w) for w in chunk.split()))
            if chunk.endswith(" "):
                out.append(r"\s*")
    return "".join(out)

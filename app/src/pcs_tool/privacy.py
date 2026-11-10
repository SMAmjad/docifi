"""Privacy helpers: masking and redaction of personal values.

Rule of the project: a full Social Security number is never displayed,
logged or written anywhere. Only the last four digits are ever shown.
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\d")


def ssn_last4(value: str | None) -> str | None:
    """Return the last four digits of an SSN-like value, or None."""
    if not value:
        return None
    digits = "".join(_DIGITS.findall(value))
    return digits[-4:] if len(digits) >= 4 else None


def mask_ssn(value: str | None) -> str:
    """Mask an SSN as ***-**-1234. Unknown values become an empty string."""
    last4 = ssn_last4(value)
    return f"***-**-{last4}" if last4 else ""


def redact_name(value: str | None) -> str:
    """Keep initials only: 'Jane Q Doe' -> 'J. Q. D.'"""
    if not value:
        return ""
    parts = [p for p in re.split(r"\s+", value.strip()) if p]
    return " ".join(f"{p[0].upper()}." for p in parts if p[0].isalpha())


def redact_date(value: str | None) -> str:
    """Hide a date completely while keeping its presence visible."""
    return "**/**/****" if value else ""


def redact_address(value: str | None) -> str:
    """Keep only the state and the first ZIP digit: '..., TX 7****'."""
    if not value:
        return ""
    m = re.search(r"\b([A-Z]{2})\s+(\d)\d{4}", value.upper())
    return f"[redacted], {m.group(1)} {m.group(2)}****" if m else "[redacted]"


def redact_free_text(value: str | None) -> str:
    """Remove any SSN-like or long digit runs from arbitrary text."""
    if not value:
        return ""
    value = re.sub(r"\b\d{3}[- ]?\d{2}[- ]?(\d{4})\b", r"***-**-\1", value)
    return value

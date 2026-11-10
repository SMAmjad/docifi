"""Normalisation of the values we compare: text, names, dates, SSNs, addresses.

Documents print the same fact in many ways ("Dr" vs "Drive", "9/5/92" vs
"09/05/1992", "SMITH, JOHN A" vs "John Smith"). Every comparison in the tool
goes through these functions so that only *real* differences are reported.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

# --------------------------------------------------------------------------- text

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})


def clean_text(text: str) -> str:
    """Unicode-normalise OCR text and unify quotes and dashes."""
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    return text.replace("\x0c", "\n")


def search_text(text: str) -> str:
    """Lower-case, single-spaced version of text used for keyword search."""
    return re.sub(r"\s+", " ", clean_text(text).lower()).strip()


def lines_of(text: str) -> list[str]:
    """Non-empty, stripped lines."""
    return [ln.strip() for ln in clean_text(text).splitlines() if ln.strip()]


# --------------------------------------------------------------------------- SSN

_SSN_CANDIDATE = re.compile(r"(?<![\w-])([0-9OoIl|SX*xX][0-9OoIl|SX*xX \-]{8,12}[0-9])(?![\w-])")
_OCR_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5"})


@dataclass(frozen=True)
class SSN:
    """A Social Security number as found on a document.

    ``full`` is set only when all nine digits were readable. Documents often
    print a masked SSN (XXX-XX-1234); then only ``last4`` is known.
    """

    last4: str
    full: str | None = None

    @property
    def masked(self) -> str:
        return f"***-**-{self.last4}"


def parse_ssn(raw: str) -> SSN | None:
    """Parse one SSN-like token. Returns None for EINs, phone numbers etc."""
    raw = raw.strip()
    if re.fullmatch(r"\d{2}-\d{7}", raw):  # Employer ID number, not an SSN
        return None
    masked = re.fullmatch(r"[Xx*]{3}[- ]?[Xx*]{2}[- ]?(\d{4})", raw)
    if masked:
        return SSN(last4=masked.group(1))
    fixed = raw.translate(_OCR_DIGIT_FIX)
    digits = re.sub(r"[\s\-]", "", fixed)
    if not re.fullmatch(r"\d{9}", digits):
        return None
    if digits[:3] in ("000", "666") or digits[0] == "9" or digits[3:5] == "00" or digits[5:] == "0000":
        return None  # structurally invalid SSN
    return SSN(last4=digits[5:], full=digits)


_SSN_STRICT = re.compile(r"[\dOoIl|S]{3}[- ][\dOoIl|S]{2}[- ]\d{4}|[Xx*]{3}[- ]?[Xx*]{2}[- ]?\d{4}")


def find_ssns(text: str, strict: bool = False) -> list[SSN]:
    """All SSNs in a block of text, in order, without duplicates.

    strict=True accepts only the printed SSN layout (123-45-6789 or XXX-XX-6789), never a bare
    run of nine digits - used when searching a whole page rather than next to an 'SSN' caption.
    """
    found: list[SSN] = []
    for m in _SSN_CANDIDATE.finditer(clean_text(text)):
        token = m.group(1)
        if token.count("-") > 2 or "  " in token:
            continue
        if strict and not _SSN_STRICT.fullmatch(token):
            continue
        ssn = parse_ssn(token)
        if ssn and ssn not in found:
            found.append(ssn)
    return found


def ssn_compatible(a: SSN, b: SSN) -> bool:
    """True when two SSNs cannot be told apart (full match, or same last 4 when one is masked)."""
    if a.full and b.full:
        return a.full == b.full
    return a.last4 == b.last4


# --------------------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b")
_TEXT_DATE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_DAY_FIRST_TEXT = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")


def _year(y: str) -> int:
    v = int(y)
    if len(y) == 2:
        v += 2000 if v <= 40 else 1900
    return v


def parse_date(text: str) -> date | None:
    """Parse the first US-style date in text (MM/DD/YYYY, M-D-YY, 'Jan 5, 2026', '05 JAN 2026')."""
    if not text:
        return None
    for m in _NUMERIC_DATE.finditer(text):
        mm, dd, yy = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        try:
            return date(yy, mm, dd)
        except ValueError:
            continue
    for rx, order in ((_TEXT_DATE, "mdy"), (_DAY_FIRST_TEXT, "dmy")):
        for m in rx.finditer(text):
            g = m.groups()
            mon_s, day_s, yr_s = (g[0], g[1], g[2]) if order == "mdy" else (g[1], g[0], g[2])
            mon = _MONTHS.get(mon_s[:3].lower())
            if not mon:
                continue
            try:
                return date(int(yr_s), mon, int(day_s))
            except ValueError:
                continue
    return None


def format_date(d: date, style: str) -> str:
    """Render a date in a label style: 'M-D-YY', 'MM-DD-YY', 'Month', 'Month YYYY', 'MM/DD/YYYY'."""
    if style == "M-D-YY":
        return f"{d.month}-{d.day}-{d.year % 100:02d}"
    if style == "MM-DD-YY":
        return f"{d.month:02d}-{d.day:02d}-{d.year % 100:02d}"
    if style == "Month":
        return MONTH_NAMES[d.month - 1]
    if style == "Month YYYY":
        return f"{MONTH_NAMES[d.month - 1]} {d.year}"
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


# --------------------------------------------------------------------------- names

_NAME_NOISE = re.compile(r"[^A-Za-z' \-]")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def clean_person_name(raw: str) -> str:
    """Tidy a raw name: handles 'LAST, FIRST M', stray symbols, extra spaces; returns Title Case."""
    raw = clean_text(raw).strip()
    if "," in raw:
        last, _, rest = raw.partition(",")
        if rest.strip() and len(last.split()) <= 2:
            raw = f"{rest} {last}"
    raw = _NAME_NOISE.sub(" ", raw)
    words = [w for w in raw.split() if w]
    return " ".join("-".join(p.capitalize() for p in w.split("-")) for w in words)


def name_tokens(name: str) -> list[str]:
    """Lower-case tokens without suffixes or single-letter initials ('DOE, JANE' -> ['jane', 'doe'])."""
    toks = [t for t in re.split(r"[\s\-]+", clean_person_name(name).lower()) if t]
    return [t for t in toks if t not in _SUFFIXES and len(t) > 1]


def first_name(name: str) -> str:
    toks = clean_person_name(name).split()
    return toks[0] if toks else ""


def name_similarity(a: str, b: str) -> float:
    """0..100 similarity that tolerates middle names, initials, order and small OCR errors."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    return float(max(fuzz.token_set_ratio(" ".join(ta), " ".join(tb)),
                     fuzz.token_sort_ratio(" ".join(ta), " ".join(tb))))


def names_match(a: str, b: str, threshold: float = 85.0) -> bool:
    """Same person? First and last name must both agree (fuzzy); middle names are optional."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    first_ok = fuzz.ratio(ta[0], tb[0]) >= threshold
    last_ok = fuzz.ratio(ta[-1], tb[-1]) >= threshold or (
        # tolerate an extra middle/maiden name: last of one matches any of the other
        any(fuzz.ratio(ta[-1], t) >= threshold for t in tb) and any(fuzz.ratio(tb[-1], t) >= threshold for t in ta)
    )
    return first_ok and last_ok


# --------------------------------------------------------------------------- addresses

STREET_SUFFIX = {
    "street": "st", "str": "st", "st": "st", "drive": "dr", "dr": "dr", "drv": "dr",
    "road": "rd", "rd": "rd", "avenue": "ave", "av": "ave", "ave": "ave",
    "boulevard": "blvd", "blvd": "blvd", "lane": "ln", "ln": "ln", "court": "ct", "ct": "ct",
    "circle": "cir", "cir": "cir", "place": "pl", "pl": "pl", "parkway": "pkwy", "pkwy": "pkwy",
    "highway": "hwy", "hwy": "hwy", "trail": "trl", "trl": "trl", "terrace": "ter", "ter": "ter",
    "way": "way", "view": "vw", "vw": "vw", "cove": "cv", "cv": "cv", "point": "pt", "pt": "pt",
    "square": "sq", "sq": "sq", "crossing": "xing", "xing": "xing", "loop": "loop", "bend": "bnd",
    "hollow": "holw", "holw": "holw", "landing": "lndg", "lndg": "lndg", "meadows": "mdws", "mdws": "mdws",
    "county": "co", "co": "co", "route": "rte", "rte": "rte", "bypass": "byp", "byp": "byp",
}
DIRECTIONS = {"north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne",
              "northwest": "nw", "southeast": "se", "southwest": "sw"}
UNIT_WORDS = {"apartment": "unit", "apt": "unit", "unit": "unit", "suite": "unit", "ste": "unit", "#": "unit", "no": "unit"}

_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI",
}
_STATE_ZIP = re.compile(r"(?<![A-Z])([A-Z]{2})[\s,._]*(\d{5})(?:-\d{4})?\b")  # 'TX 77001' or squashed 'TX77001'
_STREET_LINE = re.compile(r"^\s*(?:\d{1,6}[A-Za-z]?\s+[A-Za-z0-9]|P\s*[.,]?\s*O\s*[.,:]?\s*BOX\s*\d+)", re.IGNORECASE)
_CITY_NOISE = {"code", "zip", "state", "office", "town", "city", "post", "campaign", "election", "presidential",
               "address", "no", "apt", "country", "name", "foreign"}


@dataclass(frozen=True)
class Address:
    street: str
    city: str = ""
    state: str = ""
    zip5: str = ""

    def display(self) -> str:
        tail = " ".join(x for x in (self.state, self.zip5) if x)
        parts = [self.street, self.city, tail]
        return ", ".join(p for p in parts if p)

    def key(self) -> str:
        """Canonical comparison key for the street part (abbreviations unified, unit kept)."""
        return normalize_street(self.street)


def normalize_street(street: str) -> str:
    s = clean_text(street).lower()
    s = s.replace("#", " # ")
    s = re.sub(r"[.,]", " ", s)
    out = []
    for tok in s.split():
        if tok in UNIT_WORDS:
            out.append("unit")
        elif tok in STREET_SUFFIX:
            out.append(STREET_SUFFIX[tok])
        elif tok in DIRECTIONS:
            out.append(DIRECTIONS[tok])
        else:
            out.append(tok)
    # "unit unit" artefacts from "apt #"
    return re.sub(r"\b(unit\s+)+", "unit ", " ".join(out)).strip()


def is_street_line(line: str) -> bool:
    return bool(_STREET_LINE.match(line)) and not re.fullmatch(r"[\d\s\-/.,]+", line.strip())


def parse_city_state_zip(line: str) -> tuple[str, str, str] | None:
    """'HOUSTON, TX 77001' -> ('Houston', 'TX', '77001'). City may be '' when not printed on the same line."""
    up = line.upper()
    for m in _STATE_ZIP.finditer(up):
        if m.group(1) not in US_STATES:
            continue
        before = re.sub(r"[^A-Za-z .'-]", " ", line[: m.start()]).split()
        city_words: list[str] = []
        for w in reversed(before):  # take words right before the state, stop at form captions
            w = w.strip(".'-")
            if not w:
                continue
            if w.lower() in _CITY_NOISE or len(city_words) == 3:
                break
            city_words.insert(0, w)
        return " ".join(city_words).title(), m.group(1), m.group(2)
    # Squashed photo text such as 'HOUSTONTXT7O02': state glued to the city, OCR letters in the ZIP.
    squashed = re.sub(r"[\s,._]", "", up)
    for m in reversed(list(re.finditer(r"([A-Z]{2})([0-9OTISB]{5})(?![0-9A-Z])", squashed))):
        zip5 = m.group(2).translate(str.maketrans("OTISB", "07158"))
        if m.group(1) in US_STATES and zip5.isdigit() and sum(c.isdigit() for c in m.group(2)) >= 3:
            city = re.sub(r"[^A-Z]", "", squashed[: m.start()])[-20:]
            return city.title(), m.group(1), zip5
    return None


def addresses_match(a: Address, b: Address, threshold: float = 90.0) -> bool:
    """Same street address? ZIPs must agree when both known; street compared after normalisation."""
    if a.zip5 and b.zip5 and a.zip5 != b.zip5:
        return False
    ka, kb = a.key(), b.key()
    if not ka or not kb:
        return False
    num_a, num_b = re.match(r"\d*", ka).group(0), re.match(r"\d*", kb).group(0)
    if num_a != num_b:
        return False
    if fuzz.token_sort_ratio(ka, kb) >= threshold or ka.startswith(kb) or kb.startswith(ka):
        return True
    # Photos of ID cards are sometimes read without spaces ('1700RIVERENDDR#12'):
    # compare with all spaces removed and street-type words abbreviated.
    sq_a, sq_b = _squash(ka), _squash(kb)
    return fuzz.ratio(sq_a, sq_b) >= threshold or (len(sq_a) > 8 and (sq_a.startswith(sq_b) or sq_b.startswith(sq_a)))


def _squash(key: str) -> str:
    s = key.replace(" ", "")
    for long, short in (("drive", "dr"), ("street", "st"), ("road", "rd"), ("lane", "ln"), ("court", "ct"),
                        ("avenue", "ave"), ("apt", "unit"), ("#", "unit")):
        s = s.replace(long, short)
    return s


# --------------------------------------------------------------------------- years

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def find_years(text: str) -> list[int]:
    return [int(y) for y in _YEAR.findall(text)]


def safe_filename(name: str, max_len: int = 150) -> str:
    """Make a label safe as a Windows file name (no / \\ : * ? " < > |, no trailing dots)."""
    name = re.sub(r'[\\/:*?"<>|]+', "-", clean_text(name))
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip(" .-") or "Document"

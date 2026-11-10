"""Step 3 - Recognise what each page is, using only the rules in document_types.yaml.

Each type lists phrases with weights. A page's score for a type is the sum of the
weights of the phrases found on it (phrases near the top of the page count more,
because titles are at the top). Small OCR spelling errors are tolerated with a
fuzzy match. The best-scoring type wins if it passes the type's minimum score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..config import Config, DocType
from .normalize import clean_text, search_text

TOP_LINES = 12
TOP_WEIGHT = 1.5
FUZZY_MIN_LEN = 12
FUZZY_SCORE = 90
FUZZY_FACTOR = 0.8


@dataclass
class Classification:
    type_id: str | None
    score: float
    confidence: float
    runner_up: str | None
    continuation_of: str | None = None


def _contains(phrase: str, text: str) -> float:
    """1.0 exact hit, FUZZY_FACTOR for a near miss (OCR typo), 0 otherwise."""
    if phrase in text:
        return 1.0
    if len(phrase) >= FUZZY_MIN_LEN and fuzz.partial_ratio(phrase, text) >= FUZZY_SCORE:
        return FUZZY_FACTOR
    return 0.0


def _score(dt: DocType, full: str, top: str) -> float | None:
    rules = dt.classify
    for ex in rules.get("exclude") or []:
        if ex in full:
            return None
    required = rules.get("required_any") or []
    if required and not any(_contains(r, full) for r in required):
        return None
    score = 0.0
    for phrase, weight in (rules.get("keywords") or {}).items():
        hit = _contains(phrase, top)
        if hit:
            score += weight * TOP_WEIGHT * hit
            continue
        hit = _contains(phrase, full)
        score += weight * hit
    return score


def classify_page(text: str, cfg: Config, ocr_quality: float = 100.0) -> Classification:
    full = search_text(text)
    if len(re.findall(r"[a-z0-9]", full)) < 15:
        return Classification(None, 0.0, 0.0, None)
    top = search_text("\n".join(clean_text(text).splitlines()[:TOP_LINES]))

    # Back pages and envelopes ("Instructions for Employee") mention other forms (e.g. "Form 1040");
    # recognise them first so they stay with the document they belong to.
    for dt in cfg.types.values():
        for phrase in dt.assembly.get("continuation") or []:
            if phrase in top:
                return Classification(None, 0.0, 0.7, None, continuation_of=dt.id)

    scored: list[tuple[float, int, str]] = []
    for dt in cfg.types.values():
        s = _score(dt, full, top)
        if s is not None and s >= float(dt.classify.get("min_score", 1)):
            scored.append((s, int(dt.classify.get("priority", 0)), dt.id))

    if not scored:
        for dt in cfg.types.values():  # a "back page" or envelope belonging to the previous document
            for phrase in dt.assembly.get("continuation") or []:
                if phrase in full:
                    return Classification(None, 0.0, 0.6, None, continuation_of=dt.id)
        return Classification(None, 0.0, 0.0, None)

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_s, _, best_id = scored[0]
    runner = scored[1] if len(scored) > 1 else None
    min_s = float(cfg.types[best_id].classify.get("min_score", 1))
    conf = min(1.0, 0.6 + 0.4 * (best_s - min_s) / max(min_s * 1.5, 1.0))
    if runner and runner[0] >= 0.8 * best_s:
        conf *= 0.85  # two types look alike: be less sure
    if ocr_quality < 60:
        conf *= 0.9
    return Classification(best_id, round(best_s, 1), round(conf, 3), runner[2] if runner else None)


def is_first_page(dt: DocType, text: str) -> bool:
    full = search_text(text)
    if re.search(r"\bpage\s*1\s*of\s*\d+", full):
        return True
    return any(_contains(p, full) for p in dt.assembly.get("first_page") or [])


def page_number_hint(text: str) -> int | None:
    m = re.search(r"\bpage\s*(\d{1,2})\s*(?:of\s*\d{1,2})?\b", search_text(text))
    return int(m.group(1)) if m else None


def detect_year(dt: DocType, text: str) -> int | None:
    """Tax/statement year using the type's year_patterns (most common hit wins)."""
    full = search_text(text)
    hits: list[int] = []
    for pat in dt.assembly.get("year_patterns") or []:
        hits += [int(y) for y in re.findall(pat, full)]
    hits = [y for y in hits if 2000 <= y <= 2045]
    if not hits:
        return None
    return max(set(hits), key=hits.count)


def order_index(dt: DocType, text: str) -> int:
    """Position of the page in the type's canonical page order (unknown pages go last)."""
    full = search_text(text)
    top = search_text("\n".join(clean_text(text).splitlines()[:TOP_LINES]))
    rules = dt.assembly.get("page_order") or []
    for region in (top, full):
        best: tuple[int, int] | None = None  # (position of match in text, rule index)
        for i, rule in enumerate(rules):
            if any(n in region for n in rule.get("not", [])) or not all(a in full for a in rule.get("also", [])):
                continue
            positions = [region.find(m.strip()) for m in rule.get("match", []) if m.strip() in region]
            if positions and (best is None or min(positions) < best[0]):
                best = (min(positions), i)
        if best is not None:
            return best[1]  # the title that appears first on the page wins
    return len(rules)

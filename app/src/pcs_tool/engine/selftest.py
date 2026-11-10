"""Self-test against Guide 1.

Guide 1 is a rulebook with 78 example file names. The documents those names
belong to are not part of the data set, so the self-test measures what *can*
be measured honestly:

Rule test - for each of the 78 example names:
  1. Is the Guide's document type mapped to one of our configured types?
  2. Does the example name follow our configured label template?
  3. Read the values back out of the example name and rebuild it with the tool's
     own label code. Is the result identical (or identical except for capitals /
     spacing)? If not, which part differs and why?

The output names document numbers and field names only - never personal values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from ..config import Config
from .label import render_label, template_regex


@dataclass
class RuleRow:
    doc: str
    guide_type: str
    type_id: str | None
    follows_template: str      # "exactly" | "loosely" | "no"
    outcome: str               # "identical" | "same content" | "differs" | "not mapped"
    reasons: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    s = re.sub(r"\s*-\s*", " - ", s.replace("–", "-"))
    return re.sub(r"\s+", " ", s).strip().casefold()


def load_guide(path: Path) -> list[dict[str, str]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise ValueError("The guide file is empty")
    header =[str(h or "").strip().lower() for h in rows[0]]

    def col(*names: str) -> int:
        for n in names:
            for i, h in enumerate(header):
                if n in h:
                    return i
        raise ValueError(f"Guide file has no column like {names[0]!r}")

    c_doc, c_type = col("document name"), col("type")
    c_label, c_answer = col("label"), col("actual proposed", "proposed filename", "filename")
    out = []
    for r in rows[1:]:
        if not r or not r[c_doc]:
            continue
        out.append({"doc": str(r[c_doc]).strip(), "type": str(r[c_type] or "").strip(),
                    "template": str(r[c_label] or "").strip(), "answer": str(r[c_answer] or "").strip()})
    return out


def _map_type(guide_type: str, cfg: Config) -> str | None:
    g = re.sub(r"\s+", " ", guide_type).strip().lower()
    for t in cfg.types.values():
        for name in t.guide_type_names:
            if re.sub(r"\s+", " ", name).strip().lower() == g:
                return t.id
    for t in cfg.types.values():  # looser: prefix match
        for name in t.guide_type_names:
            if g.startswith(re.sub(r"\s+", " ", name).strip().lower()[:20]):
                return t.id
    return None


def rule_test(guide_path: Path, cfg: Config) -> dict:
    rows: list[RuleRow] = []
    for g in load_guide(guide_path):
        tid = _map_type(g["type"], cfg)
        if not tid:
            rows.append(RuleRow(g["doc"], g["type"], None, "no", "not mapped", ["Guide type not in configuration."]))
            continue
        template = cfg.types[tid].label
        answer = re.sub(r"\s+", " ", g["answer"]).strip()
        m = template_regex(template).match(answer)
        follows = "exactly" if m else "no"
        missing_dash = False
        if not m:
            m = template_regex(template, lenient=1).match(answer)
            follows = "loosely" if m else "no"
        if not m:
            m = template_regex(template, lenient=2).match(answer)
            follows = "loosely" if m else "no"
            missing_dash = bool(m)
        if not m:
            rows.append(RuleRow(g["doc"], g["type"], tid, "no", "differs",
                                [_explain_no_match(template, answer)]))
            continue
        parsed = {k: v.strip() for k, v in m.groupdict().items()}
        rebuilt, complete, missing = render_label(template, parsed)
        reasons = []
        if missing_dash:
            reasons.append("A separating dash is missing in the Guide name.")
        elif follows == "loosely":
            reasons.append("Guide name differs from its own format in spacing or capital letters only.")
        if missing:
            reasons.append("Could not interpret: " + ", ".join(missing) + ".")
        if rebuilt == answer:
            outcome = "identical"
        elif _norm(rebuilt) == _norm(answer):
            outcome = "same content"
            reasons.append("Only capital letters or spacing differ.")
        else:
            outcome = "differs"
            extra = _diff_reasons(parsed, template, rebuilt, answer)
            if missing_dash and extra == ["Rebuilt name differs from the Guide name."]:
                extra = []
            reasons += extra
        rows.append(RuleRow(g["doc"], g["type"], tid, follows, outcome, reasons))
    total = len(rows)
    summary = {
        "total": total,
        "mapped": sum(1 for r in rows if r.type_id),
        "template_exact": sum(1 for r in rows if r.follows_template == "exactly"),
        "identical": sum(1 for r in rows if r.outcome == "identical"),
        "same_content": sum(1 for r in rows if r.outcome == "same content"),
        "differs": sum(1 for r in rows if r.outcome in ("differs", "not mapped")),
    }
    return {"summary": summary, "rows": rows}


def _explain_no_match(template: str, answer: str) -> str:
    fixed = [p.strip() for p in template.split(" - ") if "{" not in p and p.strip()]
    missing = [p for p in fixed if p.lower() not in answer.lower()]
    if missing:
        return f"Guide name is missing the fixed part(s): {', '.join(missing)}."
    return "Guide name has a different structure from the format."


def _diff_reasons(parsed: dict, template: str, rebuilt: str, answer: str) -> list[str]:
    out = []
    date_fields = re.findall(r"\{([a-z0-9_]+)\|date", template)
    if date_fields:
        for k, v in parsed.items():
            if k not in date_fields:
                continue
            if re.search(r"\b(to|through)\b", v, re.I):
                out.append(f"'{k}' is a date range in the Guide; the rule asks for a single date.")
            elif re.search(r"\b20\d\d\b", v) and "Month" in template:
                out.append(f"'{k}' includes the year in the Guide; the format asks for the month only.")
            elif re.search(r"\d", v):
                out.append(f"'{k}' is written in a different date style in the Guide.")
    if "|first}" in template:
        for k, v in parsed.items():
            if f"{{{k}|first}}" in template and len(v.split()) > 1:
                out.append(f"'{k}' has more than a first name in the Guide; the rule asks for first name only.")
    if "{account_last4" in template and len(re.sub(r"\D", "", parsed.get("account_last4", ""))) != 4:
        out.append("Account number in the Guide is not 4 digits; the rule asks for the last 4.")
    if not out:
        out.append("Rebuilt name differs from the Guide name.")
    return out


def print_rule_test(results: dict) -> str:
    s = results["summary"]
    lines = [
        "Guide 1 rule test",
        f"  Example names in Guide 1 ............ {s['total']}",
        f"  Document type recognised ............ {s['mapped']} of {s['total']}",
        f"  Name follows the format exactly ..... {s['template_exact']} of {s['total']}",
        f"  Rebuilt identically by the tool ..... {s['identical']} of {s['total']}",
        f"  Same content (case/spacing only) .... {s['same_content']} of {s['total']}",
        f"  Differ ............................... {s['differs']} of {s['total']}",
    ]
    diff = [r for r in results["rows"] if r.outcome in ("differs", "not mapped")]
    if diff:
        lines.append("  Differences (document number - reason):")
        for r in diff:
            lines.append(f"    {r.doc:<9} {' '.join(r.reasons)}")
    return "\n".join(lines)

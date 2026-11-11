"""Step 8 - The Excel check sheet.

Designed for a non-technical reader first, an auditor second.

Tabs (left to right):
  Document Check  MAIN VIEW. One row per document (and person): Name / DOB / SSN / Address
                  marked Match / Differs / Not readable / Not on this document, an overall
                  Result (Correct / Issue / Check manually) and what to review.
  People          One row per person: reference values and overall status per field.
  Review Queue    Work list for staff, with reviewer / date / outcome columns.
  Duplicates      Possible duplicates (never deleted automatically).
  Files           Every input file and what happened to it.
  Pages           Page-by-page trace (how each page was read and recognised).
  Self-Test       Guide 1 rule test (when a Guide file is supplied).
  About           How to read this report, method, privacy and limitations.

Privacy: SSNs are always masked (***-**-1234). With redact=True names, dates of birth,
addresses and file names are masked too, for demos and documentation.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from .. import APP_NAME, __version__
from ..config import Config
from ..models import Check, Document, RunResult, Status
from ..privacy import mask_ssn, redact_address, redact_name
from .normalize import SSN, Address
from .verify import Person

# --------------------------------------------------------------------------- style
NAVY, TEAL, AMBER_TAB, GREY_TAB = "1F3864", "2E75B6", "C55A11", "7F7F7F"
FONT = "Calibri"
F_BODY = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_TITLE = Font(name=FONT, size=18, bold=True, color=NAVY)
F_SUB = Font(name=FONT, size=10, italic=True, color="595959")
F_LINK = Font(name=FONT, size=10, color="0563C1", underline="single")
FILL_HEAD = PatternFill("solid", fgColor=NAVY)
FILL = {
    Status.CORRECT: PatternFill("solid", fgColor="C6EFCE"),
    Status.ISSUE: PatternFill("solid", fgColor="FFC7CE"),
    Status.CHECK: PatternFill("solid", fgColor="FFEB9C"),
    Status.NOT_APPLICABLE: PatternFill("solid", fgColor="F2F2F2"),
}
FONT_STATUS = {
    Status.CORRECT: Font(name=FONT, size=10, bold=True, color="006100"),
    Status.ISSUE: Font(name=FONT, size=10, bold=True, color="9C0006"),
    Status.CHECK: Font(name=FONT, size=10, bold=True, color="9C5700"),
    Status.NOT_APPLICABLE: Font(name=FONT, size=10, color="808080"),
}
SYMBOL = {Status.CORRECT: "✔ Match", Status.ISSUE: "✘ Differs", Status.CHECK: "? Not readable",
          Status.NOT_APPLICABLE: "— n/a"}
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="top", wrap_text=True)
FOOTER_LEFT = "CONFIDENTIAL - contains personal financial data. Delete securely when no longer needed."


# --------------------------------------------------------------------------- helpers

class _Ctx:
    """Everything needed to turn IDs into readable names, with or without redaction."""

    def __init__(self, result: RunResult, sheet_dir: Path, redact: bool, people: list[Person]):
        self.redact = redact
        self.sheet_dir = sheet_dir
        self.docs = {d.doc_id: d for d in result.documents}
        self.files = {f.file_id: f for f in result.files}
        self.people = {p.person_id: p for p in people}

    # personal values
    def name(self, v: str | None) -> str:
        return redact_name(v) if self.redact else (v or "")

    def ssn(self, v: SSN | None) -> str:
        return mask_ssn(v.last4) if v else ""

    def addr(self, v: Address | None) -> str:
        if not v:
            return ""
        return redact_address(v.display()) if self.redact else v.display()

    def file(self, file_id: str) -> str:
        f = self.files.get(file_id)
        return file_id if (self.redact or not f) else f.relative

    def doc_title(self, d: Document) -> str:
        """The name the document was saved under (so it can be found in the folder)."""
        if self.redact:
            return f"{d.doc_id} ({d.type_name})"
        if d.output_path:
            return d.output_path.stem
        return d.label if d.type_id else f"Unrecognised page ({d.doc_id})"

    def person_name(self, person_id: str | None) -> str:
        p = self.people.get(person_id or "")
        return self.name(p.display_name) if p else ""

    def resolve(self, text: str) -> str:
        """Replace D001 / F001-p3 references with readable names."""
        def doc_sub(m: re.Match) -> str:
            d = self.docs.get(m.group(0))
            return f"'{self.doc_title(d)}'" if d else m.group(0)

        def page_sub(m: re.Match) -> str:
            return f"page {m.group(2)} of '{self.file(m.group(1))}'"

        text = re.sub(r"\b(F\d{3})-p(\d+)\b", page_sub, text)
        return re.sub(r"\bD\d{3}\b", doc_sub, text)

    def sources(self, d: Document) -> str:
        parts = []
        for chunk in d.page_refs.split("; "):
            if " p" in chunk:
                fid, rng = chunk.split(" p", 1)
                parts.append(f"{self.file(fid)} (page {rng})" if "," not in rng and "-" not in rng
                             else f"{self.file(fid)} (pages {rng})")
        return "\n".join(parts)

    def link(self, d: Document) -> str | None:
        if self.redact or not d.output_path or not d.output_path.exists():
            return None
        return os.path.relpath(d.output_path, self.sheet_dir).replace("\\", "/")


def _estimate_height(values: Iterable[Any], widths: list[float]) -> float:
    """Row height for wrapped text (Excel does not auto-fit rows written by a program)."""
    lines = 1
    for v, w in zip(values, widths):
        if v is None or isinstance(v, (int, float, date)):
            continue
        per_line = max(4.0, w * 1.15)  # Calibri 10 fits ~1.15 characters per width unit
        n = sum(max(1, math.ceil(len(part) / per_line)) for part in str(v).split("\n"))
        lines = max(lines, n)
    return min(409, 13.5 * lines + 3)


def _table(ws: Worksheet, top: int, headers: list[str], widths: list[float], rows: list[list[Any]],
           center_cols: Iterable[int] = (), freeze_col: int = 2) -> int:
    """Write a formatted table; returns the last row used."""
    for c, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=top, column=c, value=h)
        cell.font, cell.fill, cell.border = F_HEAD, FILL_HEAD, BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[top].height = 32
    centers = set(center_cols)
    for r, values in enumerate(rows, start=top + 1):
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font, cell.border = F_BODY, BORDER
            cell.alignment = CENTER if c in centers else WRAP
        ws.row_dimensions[r].height = _estimate_height(values, widths)
    last = top + len(rows)
    ws.freeze_panes = ws.cell(row=top + 1, column=freeze_col)
    if rows:
        ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{last}"
    ws.print_title_rows = f"{top}:{top}"
    return last


def _paint_status(cell, status: Status, text: str | None = None) -> None:
    cell.value = text if text is not None else status.value
    cell.fill = FILL[status]
    cell.font = FONT_STATUS[status]


def _page_setup(ws: Worksheet, landscape: bool = True) -> None:
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.oddFooter.left.text = FOOTER_LEFT
    ws.oddFooter.left.size = 8
    ws.oddFooter.center.text = "&A"
    ws.oddFooter.center.size = 8
    ws.oddFooter.right.text = "Page &P of &N"
    ws.oddFooter.right.size = 8
    ws.sheet_view.zoomScale = 100


def _title(ws: Worksheet, title: str, subtitle: str) -> None:
    ws["A1"] = title
    ws["A1"].font = F_TITLE
    ws.row_dimensions[1].height = 28
    ws["A2"] = subtitle
    ws["A2"].font = F_SUB


def _field_checks(d: Document, person_id: str | None) -> dict[str, Check]:
    return {c.field: c for c in d.checks if c.person == (person_id or "") or (person_id and c.person == person_id)}


def _role_text(ctx: _Ctx, d: Document, person_id: str | None) -> str:
    """How this person appears on the document, and who else is on it.

    'Spouse - joint with Shaizad K Momin', 'Primary taxpayer - joint with Anisha S Momin', 'Employee', 'Holder'.
    """
    if not person_id:
        return ""
    rec = next((r for r in d.persons if r.person_id == person_id), None)
    if rec is None:
        return ""
    role = rec.role[:1].upper() + rec.role[1:]
    others = [ctx.person_name(r.person_id) for r in d.persons if r.person_id and r.person_id != person_id]
    others = [o for o in dict.fromkeys(others) if o]
    if not others:
        return role
    link = "joint with" if d.type_id == "personal_tax_return" else "with"
    return f"{role} - {link} {', '.join(others)}"


def _row_result(d: Document, checks: dict[str, Check]) -> Status:
    st = [c.status for c in checks.values()] + [c.status for c in d.checks if c.field == "Property address"]
    if Status.ISSUE in st:
        return Status.ISSUE
    if d.review_reasons or not d.type_id or Status.CHECK in st:
        return Status.CHECK
    return Status.CORRECT


def _row_reasons(ctx: _Ctx, d: Document, checks: dict[str, Check]) -> str:
    items: list[str] = []  # every reason text already starts with the field it is about
    for f in ("Name", "DOB", "SSN", "Address"):
        c = checks.get(f)
        if c and c.status in (Status.ISSUE, Status.CHECK) and c.reason:
            items.append(ctx.resolve(c.reason))
    for c in d.checks:
        if c.field == "Property address" and c.status == Status.ISSUE:
            items.append(ctx.resolve(c.reason))
    items += [ctx.resolve(r) for r in d.review_reasons]
    return "\n".join(f"• {i}" for i in dict.fromkeys(items))


def _suggest(d: Document, row_result: Status, reasons: str) -> str:
    t = reasons.lower()
    if not d.type_id:
        return "Open the page, decide what it is, file it manually."
    if "duplicate" in t or "identical" in t:
        return "Compare both copies; keep one."
    if row_result == Status.ISSUE:
        return "Compare the values with the referenced document; correct the file or ask the borrower."
    if "not readable" in t or "poor scan" in t or "incomplete" in t:
        return "Open the document, confirm the value by eye; ask for a clearer copy if needed."
    return "Open the document and confirm the type and file name."


# --------------------------------------------------------------------------- shared view model

def document_rows(result: RunResult, people: list[Person], cfg: Config, redact: bool = False) -> list[dict]:
    """Plain rows (strings only) for any front-end: the same content as the 'Document Check' tab.

    Each row: doc_id, title, type, person, name/dob/ssn/address (status values), result, reasons, path.
    """
    sheet_dir = result.sheet_path.parent if result.sheet_path else result.run_dir
    ctx = _Ctx(result, sheet_dir, redact, people)
    order = {Status.ISSUE: 0, Status.CHECK: 1, Status.CORRECT: 2}
    out = []
    for m in sorted(_document_rows(ctx, result.documents, cfg),
                    key=lambda m: (order[m["result"]], m["doc"].doc_id)):
        d = m["doc"]
        st = {f: (m["checks"][f].status if f in m["checks"] else Status.NOT_APPLICABLE)
              for f in ("Name", "DOB", "SSN", "Address")}
        out.append({
            "doc_id": d.doc_id, "folder": d.folder, "title": ctx.doc_title(d), "type": d.type_name,
            "person": ctx.person_name(m["pid"]) or "Not readable",
            "role": _role_text(ctx, d, m["pid"]),
            "name": st["Name"], "dob": st["DOB"], "ssn": st["SSN"], "address": st["Address"],
            "result": m["result"], "reasons": m["reasons"] or "",
            "path": None if redact else d.output_path,
        })
    return out


# --------------------------------------------------------------------------- main writer

def write_check_sheet(result: RunResult, people: list[Person], cfg: Config, path: Path,
                      redact: bool = False, guide_results: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(result, path.parent, redact, people)
    wb = Workbook()
    wb.properties.title = "Loan Document Check Sheet"
    wb.properties.creator = f"{APP_NAME} {__version__}"
    wb.properties.subject = "Name / DOB / SSN / Address verification"
    run_id = result.run_dir.name
    stamp = datetime.now().strftime("%B %d, %Y %I:%M %p")
    subtitle = f"Run {run_id}  |  Created {stamp}  |  {APP_NAME} {__version__}" + \
               ("  |  PERSONAL VALUES REDACTED" if redact else "")

    rows_meta = _document_rows(ctx, result.documents, cfg)
    _sheet_document_check(wb.active, ctx, result, rows_meta, subtitle)
    _sheet_people(wb.create_sheet("People"), ctx, people, subtitle)
    _sheet_review_queue(wb.create_sheet("Review Queue"), ctx, rows_meta, subtitle)
    _sheet_duplicates(wb.create_sheet("Duplicates"), ctx, result, subtitle)
    _sheet_files(wb.create_sheet("Files"), ctx, result, subtitle)
    _sheet_pages(wb.create_sheet("Pages"), ctx, result, cfg, subtitle)
    if guide_results:
        _sheet_selftest(wb.create_sheet("Self-Test"), guide_results, subtitle)
    _sheet_about(wb.create_sheet("About"), result, cfg, redact, subtitle)
    wb.active = 0
    wb.save(path)
    return path


def _unreadable_checks(d: Document, cfg: Config) -> dict[str, Check]:
    """A recognised document whose person could not be read: every expected field is 'not readable'."""
    if not d.type_id:
        return {}
    v = cfg.types[d.type_id].verify
    out = {}
    if v.get("name"):
        out["Name"] = Check("Name", Status.CHECK, "Name not readable on this scan.")
    if v.get("dob") in (True, "required"):
        out["DOB"] = Check("DOB", Status.CHECK, "Date of birth not readable on this scan.")
    if v.get("ssn") in (True, "required"):
        out["SSN"] = Check("SSN", Status.CHECK, "SSN not readable on this scan.")
    if v.get("address") == "residence":
        out["Address"] = Check("Address", Status.CHECK, "Home address not readable on this scan.")
    return out


def _document_rows(ctx: _Ctx, docs: list[Document], cfg: Config) -> list[dict]:
    """One entry per (document, person) - joint tax returns produce one row per spouse."""
    out = []
    for d in docs:
        persons = [r.person_id for r in d.persons if r.person_id] or [None]
        for pid in persons:
            checks = _field_checks(d, pid) if pid else _unreadable_checks(d, cfg)
            res = _row_result(d, checks)
            reasons = _row_reasons(ctx, d, checks)
            out.append({"doc": d, "pid": pid, "checks": checks, "result": res, "reasons": reasons})
    return out


def _sheet_document_check(ws: Worksheet, ctx: _Ctx, result: RunResult, rows_meta: list[dict], subtitle: str) -> None:
    ws.title = "Document Check"
    ws.sheet_properties.tabColor = NAVY
    _title(ws, "Loan Document Check - Name, Date of Birth, SSN, Address", subtitle)

    docs = result.documents
    counts = Counter(d.result for d in docs)
    tiles = [
        (2, f"{len(docs)} documents checked", PatternFill("solid", fgColor="D9E1F2"), Font(name=FONT, size=12, bold=True, color=NAVY)),
        (3, f"{counts[Status.CORRECT]} Correct", FILL[Status.CORRECT], Font(name=FONT, size=12, bold=True, color="006100")),
        (4, f"{counts[Status.ISSUE]} Issue", FILL[Status.ISSUE], Font(name=FONT, size=12, bold=True, color="9C0006")),
        (5, f"{counts[Status.CHECK]} Check manually", FILL[Status.CHECK], Font(name=FONT, size=12, bold=True, color="9C5700")),
        (9, f"{len(result.persons)} people", PatternFill("solid", fgColor="D9E1F2"), Font(name=FONT, size=12, bold=True, color=NAVY)),
    ]
    for col, text, fill, font in tiles:
        c = ws.cell(row=4, column=col, value=text)
        c.fill, c.font, c.border = fill, font, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=4, start_column=5, end_row=4, end_column=8)
    ws.row_dimensions[4].height = 26
    legend = ("How to read:  ✔ Match = same as the person's other documents   ✘ Differs = documents disagree   "
              "? Not readable = the tool could not read it, check by eye   — n/a = not shown on this type of document.   "
              "Result:  Correct = nothing to do   Issue = documents disagree   Check manually = a person must look.")
    ws["A6"] = legend
    ws["A6"].font = F_SUB
    ws["A6"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A6:M6")
    ws.row_dimensions[6].height = 30

    headers = ["#", "Document (new file name - click to open)", "Document type", "Person", "Role on document",
               "Name", "Date of birth", "SSN", "Home address", "Result", "What to review", "Original file", "Confidence"]
    widths = [5, 50, 24, 20, 26, 12, 12, 12, 12, 15, 62, 34, 11]
    order = {Status.ISSUE: 0, Status.CHECK: 1, Status.CORRECT: 2}
    rows_meta = sorted(rows_meta, key=lambda m: (order[m["result"]], ctx.person_name(m["pid"]) or "~", m["doc"].doc_id))
    rows = []
    for i, m in enumerate(rows_meta, start=1):
        d = m["doc"]
        rows.append([i, ctx.doc_title(d), d.type_name, ctx.person_name(m["pid"]) or "Not readable",
                     _role_text(ctx, d, m["pid"]) or "-",
                     None, None, None, None, m["result"].value, m["reasons"] or "-",
                     ctx.sources(d), d.confidence if d.type_id else "-"])
    top = 8
    _table(ws, top, headers, widths, rows, center_cols=(1, 6, 7, 8, 9, 10, 13), freeze_col=3)
    for i, m in enumerate(rows_meta, start=top + 1):
        d = m["doc"]
        for col, f in zip((6, 7, 8, 9), ("Name", "DOB", "SSN", "Address")):
            c = m["checks"].get(f)
            st = c.status if c else Status.NOT_APPLICABLE
            _paint_status(ws.cell(row=i, column=col), st, SYMBOL[st])
        _paint_status(ws.cell(row=i, column=10), m["result"])
        ws.cell(row=i, column=13).number_format = "0%"
        link = ctx.link(d)
        if link:
            cell = ws.cell(row=i, column=2)
            cell.hyperlink = link
            cell.font = F_LINK
    _page_setup(ws)


def _sheet_people(ws: Worksheet, ctx: _Ctx, people: list[Person], subtitle: str) -> None:
    ws.sheet_properties.tabColor = TEAL
    _title(ws, "People - reference values and overall result", subtitle)
    ws["A3"] = ("Reference value = the value taken from the most trustworthy document for that person "
                "(SSN card > tax return > W-2 for SSN; ID > tax return for date of birth; latest tax return for home address).")
    ws["A3"].font = F_SUB
    headers = ["Person", "Name", "Date of birth", "SSN", "Home address", "Name", "Date of birth", "SSN", "Address",
               "Overall", "Documents", "Findings", "Reference values taken from"]
    widths = [8, 24, 13, 13, 34, 12, 12, 12, 12, 15, 10, 52, 46]
    rows, statuses = [], []
    for p in people:
        per_field: dict[str, list[tuple[Status, Document]]] = defaultdict(list)
        doc_ids = []
        for d, _ in p.records:
            doc_ids.append(d.doc_id)
            for c in d.checks:
                if c.person == p.person_id:
                    per_field[c.field].append((c.status, d))
        agg = {}
        for f in ("Name", "DOB", "SSN", "Address"):
            sts = [s for s, _ in per_field.get(f, [])]
            agg[f] = Status.ISSUE if Status.ISSUE in sts else Status.CHECK if Status.CHECK in sts else \
                Status.CORRECT if Status.CORRECT in sts else Status.NOT_APPLICABLE
        overall = Status.ISSUE if Status.ISSUE in agg.values() else Status.CHECK if Status.CHECK in agg.values() \
            else Status.CORRECT
        findings = []
        for f in ("Name", "DOB", "SSN", "Address"):
            lbl = {"DOB": "Date of birth"}.get(f, f)
            n_issue = sum(1 for s, _ in per_field.get(f, []) if s == Status.ISSUE)
            n_check = sum(1 for s, _ in per_field.get(f, []) if s == Status.CHECK)
            if n_issue:
                findings.append(f"• {lbl} differs on {n_issue} document{'s' if n_issue > 1 else ''}")
            if n_check:
                findings.append(f"• {lbl} not readable on {n_check} document{'s' if n_check > 1 else ''}")
        refs = []
        for f, lbl in (("ssn", "SSN"), ("dob", "Date of birth"), ("address", "Home address")):
            did = p.reference_doc.get(f)
            if did:
                refs.append(f"{lbl}: {ctx.resolve(did)}")
        dob = p.reference.get("dob")
        rows.append([p.person_id, ctx.name(p.reference.get("name")),
                     ("**/**/****" if ctx.redact else dob) if dob else "",
                     ctx.ssn(p.reference.get("ssn")), ctx.addr(p.reference.get("address")),
                     None, None, None, None, None, len(set(doc_ids)),
                     "\n".join(findings) or "No differences found", "\n".join(refs)])
        statuses.append((agg, overall))
    top = 5
    _table(ws, top, headers, widths, rows, center_cols=(1, 3, 4, 6, 7, 8, 9, 10, 11), freeze_col=3)
    for i, (agg, overall) in enumerate(statuses, start=top + 1):
        for col, f in zip((6, 7, 8, 9), ("Name", "DOB", "SSN", "Address")):
            _paint_status(ws.cell(row=i, column=col), agg[f], SYMBOL[agg[f]])
        _paint_status(ws.cell(row=i, column=10), overall)
        if not ctx.redact:
            ws.cell(row=i, column=3).number_format = "mm/dd/yyyy"
    # two-level header: values vs checks
    ws.cell(row=4, column=2, value="Reference values").font = F_BOLD
    ws.cell(row=4, column=6, value="Result per field (across all of the person's documents)").font = F_BOLD
    _page_setup(ws)


def _sheet_review_queue(ws: Worksheet, ctx: _Ctx, rows_meta: list[dict], subtitle: str) -> None:
    ws.sheet_properties.tabColor = AMBER_TAB
    _title(ws, "Review Queue - work list for staff", subtitle)
    ws["A3"] = "Issues first, then items to check by eye. Fill in the last three columns as you work through the list."
    ws["A3"].font = F_SUB
    by_doc: dict[str, dict] = {}
    for m in rows_meta:
        if m["result"] == Status.CORRECT:
            continue
        e = by_doc.setdefault(m["doc"].doc_id, {"doc": m["doc"], "result": m["result"], "people": [], "reasons": []})
        if m["result"] == Status.ISSUE:
            e["result"] = Status.ISSUE
        if m["pid"]:
            e["people"].append(ctx.person_name(m["pid"]))
        e["reasons"] += [r for r in m["reasons"].split("\n") if r]
    entries = sorted(by_doc.values(), key=lambda e: (0 if e["result"] == Status.ISSUE else 1, e["doc"].doc_id))
    headers = ["#", "Priority", "Document", "Person(s)", "What to check", "Suggested action",
               "Reviewed by", "Review date", "Outcome"]
    widths = [5, 19, 46, 22, 62, 40, 14, 13, 22]
    rows = []
    for i, e in enumerate(entries, start=1):
        reasons = "\n".join(dict.fromkeys(e["reasons"]))
        rows.append([i, None, ctx.doc_title(e["doc"]), ", ".join(dict.fromkeys(p for p in e["people"] if p)) or "-",
                     reasons, _suggest(e["doc"], e["result"], reasons), "", "", ""])
    top = 5
    last = _table(ws, top, headers, widths, rows, center_cols=(1, 2), freeze_col=4)
    for i, e in enumerate(entries, start=top + 1):
        _paint_status(ws.cell(row=i, column=2), e["result"],
                      "1 - Issue" if e["result"] == Status.ISSUE else "2 - Check manually")
        ws.cell(row=i, column=8).number_format = "mm/dd/yyyy"
        link = ctx.link(e["doc"])
        if link:
            ws.cell(row=i, column=3).hyperlink = link
            ws.cell(row=i, column=3).font = F_LINK
    if entries:
        dv = DataValidation(type="list", allow_blank=True,
                            formula1='"Confirmed OK,Corrected,New copy requested,Escalated,Duplicate removed"')
        dv.error, dv.errorTitle = "Choose a value from the list.", "Outcome"
        ws.add_data_validation(dv)
        dv.add(f"I{top + 1}:I{last}")
        dvd = DataValidation(type="date", allow_blank=True)
        ws.add_data_validation(dvd)
        dvd.add(f"H{top + 1}:H{last}")
    else:
        ws.cell(row=top + 1, column=3, value="Nothing to review.")
    _page_setup(ws)


def _sheet_duplicates(ws: Worksheet, ctx: _Ctx, result: RunResult, subtitle: str) -> None:
    ws.sheet_properties.tabColor = GREY_TAB
    _title(ws, "Possible duplicates", subtitle)
    ws["A3"] = "Duplicates are never deleted automatically. A person decides which copy to keep."
    ws["A3"].font = F_SUB
    rows = []
    for f in result.files:
        if f.duplicate_of:
            rows.append([ctx.file(f.file_id), ctx.file(f.duplicate_of), "Identical file (byte-for-byte copy)",
                         "Processed once; the copy is listed here and kept."])
    for d in result.documents:
        if d.duplicate_of:
            other = ctx.docs.get(d.duplicate_of)
            rows.append([ctx.doc_title(d), ctx.doc_title(other) if other else d.duplicate_of,
                         "Same document type and same details" if "identical" not in " ".join(d.review_reasons)
                         else "Pages look identical",
                         "Both kept and listed in the Review Queue."])
    if not rows:
        rows = [["No duplicates found", "", "", ""]]
    _table(ws, 5, ["Document", "Looks like a duplicate of", "Why it was flagged", "What the tool did"],
           [48, 48, 34, 44], rows, freeze_col=2)
    _page_setup(ws)


def _sheet_files(ws: Worksheet, ctx: _Ctx, result: RunResult, subtitle: str) -> None:
    ws.sheet_properties.tabColor = GREY_TAB
    _title(ws, "Input files", subtitle)
    rows, sts = [], []
    for f in result.files:
        if f.error:
            st, msg = Status.ISSUE, f.error
        elif f.skipped:
            st, msg = Status.NOT_APPLICABLE, f.skipped
        elif f.duplicate_of:
            st, msg = Status.CHECK, f"Exact copy of {ctx.file(f.duplicate_of)}"
        else:
            st, msg = Status.CORRECT, "Processed"
        rows.append([f.file_id, ctx.file(f.file_id), "" if ctx.redact else f.folder, f.ext.lstrip(".").upper(),
                     "Yes" if f.converted else ("Already PDF" if f.pdf_path else "-"), f.page_count or "-", msg])
        sts.append(st)
    _table(ws, 4, ["File ID", "Original file", "Folder", "Type", "Converted to PDF", "Pages", "Result"],
           [8, 50, 16, 7, 15, 7, 48], rows, center_cols=(1, 4, 5, 6), freeze_col=3)
    for i, st in enumerate(sts, start=5):
        c = ws.cell(row=i, column=7)
        c.fill = FILL[st]
    _page_setup(ws)


def _sheet_pages(ws: Worksheet, ctx: _Ctx, result: RunResult, cfg: Config, subtitle: str) -> None:
    ws.sheet_properties.tabColor = GREY_TAB
    _title(ws, "Page trace - how every page was read and recognised", subtitle)
    page_doc = {p.ref: d for d in result.documents for p in d.pages}
    rows = []
    for p in result.pages:
        tname = cfg.types[p.type_id].name if p.type_id else (
            f"Back / envelope of {cfg.types[p.continuation_of].name}" if p.continuation_of else "Not recognised")
        d = page_doc.get(p.ref)
        rows.append([ctx.file(p.file_id), p.index + 1,
                     "Digital text" if p.method == "text layer" else ("Scan (OCR)" if p.method == "ocr" else p.method),
                     "-" if p.method == "text layer" else round(p.ocr_confidence),
                     f"{p.rotation}°" if p.rotation else "", tname,
                     p.type_confidence if p.type_id else None, ctx.doc_title(d) if d else ""])
    _table(ws, 4, ["Original file", "Page", "Read as", "Scan quality (0-100)", "Turned", "Recognised as",
                   "Type confidence", "Placed in document"], [44, 6, 13, 12, 8, 34, 12, 50], rows,
           center_cols=(2, 3, 4, 5, 7), freeze_col=3)
    for i in range(5, 5 + len(rows)):
        ws.cell(row=i, column=7).number_format = "0%"
    _page_setup(ws)


def _sheet_selftest(ws: Worksheet, results: dict, subtitle: str) -> None:
    ws.sheet_properties.tabColor = GREY_TAB
    s = results["summary"]
    _title(ws, "Self-test - Guide 1 rule test", subtitle)
    ws["A3"] = (f"Each of the {s['total']} example file names in Guide 1 was read back and rebuilt with the tool's own "
                f"naming rules. Recognised type: {s['mapped']} of {s['total']}. Rebuilt identically: {s['identical']}. "
                f"Same except capitals/spacing: {s['same_content']}. Different: {s['differs']} "
                "(each one is an inconsistency inside Guide 1 itself, explained below).")
    ws["A3"].font = F_SUB
    ws["A3"].alignment = Alignment(wrap_text=True)
    ws.merge_cells("A3:E3")
    ws.row_dimensions[3].height = 44
    outcome_status = {"identical": Status.CORRECT, "same content": Status.CORRECT, "differs": Status.ISSUE,
                      "not mapped": Status.ISSUE}
    rows = [[r.doc, r.type_id or "-", r.follows_template, r.outcome, " ".join(r.reasons) or "-"]
            for r in results["rows"]]
    _table(ws, 5, ["Guide document", "Mapped to type", "Follows the format", "Outcome", "Explanation"],
           [15, 24, 16, 15, 90], rows, center_cols=(1, 3, 4), freeze_col=2)
    for i, r in enumerate(results["rows"], start=6):
        st = outcome_status.get(r.outcome, Status.CHECK)
        _paint_status(ws.cell(row=i, column=4), st, r.outcome)
    _page_setup(ws)


def _sheet_about(ws: Worksheet, result: RunResult, cfg: Config, redact: bool, subtitle: str) -> None:
    ws.sheet_properties.tabColor = GREY_TAB
    _title(ws, "About this report", subtitle)
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 120
    s = result.stats
    sections = [
        ("WHAT THIS IS", None),
        ("Purpose", "Confirms whether each document agrees with the same person's other documents on Name, "
                    "Date of birth, SSN and Home address, and lists exactly which document to review if not."),
        ("Main tab", "'Document Check'. Sorted with Issues first, then Check manually, then Correct."),
        ("RESULTS", None),
        ("Correct", "Every value on the document matches the person's other documents. Nothing to do."),
        ("Issue", "Two documents disagree (e.g. a different address or SSN). A person must decide which is right."),
        ("Check manually", "The tool could not read a value or is not sure of the document type. "
                           "This is not necessarily a problem in the document - look at it by eye."),
        ("n/a", "This type of document does not normally show this value (e.g. a W-2 has no date of birth)."),
        ("HOW CHECKS WORK", None),
        ("People", "Documents are linked to a person by SSN or by first + last name. Middle names, initials and "
                   "small spelling differences are tolerated."),
        ("Reference value", "For each person and field the most trustworthy document is the reference: SSN card, "
                            "then tax return, then W-2 for SSN; ID card for date of birth; latest tax return for "
                            "home address. Every other document is compared with it."),
        ("Addresses", "'Home address' (where the person lives) and 'property address' (the house on insurance, "
                      "mortgage, HOA or contract documents) are checked separately on purpose. An older document "
                      "with a different home address may simply show a previous address."),
        ("Confidence", "How sure the tool is about the document type and file name. Below "
                       f"{cfg.settings.get('confidence.auto_accept', 0.8):.0%} the document is sent to the Review Queue."),
        ("PRIVACY", None),
        ("SSN", "Always masked to the last 4 digits (***-**-1234). The full number is never written anywhere."),
        ("This file", "Contains personal financial data" + (" (names, dates of birth and addresses are redacted in "
                                                           "this copy)." if redact else ".")
                      + " Keep it on this computer and delete the output folder securely after use."),
        ("Processing", "Everything ran on this computer. No document or value was sent to the internet."),
        ("LIMITATIONS", None),
        ("Scans", "Text on poor scans and phone photos can be misread. Values the tool is unsure about are marked "
                  "'Check manually' instead of being guessed."),
        ("Not a decision", "This report supports a human reviewer; it does not replace one."),
        ("THIS RUN", None),
        ("Run", result.run_dir.name),
        ("Files / pages", f"{s['files_found']} files ({s['files_skipped']} skipped, {s['files_with_problems']} with "
                          f"problems); {s['pages']} pages ({s['pages_text_layer']} digital, {s['pages_ocr']} scanned)"),
        ("Documents", f"{s['documents']} found: {s['documents_correct']} Correct, {s['documents_issue']} Issue, "
                      f"{s['documents_check']} Check manually"),
        ("Rules", f"{len(cfg.types)} document types configured"),
        ("Software", f"{APP_NAME} {__version__}"),
    ]
    r = 4
    for k, v in sections:
        if v is None:
            r += 1
            c = ws.cell(row=r, column=1, value=k)
            c.font = Font(name=FONT, size=11, bold=True, color=NAVY)
        else:
            ws.cell(row=r, column=1, value=k).font = F_BOLD
            c = ws.cell(row=r, column=2, value=v)
            c.font, c.alignment = F_BODY, WRAP
            ws.row_dimensions[r].height = _estimate_height([v], [120])
            ws.cell(row=r, column=1).alignment = WRAP
        r += 1
    _page_setup(ws, landscape=False)

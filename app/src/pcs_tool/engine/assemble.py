"""Step 4 - Split bulk scans: turn a stream of classified pages into documents.

Three strategies, chosen per type in the config:

* single_page - every page is its own document (ID cards).
* contiguous  - consecutive pages of the same type in the same file form one
                document, until a "first page" marker (e.g. "Page 1 of 3",
                a new W-2 heading) starts the next one.
* grouped     - pages are collected across files by a key such as
                (borrower folder, tax year) and put back into the standard order.
                Needed because real bulk scans are often shuffled: a tax return's
                page 1 may be in one scan and its page 2 in another.

Pages that match no type become "Unclassified" documents in the review queue,
so nothing is ever silently dropped.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..config import Config
from ..models import Document, Page, SourceFile
from .classify import detect_year, is_first_page, order_index, page_number_hint

BLANK_CHARS = 25


def _alnum(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]", text))


def assemble_documents(pages: list[Page], files: dict[str, SourceFile], cfg: Config) -> list[Document]:
    docs: list[Document] = []
    counter = iter(range(1, 100000))

    def new_doc(type_id: str | None, pgs: list[Page], folder: str) -> Document:
        name = cfg.types[type_id].name if type_id else "Unclassified"
        d = Document(doc_id=f"D{next(counter):03d}", type_id=type_id, type_name=name, pages=pgs, folder=folder)
        docs.append(d)
        return d

    # ---- grouped types (collected across files) --------------------------------
    grouped_ids = {t.id for t in cfg.types.values() if t.mode == "grouped"}
    grouped_pages = [p for p in pages if p.type_id in grouped_ids]
    for p in grouped_pages:
        dt = cfg.types[p.type_id]
        p.year = detect_year(dt, p.text)
        p.order_key = f"{order_index(dt, p.text):03d}-{page_number_hint(p.text) or 0:02d}"
    # fill missing years from the nearest page of the same type in the same file
    by_file: dict[str, list[Page]] = defaultdict(list)
    for p in grouped_pages:
        by_file[p.file_id].append(p)
    for fpages in by_file.values():
        fpages.sort(key=lambda x: x.index)
        for p in fpages:
            if p.year is None:
                near = sorted((q for q in fpages if q.year), key=lambda q: abs(q.index - p.index))
                if near:
                    p.year = near[0].year
    groups: dict[tuple, list[Page]] = defaultdict(list)
    for p in grouped_pages:
        folder = files[p.file_id].folder
        if p.year is None:  # still unknown: use the only year in this folder, if exactly one
            years = {q.year for q in grouped_pages if q.year and files[q.file_id].folder == folder and q.type_id == p.type_id}
            p.year = years.pop() if len(years) == 1 else None
        groups[(p.type_id, folder, p.year)].append(p)
    for (tid, folder, year), gpages in groups.items():
        gpages.sort(key=lambda x: (x.order_key, x.file_id, x.index))
        d = new_doc(tid, gpages, folder)
        d.fields["group_year"] = year
        if year is None:
            d.review_reasons.append("Tax year not readable; pages were grouped by folder only - confirm.")
        if len({p.file_id for p in gpages}) > 1:
            d.fields["_assembled_from_files"] = sorted({p.file_id for p in gpages})

    # ---- everything else, file by file in page order ---------------------------
    rest = [p for p in pages if p.type_id not in grouped_ids]
    rest.sort(key=lambda p: (p.file_id, p.index))
    current: Document | None = None
    last_by_type: dict[tuple[str, str], Document] = {}
    pending: dict[tuple[str, str], list[Page]] = defaultdict(list)  # back pages seen before their document
    for p in rest:
        folder = files[p.file_id].folder
        same_file_adjacent = (current is not None and current.pages[-1].file_id == p.file_id
                              and current.pages[-1].index == p.index - 1)

        if p.type_id is None:
            if p.role == "continuation" and p.continuation_of:
                target = last_by_type.get((p.file_id, p.continuation_of))
                if target is not None:
                    target.pages.append(p)
                    continue
                pending[(p.file_id, p.continuation_of)].append(p)
                continue
            if _alnum(p.text) < BLANK_CHARS and same_file_adjacent and current is not None:
                current.pages.append(p)
                current.review_reasons.append(f"Nearly blank page {p.ref} was attached to this document - confirm it belongs here.")
                continue
            d = new_doc(None, [p], folder)
            if p.error:
                d.review_reasons.append(p.error)
            elif p.ink < 0.01 and _alnum(p.text.split("---")[0]) < 60:
                d.review_reasons.append("Page appears to be blank (or extremely faint) - confirm and discard.")
            elif p.method == "ocr" and p.ocr_confidence < 45:
                d.review_reasons.append(f"Page unreadable (scan quality {p.ocr_confidence:.0f}/100) - open it and file it manually.")
            else:
                d.review_reasons.append("Page not recognised by the current rules - open it and file it manually.")
            current = d
            continue

        dt = cfg.types[p.type_id]
        mode = dt.mode
        start_new = True
        if mode == "contiguous" and current is not None and current.type_id == p.type_id and same_file_adjacent:
            hint = page_number_hint(p.text)
            if dt.assembly.get("one_per_page"):
                start_new = is_first_page(dt, p.text)
            elif hint and hint > 1:
                start_new = False
            else:
                start_new = is_first_page(dt, p.text) and len(current.pages) >= 1 and (hint in (None, 1))
                if not dt.assembly.get("first_page") and hint is None:
                    start_new = False
        if start_new:
            early = pending.pop((p.file_id, p.type_id), [])
            current = new_doc(p.type_id, [p] + early, folder)
            if early:
                current.review_reasons.append(
                    f"Related page {', '.join(e.ref for e in early)} (earlier in the scan) was attached - confirm it belongs here.")
            last_by_type[(p.file_id, p.type_id)] = current
        else:
            current.pages.append(p)  # type: ignore[union-attr]

    for (fid, _tid), leftover in pending.items():  # back pages whose document never appeared
        for p in leftover:
            d = new_doc(None, [p], files[fid].folder)
            d.review_reasons.append("Looks like the back or envelope of a document that was not found - file it manually.")

    docs.sort(key=lambda d: (d.folder, min((p.file_id, p.index) for p in d.pages)))
    for i, d in enumerate(docs, start=1):
        d.doc_id = f"D{i:03d}"
    return docs


def page_type_summary(pages: list[Page]) -> dict[str, int]:
    return dict(Counter(p.type_id or "unclassified" for p in pages))

"""The orchestrator: runs every step in order and reports progress.

    run_pipeline(input_dir, output_dir, progress=..., cancel=...) -> RunResult

Used identically by the command line and the desktop app (and later a web or
batch front-end). One bad file or page never stops the run: the problem is
recorded and the item goes to the review queue.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

from ..audit import AuditLog
from ..config import Config, load_config
from ..environment import find_tesseract
from ..models import Document, Page, RunResult, SourceFile
from . import pdf_backend
from .assemble import assemble_documents
from .classify import classify_page
from .convert import ConversionError, convert_to_pdf
from .extract import extract_document
from .label import render_label
from .normalize import safe_filename
from .ocr import OcrCache, PageReading, hash_distance, read_page, second_reading_job
from .verify import Person, verify

OUTPUT_MARKER = ".pcs_output"
ProgressFn = Callable[[str, int, int, str], None]


class PipelineError(Exception):
    """A problem that stops the run before it starts (bad folders, missing config)."""


# --------------------------------------------------------------------------- folder safety

def validate_folders(input_dir: Path, output_dir: Path) -> None:
    inp, out = input_dir.resolve(), output_dir.resolve()
    if not inp.is_dir():
        raise PipelineError("The input folder does not exist.")
    if inp == out:
        raise PipelineError("The output folder must be different from the input folder.")
    if out.is_relative_to(inp):
        raise PipelineError("The output folder cannot be inside the input folder (originals must stay untouched).")
    if inp.is_relative_to(out):
        raise PipelineError("The input folder cannot be inside the output folder.")
    if out.exists() and any(out.iterdir()) and not (out / OUTPUT_MARKER).exists():
        raise PipelineError("The output folder already contains other files. Choose an empty folder "
                            "or a folder previously created by this tool.")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- steps

def discover(input_dir: Path, cfg: Config) -> list[SourceFile]:
    exts = {e.lower() for e in cfg.settings.get("processing.supported_extensions", [])}
    ignore = {n.lower() for n in cfg.settings.get("processing.ignore_names", [])}
    files: list[SourceFile] = []
    for path in sorted(input_dir.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file() or path.name.lower() in ignore or path.name.startswith(("~$", ".")):
            continue
        rel = path.relative_to(input_dir)
        folder = rel.parts[0] if len(rel.parts) > 1 else ""
        sf = SourceFile(file_id=f"F{len(files) + 1:03d}", path=path, relative=str(rel), folder=folder,
                        ext=path.suffix.lower())
        if sf.ext in (".xlsx", ".xls", ".xlsm", ".csv"):
            sf.skipped = "Skipped: spreadsheet (not a client document)."
        elif sf.ext not in exts:
            sf.error = f"File type '{sf.ext or 'none'}' is not supported."
        elif path.stat().st_size > float(cfg.settings.get("processing.max_file_mb", 200)) * 1024 * 1024:
            sf.error = "File is larger than the configured size limit."
        files.append(sf)
    return files


def _worker_count(cfg: Config, override: int | None) -> int:
    n = override if override is not None else int(cfg.settings.get("processing.workers", 4))
    if n <= 0:
        n = max(1, (os.cpu_count() or 4) - 2)
    return n


def run_pipeline(
    input_dir: Path | str,
    output_dir: Path | str,
    cfg: Config | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    workers: int | None = None,
    redact: bool | None = None,
    guide_path: Path | None = None,
) -> RunResult:
    t0 = time.time()
    cfg = cfg or load_config()
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    validate_folders(input_dir, output_dir)
    progress = progress or (lambda *a: None)
    cancel = cancel or threading.Event()

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / OUTPUT_MARKER).write_text(
        "This folder was created by DociFi (Loan Document Check) and may be securely deleted by it.\n",
        encoding="utf-8")
    run_dir = output_dir / datetime.now().strftime(cfg.settings.get("output.run_folder_format", "run_%Y-%m-%d_%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    audit = AuditLog(run_dir / "logs" / "audit.jsonl")
    audit.event("run", "start", workers=_worker_count(cfg, workers))

    ocr_settings = {
        "language": cfg.settings.get("ocr.language", "eng"),
        "dpi": cfg.settings.get("ocr.dpi", 300),
        "low_confidence": cfg.settings.get("ocr.low_confidence", 60),
        "try_all_rotations_below": cfg.settings.get("ocr.try_all_rotations_below", 45),
        "min_text_layer_chars": cfg.settings.get("ocr.min_text_layer_chars", 50),
        "tesseract_path": find_tesseract(cfg.settings.get("ocr.tesseract_path", "") or ""),
        "second_engine": bool(cfg.settings.get("ocr.second_engine", True)),
        "second_reading_below": cfg.settings.get("ocr.second_reading_below", 70),
    }

    # 1. discover + fingerprint ------------------------------------------------
    progress("Finding files", 0, 1, "Looking for documents in the input folder")
    files = discover(input_dir, cfg)
    seen: dict[str, str] = {}
    for sf in files:
        if sf.error or sf.skipped:
            audit.event("ingest", "skipped", file=sf.file_id, reason=sf.error or sf.skipped)
            continue
        try:
            sf.sha256 = _sha256(sf.path)
        except OSError:
            sf.error = "File could not be opened (it may be in use or damaged)."
            continue
        if sf.sha256 in seen:
            sf.duplicate_of = seen[sf.sha256]
            audit.event("ingest", "exact_duplicate", file=sf.file_id, duplicate_of=sf.duplicate_of)
        else:
            seen[sf.sha256] = sf.file_id
    audit.event("ingest", "found", files=len(files))

    # 2. convert ------------------------------------------------------------------
    conv_dir = run_dir / "converted"
    for i, sf in enumerate(files, start=1):
        progress("Converting to PDF", i, len(files), f"File {sf.file_id}")
        if sf.error or sf.skipped or sf.duplicate_of or cancel.is_set():
            continue
        try:
            sf.pdf_path, sf.converted, method = convert_to_pdf(sf.path, conv_dir, sf.file_id)
            sf.page_count = pdf_backend.page_count(sf.pdf_path)
            audit.event("convert", "ok", file=sf.file_id, method=method, pages=sf.page_count)
        except ConversionError as exc:
            sf.error = str(exc)
            audit.event("convert", "failed", file=sf.file_id, reason=str(exc))
        except Exception as exc:  # noqa: BLE001
            sf.error = f"Unexpected problem while converting ({type(exc).__name__})."
            audit.event("convert", "failed", file=sf.file_id, reason=type(exc).__name__)

    # 3. read pages (text layer / OCR, cached, parallel) ----------------------------
    cache = OcrCache(output_dir / ".pcs_cache", ocr_settings)
    jobs: list[tuple[SourceFile, int]] = [(sf, i) for sf in files if sf.pdf_path for i in range(sf.page_count)]
    readings: dict[tuple[str, int], PageReading] = {}
    todo = []
    for sf, i in jobs:
        hit = cache.get(sf.sha256, i)
        if hit:
            readings[(sf.file_id, i)] = hit
        else:
            todo.append((sf, i))
    done = len(readings)
    progress("Reading pages", done, len(jobs), f"{done} pages already read earlier (cache)")
    if todo and not cancel.is_set():
        n_workers = min(_worker_count(cfg, workers), len(todo))
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(read_page, sf.pdf_path, i, ocr_settings): (sf, i) for sf, i in todo}
            for fut in as_completed(futs):
                sf, i = futs[fut]
                try:
                    r = fut.result()
                except Exception as exc:  # noqa: BLE001
                    r = PageReading("", "failed", 0.0, 0, "", f"Page could not be read ({type(exc).__name__}).")
                readings[(sf.file_id, i)] = r
                cache.put(sf.sha256, i, r)
                done += 1
                progress("Reading pages", done, len(jobs), f"Read page {i + 1} of {sf.file_id}")
                if cancel.is_set():
                    for f in futs:
                        f.cancel()
                    break
    if cancel.is_set():
        audit.event("run", "cancelled")
        audit.close()
        return RunResult(run_dir, files, [], [], {}, cancelled=True, duration_s=time.time() - t0)

    # 4. classify pages ---------------------------------------------------------------
    def classified(p: Page) -> Page:
        c = classify_page(p.text, cfg, p.ocr_confidence)
        p.type_id, p.type_score, p.type_confidence, p.runner_up = c.type_id, c.score, c.confidence, c.runner_up
        if c.continuation_of:
            p.role, p.continuation_of = "continuation", c.continuation_of
        return p

    pages: list[Page] = []
    for sf, i in jobs:
        r = readings[(sf.file_id, i)]
        p = classified(Page(file_id=sf.file_id, index=i, text=r.text, method=r.method, ocr_confidence=r.confidence,
                            rotation=r.rotation, image_hash=r.image_hash, error=r.error, ink=r.ink))
        if p.type_id and cfg.types[p.type_id].category == "identity" and p.method == "ocr":
            parts = _split_cards(sf, p, cache, ocr_settings)
            if len(parts) > 1:
                audit.event("classify", "cards_split", page=p.ref, cards=len(parts))
                pages += [classified(x) for x in parts]
                continue
        pages.append(p)
    for p in pages:
        audit.event("classify", "page", page=p.ref, type=p.type_id or "unrecognised", score=p.type_score,
                    confidence=p.type_confidence, method=p.method, ocr_quality=p.ocr_confidence)
    progress("Recognising documents", 1, 1, f"{len(pages)} pages recognised")

    # 5. split into documents -----------------------------------------------------------
    file_map = {sf.file_id: sf for sf in files}
    docs = assemble_documents(pages, file_map, cfg)

    # 6. extract + label -------------------------------------------------------------------
    auto_accept = float(cfg.settings.get("confidence.auto_accept", 0.8))
    base_reasons = {d.doc_id: list(d.review_reasons) for d in docs}
    base_fields = {d.doc_id: dict(d.fields) for d in docs}

    def read_details(d: Document) -> None:
        d.review_reasons = list(base_reasons[d.doc_id])
        d.fields, d.field_confidence, d.persons = dict(base_fields[d.doc_id]), {}, []
        try:
            extract_document(d, cfg)
        except Exception as exc:  # noqa: BLE001
            d.review_reasons.append(f"Details could not be read ({type(exc).__name__}).")
        _label_and_score(d, cfg, auto_accept)

    for k, d in enumerate(docs, start=1):
        progress("Reading details", k, len(docs), f"Document {d.doc_id}")
        read_details(d)

    # 6b. second reading, only where a needed value is still missing ----------------------
    file_map_ = {sf.file_id: sf for sf in files}
    retry_docs = [d for d in docs if d.type_id and (not d.label_complete or _missing_person_values(d, cfg))]
    todo2 = sorted({(p.file_id, p.index) for d in retry_docs for p in d.pages
                    if p.method == "ocr" and p.region is None and readings[(p.file_id, p.index)].second_quality == 0})
    if todo2 and not cancel.is_set() and ocr_settings["second_engine"]:
        done2 = 0
        progress("Second reading", 0, len(todo2), f"{len(todo2)} pages need a closer look")
        with ProcessPoolExecutor(max_workers=min(_worker_count(cfg, workers), len(todo2))) as pool:
            futs = {pool.submit(second_reading_job, file_map_[f].pdf_path, i, readings[(f, i)], ocr_settings): (f, i)
                    for f, i in todo2}
            for fut in as_completed(futs):
                f, i = futs[fut]
                try:
                    readings[(f, i)] = fut.result()
                    cache.put(file_map_[f].sha256, i, readings[(f, i)])
                except Exception:  # noqa: BLE001 - keep the first reading
                    pass
                done2 += 1
                progress("Second reading", done2, len(todo2), f"Page {i + 1} of {f}")
        for p in pages:
            r = readings.get((p.file_id, p.index))
            if r and p.region is None and r.second_quality and (p.file_id, p.index) in set(todo2):
                p.text, p.ocr_confidence = r.text, r.confidence
        for d in retry_docs:
            read_details(d)
        audit.event("ocr", "second_reading", pages=len(todo2), documents=len(retry_docs))

    for d in docs:
        audit.event("document", "assembled", doc=d.doc_id, type=d.type_id or "unclassified", pages=d.page_refs,
                    confidence=d.confidence, label_complete=d.label_complete)

    # 7. duplicates (never deleted) ----------------------------------------------------------
    _flag_duplicates(docs, files, audit)

    # 8. verification -----------------------------------------------------------------------
    progress("Checking Name / DOB / SSN / Address", 1, 1, "Comparing documents")
    people = verify([d for d in docs if d.type_id], cfg)
    for d in docs:
        for c in d.checks:
            audit.event("verify", "check", doc=d.doc_id, person=c.person, field_name=c.field, status=c.status.value)

    # 9. write labeled documents -------------------------------------------------------------
    _write_documents(docs, people, files, run_dir, cfg, progress, audit)

    # 10. report -------------------------------------------------------------------------------
    result = RunResult(run_dir=run_dir, files=files, pages=pages, documents=docs,
                       persons={p.person_id: {"person": p} for p in people})
    result.duration_s = round(time.time() - t0, 1)
    result.stats = _stats(result)
    progress("Writing check sheet", 1, 1, "Creating the Excel check sheet")
    from .report import write_check_sheet  # local import keeps UI start-up fast
    guide_results = None
    if guide_path:  # optional extra check: a bad guide file must never cost the user their results
        from .selftest import rule_test
        try:
            guide_results = rule_test(guide_path, cfg)
        except Exception as exc:  # noqa: BLE001
            audit.event("selftest", "skipped", reason=type(exc).__name__)
            progress("Writing check sheet", 1, 1, "Guide/rules file could not be read - naming test skipped")
    redact_flag = bool(cfg.settings.get("output.redact_personal_values", False)) if redact is None else redact
    result.sheet_path = write_check_sheet(result, people, cfg, run_dir / "Check_Sheet.xlsx",
                                          redact=redact_flag, guide_results=guide_results)
    (run_dir / "run_summary.json").write_text(json.dumps(result.stats, indent=2), encoding="utf-8")
    result.log_path = run_dir / "logs" / "audit.jsonl"
    audit.event("run", "finished", **{k: v for k, v in result.stats.items() if isinstance(v, (int, float))})
    audit.close()
    progress("Finished", 1, 1, "Done")
    return result


# --------------------------------------------------------------------------- helpers

def _split_cards(sf: SourceFile, page: Page, cache: OcrCache, ocr_settings: dict) -> list[Page]:
    """A sheet with several ID cards (e.g. both spouses' licences) becomes one page per card."""
    from . import cards, ocr_second
    try:
        img = pdf_backend.render_page_rgb(sf.pdf_path, page.index, dpi=300)
        boxes = cards.find_cards(img)
    except Exception:  # noqa: BLE001 - fall back to treating the page as one document
        return [page]
    if len(boxes) < 2:
        return [page]
    parts = []
    for k, box in enumerate(boxes, start=1):
        key = f"{page.index:04d}_card{k}"
        hit = cache.get(sf.sha256, key)  # type: ignore[arg-type]
        if hit is None:
            read = ocr_second.read_image(cards.crop(img, box)) if ocr_second.available() else None
            if not read:
                return [page]
            hit = PageReading(text=read[0], method="ocr", confidence=round(read[1] * 0.8, 1), rotation=0,
                              image_hash=page.image_hash, ink=page.ink, second_quality=read[1])
            cache.put(sf.sha256, key, hit)  # type: ignore[arg-type]
        parts.append(Page(file_id=page.file_id, index=page.index, text=hit.text, method="ocr",
                          ocr_confidence=hit.confidence, rotation=0, image_hash=f"{page.image_hash}:{k}",
                          ink=page.ink, region=box, part=k))
    return parts


def _missing_person_values(d: Document, cfg: Config) -> bool:
    """True when a value this document type should carry (name, SSN, DOB, home address) was not read."""
    v = cfg.types[d.type_id].verify
    if not d.persons:
        return bool(v.get("name"))
    for r in d.persons:
        if v.get("name") and not r.name:
            return True
        if v.get("ssn") in (True, "required") and not r.ssn:
            return True
        if v.get("dob") in (True, "required") and not r.dob:
            return True
        if v.get("address") == "residence" and not (r.address and r.address.street):
            return True
    return False


def _label_and_score(d: Document, cfg: Config, auto_accept: float) -> None:
    if not d.type_id:
        d.label = f"UNCLASSIFIED - {d.doc_id}"
        d.confidence = 0.0
        return
    dt = cfg.types[d.type_id]
    d.label, d.label_complete, missing = render_label(dt.label, d.fields)
    page_conf = sum(p.type_confidence for p in d.pages if p.type_id) / max(1, sum(1 for p in d.pages if p.type_id))
    field_confs = [d.field_confidence.get(f, 0.0) for f in _label_field_names(dt.label)]
    field_conf = min(field_confs) if field_confs else 1.0
    d.confidence = round(page_conf * (0.6 + 0.4 * field_conf) if d.label_complete else min(0.5, page_conf * 0.5), 3)
    if missing:
        d.review_reasons.append("File name incomplete: " + ", ".join(m.replace("_", " ") for m in missing)
                                + " not readable. Type it in when renaming.")
    elif d.confidence < auto_accept:
        d.review_reasons.append(f"Tool is only {d.confidence:.0%} sure of the type / file name - confirm.")
    low_ocr = [p.ref for p in d.pages if p.method == "ocr" and p.ocr_confidence < 45]
    if low_ocr:
        where = "" if len(d.pages) == 1 else " on " + ", ".join(low_ocr)
        d.review_reasons.append(f"Poor scan quality{where} - values may be misread.")


def _label_field_names(template: str) -> list[str]:
    from .label import template_fields
    return [f for f, _, _ in template_fields(template)]


def _flag_duplicates(docs: list[Document], files: list[SourceFile], audit: AuditLog) -> None:
    if not docs:
        return
    seen_labels: dict[tuple[str, str], Document] = {}
    for d in docs:
        if d.type_id and d.label_complete and d.type_id != "personal_tax_return":
            key = (d.type_id, d.label.lower())
            if key in seen_labels:
                d.duplicate_of = seen_labels[key].doc_id
                d.review_reasons.append(f"Possible duplicate of {d.duplicate_of} (same details). Kept, not deleted - decide which to keep.")
            else:
                seen_labels[key] = d
    for i, a in enumerate(docs):
        if a.duplicate_of or any(p.region for p in a.pages):
            continue  # cards cut from the same sheet share the sheet's fingerprint; compare by details only
        for b in docs[:i]:
            if any(p.region for p in b.pages):
                continue
            if len(a.pages) == len(b.pages) and all(
                    hash_distance(pa.image_hash, pb.image_hash) <= 8 for pa, pb in zip(a.pages, b.pages)):
                a.duplicate_of = b.doc_id
                a.review_reasons.append(f"Pages look identical to {b.doc_id}. Kept, not deleted - decide which to keep.")
                break
    for d in docs:
        if d.duplicate_of:
            audit.event("duplicates", "flagged", doc=d.doc_id, duplicate_of=d.duplicate_of)


def _primary_person(d: Document, people: list[Person]) -> Person | None:
    ids = [r.person_id for r in d.persons if r.person_id]
    return next((p for p in people if ids and p.person_id == ids[0]), None)


def _write_documents(docs, people, files, run_dir: Path, cfg: Config, progress, audit) -> None:
    file_map = {sf.file_id: sf for sf in files}
    out_root = run_dir / "documents"
    used: set[str] = set()
    for k, d in enumerate(docs, start=1):
        progress("Saving labeled documents", k, len(docs), f"Document {d.doc_id}")
        if not d.type_id:
            folder = out_root / "_Needs review - unrecognised pages"
        else:
            person = _primary_person(d, people) if cfg.settings.get("output.group_documents_by_person", True) else None
            folder = out_root / safe_filename(person.display_name if person else (d.folder or "Other documents"))
        # The file name is exactly the Guide-format label; review status lives in the check sheet.
        base = safe_filename(d.label if d.type_id else f"UNRECOGNISED PAGE - {d.doc_id}")
        name = base
        n = 2
        while str(folder / name).lower() in used:
            name = f"{base} ({n})"
            n += 1
        used.add(str(folder / name).lower())
        target = folder / f"{name}.pdf"
        try:
            pdf_backend.assemble([(file_map[p.file_id].pdf_path, p.index, p.rotation, p.region) for p in d.pages],
                                 target)
            d.output_path = target
            audit.event("output", "saved", doc=d.doc_id)
        except Exception as exc:  # noqa: BLE001
            d.review_reasons.append(f"Could not save the PDF ({type(exc).__name__}).")
            audit.event("output", "failed", doc=d.doc_id, reason=type(exc).__name__)
    for sf in files:  # exact duplicate files: listed, never processed twice, never deleted
        if sf.duplicate_of:
            dup_dir = out_root / "_Duplicate files (not processed)"
            dup_dir.mkdir(parents=True, exist_ok=True)
            if sf.pdf_path is None:
                shutil.copy2(sf.path, dup_dir / f"{sf.file_id}{sf.ext}")


def _stats(r: RunResult) -> dict:
    docs = r.documents
    by_type: dict[str, int] = {}
    for d in docs:
        by_type[d.type_name] = by_type.get(d.type_name, 0) + 1
    return {
        "files_found": len(r.files),
        "files_skipped": sum(1 for f in r.files if f.skipped),
        "files_with_problems": sum(1 for f in r.files if f.error),
        "exact_duplicate_files": sum(1 for f in r.files if f.duplicate_of),
        "pages": len(r.pages),
        "pages_text_layer": sum(1 for p in r.pages if p.method == "text layer"),
        "pages_ocr": sum(1 for p in r.pages if p.method == "ocr"),
        "pages_unrecognised": sum(1 for p in r.pages if not p.type_id and p.role != "continuation"),
        "documents": len(docs),
        "documents_labeled_complete": sum(1 for d in docs if d.label_complete),
        "documents_needing_review": sum(1 for d in docs if d.needs_review),
        "documents_correct": sum(1 for d in docs if d.result.value == "Correct"),
        "documents_issue": sum(1 for d in docs if d.result.value == "Issue"),
        "documents_check": sum(1 for d in docs if d.result.value == "Check manually"),
        "possible_duplicates": sum(1 for d in docs if d.duplicate_of),
        "people": len(r.persons),
        "documents_by_type": by_type,
        "seconds": r.duration_s,
    }

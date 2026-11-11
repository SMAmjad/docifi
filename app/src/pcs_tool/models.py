"""Plain data objects passed between the engine stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from .engine.normalize import SSN, Address


class Status(str, Enum):
    """Outcome of one check.

    ISSUE  - the documents disagree (a real problem in the file).
    CHECK  - the tool could not read the value; a person should look (not necessarily a problem).
    """
    CORRECT = "Correct"
    ISSUE = "Issue"
    CHECK = "Check manually"
    NOT_APPLICABLE = "Not applicable"


@dataclass
class SourceFile:
    """One file found in the input folder."""
    file_id: str                 # F001, F002 ... (used in logs instead of the file name)
    path: Path
    relative: str                # path relative to the input folder (may contain names - never logged)
    folder: str                  # first sub-folder (e.g. "Borrower 1"); hint only
    ext: str
    sha256: str = ""
    pdf_path: Path | None = None
    converted: bool = False
    page_count: int = 0
    error: str | None = None
    skipped: str | None = None   # not a client document (e.g. a spreadsheet) - ignored on purpose
    duplicate_of: str | None = None


@dataclass
class Page:
    """One page of one source file, after reading (text layer or OCR)."""
    file_id: str
    index: int                   # 0-based page index inside the source PDF
    text: str = ""
    method: str = ""             # "text layer" | "ocr"
    ocr_confidence: float = 100.0
    rotation: int = 0
    image_hash: str = ""
    type_id: str | None = None
    type_score: float = 0.0
    type_confidence: float = 0.0
    runner_up: str | None = None
    year: int | None = None
    order_key: str = ""
    role: str = "body"           # body | continuation
    continuation_of: str | None = None
    error: str | None = None
    ink: float = 1.0
    region: tuple[float, float, float, float] | None = None  # part of the page (one of several ID cards)
    part: int = 0                # 1, 2 ... when a page holds several cards

    @property
    def ref(self) -> str:
        suffix = f" card {self.part}" if self.part else ""
        return f"{self.file_id}-p{self.index + 1}{suffix}"


@dataclass
class PersonRecord:
    """A person as described by one document."""
    role: str
    name: str = ""
    ssn: SSN | None = None
    dob: date | None = None
    address: Address | None = None
    person_id: str | None = None     # P1, P2 ... assigned by verification


@dataclass
class Check:
    """Result of one field check (Name / DOB / SSN / Address) for one person on one document."""
    field: str
    status: Status
    reason: str = ""
    review_doc: str = ""
    person: str = ""             # P1, P2 ... (empty for document-level checks)


@dataclass
class Document:
    """A logical document assembled from one or more pages."""
    doc_id: str
    type_id: str | None
    type_name: str
    pages: list[Page]
    folder: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    field_confidence: dict[str, float] = field(default_factory=dict)
    persons: list[PersonRecord] = field(default_factory=list)
    property_address: Address | None = None
    label: str = ""
    label_complete: bool = False
    confidence: float = 0.0
    review_reasons: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    duplicate_of: str | None = None
    output_path: Path | None = None

    @property
    def page_refs(self) -> str:
        """Compact page list, e.g. 'F001 p1-3; F002 p5'."""
        out: list[str] = []
        by_file: dict[str, list[int]] = {}
        for p in self.pages:
            by_file.setdefault(p.file_id, []).append(p.index + 1)
        for fid, nums in by_file.items():
            nums.sort()
            ranges, start, prev = [], nums[0], nums[0]
            for n in nums[1:]:
                if n == prev + 1:
                    prev = n
                    continue
                ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
                start = prev = n
            ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
            out.append(f"{fid} p{','.join(ranges)}")
        return "; ".join(out)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons) or any(c.status in (Status.ISSUE, Status.CHECK) for c in self.checks)

    @property
    def result(self) -> Status:
        """Overall result of the document: Issue > Check manually > Correct."""
        if any(c.status == Status.ISSUE for c in self.checks):
            return Status.ISSUE
        if self.review_reasons or not self.type_id or any(c.status == Status.CHECK for c in self.checks):
            return Status.CHECK
        return Status.CORRECT


@dataclass
class RunResult:
    run_dir: Path
    files: list[SourceFile]
    pages: list[Page]
    documents: list[Document]
    persons: dict[str, dict[str, Any]]
    sheet_path: Path | None = None
    log_path: Path | None = None
    duration_s: float = 0.0
    cancelled: bool = False
    stats: dict[str, Any] = field(default_factory=dict)
    # Several documents folders in one run: folder name -> its own check sheet (empty for a single folder).
    sheets: dict[str, Path] = field(default_factory=dict)

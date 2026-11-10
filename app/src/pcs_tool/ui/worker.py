"""Runs the engine in a background thread so the window never freezes."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..config import ConfigError, load_config
from ..engine.pipeline import PipelineError, run_pipeline

# Share of the progress bar given to each engine stage (reading pages dominates).
STAGE_WEIGHTS = [
    ("Finding files", 2), ("Converting to PDF", 8), ("Reading pages", 62), ("Recognising documents", 2),
    ("Reading details", 3), ("Second reading", 10), ("Checking Name / DOB / SSN / Address", 3),
    ("Saving labeled documents", 9),
    ("Writing check sheet", 7), ("Finished", 1),
]


def overall_percent(stage: str, done: int, total: int) -> int:
    before, weight = 0, 0
    for name, w in STAGE_WEIGHTS:
        if name == stage:
            weight = w
            break
        before += w
    else:
        return -1
    frac = (done / total) if total else 1.0
    return int(100 * (before + weight * frac) / sum(w for _, w in STAGE_WEIGHTS))


class PipelineWorker(QObject):
    progress = Signal(str, int, int, str, int)   # stage, done, total, message, overall percent
    finished = Signal(object)                    # RunResult
    failed = Signal(str, str)                    # plain-English message, technical details
    cancelled = Signal()

    def __init__(self, input_dir: Path, output_dir: Path, redact: bool, guide: Path | None):
        super().__init__()
        self.input_dir, self.output_dir, self.redact, self.guide = input_dir, output_dir, redact, guide
        self.cancel_event = threading.Event()

    def _on_progress(self, stage: str, done: int, total: int, msg: str) -> None:
        self.progress.emit(stage, done, total, msg, overall_percent(stage, done, total))

    @Slot()
    def run(self) -> None:
        try:
            cfg = load_config()
            result = run_pipeline(self.input_dir, self.output_dir, cfg=cfg, progress=self._on_progress,
                                  cancel=self.cancel_event, redact=self.redact, guide_path=self.guide)
            if result.cancelled:
                self.cancelled.emit()
            else:
                self.finished.emit(result)
        except (PipelineError, ConfigError) as exc:
            self.failed.emit(str(exc), "")
        except Exception as exc:  # noqa: BLE001 - shown to the user in plain English
            self.failed.emit("Something unexpected went wrong. No original file was changed. "
                             "The technical details below can be sent to support.",
                             "".join(traceback.format_exception_only(type(exc), exc)).strip())

    def cancel(self) -> None:
        self.cancel_event.set()

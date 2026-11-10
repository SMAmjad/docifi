"""Audit trail: one JSON line per decision, with IDs only - never personal values.

The audit log answers "what did the tool do and why?" for every file, page and
document, without containing names, dates of birth, SSNs or addresses.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .privacy import redact_free_text

_FORBIDDEN_KEYS = {"name", "dob", "ssn", "address", "text", "label", "path", "filename"}

log = logging.getLogger("pcs_tool")


class AuditLog:
    """Thread-safe JSON-lines writer. Values under personal-looking keys are refused."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def event(self, stage: str, action: str, **data: Any) -> None:
        for key in list(data):
            if key.lower() in _FORBIDDEN_KEYS:
                data[key] = "[withheld]"
            elif isinstance(data[key], str):
                data[key] = redact_free_text(data[key])
        record = {"time": datetime.now().isoformat(timespec="seconds"), "stage": stage, "action": action, **data}
        line = json.dumps(record, default=str)
        with self._lock:
            if self._fh:
                self._fh.write(line + "\n")
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

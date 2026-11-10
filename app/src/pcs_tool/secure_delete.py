"""Secure deletion of an output folder after review.

Safeguards (all must pass before anything is touched):
* The folder must contain the marker file '.pcs_output' that this tool writes
  when it creates an output folder. Any other folder is refused.
* Drive roots, the user's home folder, Windows and Program Files are refused.
* The caller must confirm by typing the folder's exact name.
* Symbolic links are removed as links; their targets are never followed.

How it deletes: every file is overwritten once with random data, flushed to
disk, renamed to a random name and then removed.

Honest limitation: on SSDs and some cloud-synced folders the drive may keep
old copies of the data internally (wear levelling, snapshots), so overwriting
cannot be guaranteed to erase every physical copy. Full-disk encryption
(BitLocker) is the reliable protection for SSDs.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from .engine.pipeline import OUTPUT_MARKER


class SecureDeleteRefused(Exception):
    """The folder failed a safety check. Message is plain English."""


@dataclass
class DeleteReport:
    files: int = 0
    bytes: int = 0
    failures: int = 0


def _protected(path: Path) -> bool:
    p = path.resolve()
    if p.parent == p:  # drive root
        return True
    protected = {Path.home().resolve()}
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "USERPROFILE"):
        v = os.environ.get(var)
        if v:
            protected.add(Path(v).resolve())
    return p in protected or any(p == x.parent for x in protected if x.parent != x.parent.parent)


def check_folder(folder: Path) -> None:
    folder = Path(folder)
    if not folder.exists():
        raise SecureDeleteRefused("The folder does not exist.")
    if not folder.is_dir() or folder.is_symlink():
        raise SecureDeleteRefused("This is not a normal folder.")
    if _protected(folder):
        raise SecureDeleteRefused("This is a system or top-level folder and will never be deleted.")
    if not (folder / OUTPUT_MARKER).is_file():
        raise SecureDeleteRefused("This folder was not created by DociFi "
                                  f"(the '{OUTPUT_MARKER}' marker is missing), so it will not be deleted.")


def _wipe_file(path: Path) -> int:
    size = path.stat().st_size
    with open(path, "r+b", buffering=0) as fh:
        remaining = size
        while remaining > 0:
            chunk = min(remaining, 1 << 20)
            fh.write(secrets.token_bytes(chunk))
            remaining -= chunk
        fh.flush()
        os.fsync(fh.fileno())
    scrambled = path.with_name(secrets.token_hex(8))
    path.rename(scrambled)
    scrambled.unlink()
    return size


def secure_delete(folder: Path, typed_confirmation: str) -> DeleteReport:
    """Delete ``folder`` after all safeguards pass. ``typed_confirmation`` must equal the folder name."""
    folder = Path(folder)
    check_folder(folder)
    if typed_confirmation.strip() != folder.name:
        raise SecureDeleteRefused("The typed name does not match the folder name. Nothing was deleted.")
    report = DeleteReport()
    for root, dirs, files in os.walk(folder, topdown=False, followlinks=False):
        rp = Path(root)
        for name in files:
            fp = rp / name
            try:
                if fp.is_symlink():
                    fp.unlink()
                    continue
                os.chmod(fp, 0o600)
                report.bytes += _wipe_file(fp)
                report.files += 1
            except OSError:
                report.failures += 1
        for name in dirs:
            dp = rp / name
            try:
                dp.unlink() if dp.is_symlink() else dp.rmdir()
            except OSError:
                report.failures += 1
    try:
        folder.rmdir()
    except OSError:
        report.failures += 1
    return report

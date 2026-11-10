"""Command line entry point.

    pcs run       --input <folder> --output <folder> [--guide <Guide 1.xlsx>] [--redact] [--workers N]
    pcs selftest  --guide <Guide 1.xlsx>
    pcs envcheck
    pcs secure-delete --folder <output folder>

Console output never contains names, dates of birth, SSNs or addresses.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__
from .config import ConfigError, load_config


def _bar(stage: str, done: int, total: int, _msg: str, _state: dict = {}) -> None:  # noqa: B006
    now = time.time()
    if stage == _state.get("stage") and done < total and now - _state.get("t", 0) < 0.5:
        return
    _state.update(stage=stage, t=now)
    pct = int(100 * done / total) if total else 100
    filled = pct // 5
    sys.stdout.write(f"\r  {stage:<38} [{'#' * filled}{'.' * (20 - filled)}] {pct:3d}%")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")


def cmd_run(args: argparse.Namespace) -> int:
    from .engine.pipeline import PipelineError, run_pipeline
    cfg = load_config(Path(args.config) if args.config else None)
    print(f"{APP_NAME} {__version__}")
    try:
        result = run_pipeline(args.input, args.output, cfg=cfg, progress=_bar, workers=args.workers,
                              redact=args.redact, guide_path=Path(args.guide) if args.guide else None)
    except PipelineError as exc:
        print(f"\nCannot start: {exc}")
        return 2
    s = result.stats
    print("\nResult")
    print(f"  Files ........................ {s['files_found']} ({s['files_skipped']} skipped, "
          f"{s['files_with_problems']} with problems, {s['exact_duplicate_files']} exact duplicates)")
    print(f"  Pages ........................ {s['pages']} ({s['pages_text_layer']} digital, {s['pages_ocr']} scanned)")
    print(f"  Documents .................... {s['documents']} ({s['documents_labeled_complete']} fully labeled)")
    print(f"  Result ....................... {s['documents_correct']} Correct, {s['documents_issue']} Issue, "
          f"{s['documents_check']} Check manually")
    print(f"  Possible duplicates .......... {s['possible_duplicates']}")
    print(f"  People ....................... {s['people']}")
    print(f"  Time ......................... {result.duration_s} s")
    print("  Documents by type:")
    for t, n in sorted(s["documents_by_type"].items()):
        print(f"    {n:3d}  {t}")
    print(f"\n  Check sheet: {result.sheet_path}")
    print(f"  Run folder:  {result.run_dir}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from .engine.selftest import print_rule_test, rule_test
    cfg = load_config(Path(args.config) if args.config else None)
    print(print_rule_test(rule_test(Path(args.guide), cfg)))
    return 0


def cmd_envcheck(args: argparse.Namespace) -> int:
    from .environment import check_environment
    cfg = load_config(Path(args.config) if args.config else None)
    ok = True
    print(f"{APP_NAME} {__version__} - environment check\n")
    for t in check_environment(cfg.settings.get("ocr.tesseract_path", "") or ""):
        mark = "OK     " if t.found else ("MISSING" if t.required else "info   ")
        print(f"  [{mark}] {t.name}: {t.detail}")
        if t.fix:
            print(f"            -> {t.fix}")
        ok &= t.found or not t.required
    src = "the rules built into DociFi" if getattr(sys, "frozen", False) else cfg.source_dir
    print(f"\n  Configuration: {len(cfg.types)} document types loaded from {src}")
    print("\n  Ready." if ok else "\n  Not ready: install the missing required item above.")
    return 0 if ok else 1


def cmd_secure_delete(args: argparse.Namespace) -> int:
    from .secure_delete import SecureDeleteRefused, check_folder, secure_delete
    folder = Path(args.folder)
    try:
        check_folder(folder)
    except SecureDeleteRefused as exc:
        print(f"Refused: {exc}")
        return 2
    n_files = sum(1 for p in folder.rglob("*") if p.is_file())
    print(f"This will permanently destroy {n_files} files in:\n  {folder.resolve()}\n")
    print("Note: on SSD drives, overwriting cannot guarantee that every physical copy is erased.")
    typed = args.confirm_name if args.confirm_name is not None else input(
        f"Type the folder name '{folder.name}' to confirm: ")
    try:
        rep = secure_delete(folder, typed)
    except SecureDeleteRefused as exc:
        print(f"Refused: {exc}")
        return 2
    print(f"Deleted {rep.files} files ({rep.bytes / 1_048_576:.1f} MB)."
          + (f" {rep.failures} items could not be removed (in use?)." if rep.failures else ""))
    return 0 if not rep.failures else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="docifi-cli", description=f"{APP_NAME} - convert, split, label and check loan documents locally.")
    p.add_argument("--config", help="Folder with document_types.yaml and rules.yaml (default: built-in)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Process every file in a folder")
    r.add_argument("--input", "-i", required=True, help="Folder with the documents (read only)")
    r.add_argument("--output", "-o", required=True, help="Folder for the results")
    r.add_argument("--guide", help="Optional Guide 1.xlsx to include the rule self-test in the check sheet")
    r.add_argument("--redact", action="store_true", default=None, help="Mask names, DOB and addresses in the sheet")
    r.add_argument("--workers", type=int, help="Parallel OCR workers (default from rules.yaml)")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("selftest", help="Check the label rules against Guide 1")
    s.add_argument("--guide", required=True, help="Path to Guide 1.xlsx")
    s.set_defaults(func=cmd_selftest)

    e = sub.add_parser("envcheck", help="Check that the required programs are installed")
    e.set_defaults(func=cmd_envcheck)

    d = sub.add_parser("secure-delete", help="Securely delete an output folder created by this tool")
    d.add_argument("--folder", required=True)
    d.add_argument("--confirm-name", help="Folder name typed in advance (for scripts); otherwise you are asked")
    d.set_defaults(func=cmd_secure_delete)
    return p


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}")
        return 3
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

"""Entry point used when DociFi is packaged as a single .exe (see build_exe.py).

    DociFi.exe                      opens the desktop app
    DociFi.exe run|selftest|envcheck|secure-delete ...
                                    the same engine from a command prompt (output in that window)
"""

import multiprocessing
import sys

CLI_COMMANDS = {"run", "selftest", "envcheck", "secure-delete", "-h", "--help", "--version"}


def _attach_console() -> None:
    """A windowed .exe has no console; borrow the one of the command prompt that started it."""
    if sys.stdout is not None and sys.stderr is not None:
        return  # output already redirected (file or pipe)
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
            sys.stderr = sys.stdout
            print()
    except OSError:
        pass
    if sys.stdout is None:  # started without any console: discard output rather than crash
        import os
        sys.stdout = sys.stderr = open(os.devnull, "w")  # noqa: SIM115


if __name__ == "__main__":
    multiprocessing.freeze_support()  # page readers run in child processes of the same .exe
    if len(sys.argv) > 1 and sys.argv[1] in CLI_COMMANDS:
        try:  # command-line use: no start-up picture
            import pyi_splash  # type: ignore

            pyi_splash.close()
        except ImportError:
            pass
        _attach_console()
        from pcs_tool.cli import main as cli_main

        sys.exit(cli_main(sys.argv[1:]))
    from pcs_tool.ui.app import main

    sys.exit(main())

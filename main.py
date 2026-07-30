"""
SnipOCR – Entry point.
Bootstraps the application: creates the Tk root, wires everything, and runs.
RapidOCR runs in-process — no external server needed.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import warnings

import ctypes
if sys.platform == "win32":
    try:
        # Set process priority to ABOVE_NORMAL (0x00008000) to prevent Windows Scheduler
        # from throttling background/hidden window tasks on the CPU.
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00008000)
    except Exception:
        pass

os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", message=".*np\\.object.*")

import tkinter as tk

from src.app import SnipOCRApp
from src.config import (
    get_pics_dir,
)

_logger = logging.getLogger("snipocr.main")

# ── Fault handler file handle (kept alive to avoid GC) ───────────────────────
_faulthandler_file: object = None  # object type to avoid unused-variable warnings


def _setup_logging() -> None:
    """Configure logging: console handler + rotating file handler."""
    handlers = [
        logging.StreamHandler(sys.stderr),
    ]

    # File handler — best-effort; non-writable filesystem must not crash startup
    try:
        fh = logging.FileHandler("snipocr.log", mode="a", encoding="utf-8")
        fh.setLevel(logging.INFO)
        handlers.append(fh)
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _install_crash_hooks() -> None:
    """Install faulthandler + excepthooks so crashes are captured in crash.log."""
    global _faulthandler_file

    # ── faulthandler (native crashes, SIGABRT, etc.) ────────────────────────
    try:
        _faulthandler_file = open("crash.log", mode="a", encoding="utf-8")
        faulthandler.enable(file=_faulthandler_file)
    except Exception:
        faulthandler.enable()  # fall back to stderr

    # ── sys.excepthook (unhandled Python exceptions) ───────────────────────
    _original_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        _logger.critical(
            "Unhandled exception (sys.excepthook): %s: %s",
            exc_type.__name__, exc_value,
            exc_info=(exc_type, exc_value, exc_tb),
        )
        if _original_excepthook is not None:
            _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    # ── threading.excepthook (unhandled exceptions in threads) ─────────────
    if hasattr(threading, "excepthook"):

        def _thread_excepthook(args) -> None:
            thread_name = getattr(args.thread, "name", "<unknown>")
            _logger.critical(
                "Unhandled exception in thread '%s': %s: %s",
                thread_name,
                args.exc_type.__name__,
                args.exc_value,
                exc_info=(args.exc_type, args.exc_value, args.exc_tb),
            )

        threading.excepthook = _thread_excepthook


def main() -> None:
    _setup_logging()
    _install_crash_hooks()

    pics_dir = get_pics_dir()
    os.makedirs(pics_dir, exist_ok=True)

    # ── Clean stale files from previous sessions ────────────────────────────
    for filename in os.listdir(pics_dir):
        filepath = os.path.join(pics_dir, filename)
        try:
            if os.path.isfile(filepath) or os.path.islink(filepath):
                os.unlink(filepath)
        except Exception:
            pass

    root = tk.Tk()
    root.withdraw()

    app = SnipOCRApp(root=root, pics_dir=pics_dir)
    app.start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.shutdown()
    except SystemExit:
        pass
    finally:
        app.shutdown()
        logging.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()

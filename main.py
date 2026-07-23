"""
SnipOCR – Entry point.
Bootstraps the application: creates the Tk root, wires everything, and runs.
RapidOCR runs in-process — no external server needed.
"""
from __future__ import annotations

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


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _setup_logging()

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

"""
SnipOCR – Entry point.
Bootstraps the application: creates the Tk root, wires everything, and runs.
"""
from __future__ import annotations

import logging
import os
import sys
import warnings

os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", message=".*np\\.object.*")

import threading

import tkinter as tk

from src.app import SnipOCRApp
from src.config import get_pics_dir


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _forceful_exit() -> None:
    """Last-resort process kill if graceful shutdown hangs."""
    import subprocess
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(os.getpid())],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass
    os._exit(1)


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
        logging.shutdown()
        app.shutdown()
        # ── Watchdog: if shutdown takes >5s, force-kill ────────────
        watchdog = threading.Thread(target=lambda: (
            threading.Event().wait(5) or _forceful_exit()
        ), daemon=True)
        watchdog.start()
        sys.exit(0)
        os._exit(0)


if __name__ == "__main__":
    main()
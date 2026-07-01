"""
SnipOCR – Entry point.
Bootstraps the application: optionally starts the OCR FastAPI server in a
background thread, creates the Tk root, wires everything, and runs.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import warnings

os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", message=".*np\\.object.*")

import tkinter as tk

from src.app import SnipOCRApp
from src.config import (
    OCR_SERVER_AUTO_START,
    OCR_SERVER_HOST,
    OCR_SERVER_PORT,
    get_pics_dir,
    is_port_free,
)

_logger = logging.getLogger("snipocr.main")


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


# ── OCR server lifecycle ─────────────────────────────────────────────────────
_server_thread: threading.Thread | None = None
_server_instance = None  # uvicorn.Server instance, kept for graceful shutdown


def _start_ocr_server() -> None:
    """
    Launch the FastAPI/PaddleOCR server on a background daemon thread.

    Behaviour:
        * If the configured port is already bound (e.g. an external `python
          server.py` is running), we skip starting a second instance and
          reuse the existing one.
        * The server runs in a daemon thread so it dies with the Tk process.
        * The first OCR call (or the explicit preloader in SnipOCRApp) will
          poll /health until the server is ready.
    """
    global _server_thread, _server_instance

    if not OCR_SERVER_AUTO_START:
        _logger.info("OCR_SERVER_AUTO_START=False; expecting an external server.")
        return

    if not is_port_free(OCR_SERVER_HOST, OCR_SERVER_PORT):
        _logger.info(
            "OCR server already running on %s:%d; reusing it.",
            OCR_SERVER_HOST, OCR_SERVER_PORT,
        )
        return

    try:
        # Imported lazily so missing uvicorn/fastapi only blows up if you
        # actually want the bundled server.
        import uvicorn
        # server.py lives at the project root; import it as a module.
        import server as ocr_server_module  # type: ignore[import-not-found]
    except Exception as exc:
        _logger.error("Failed to import OCR server module: %s", exc)
        return

    config = uvicorn.Config(
        ocr_server_module.app,
        host=OCR_SERVER_HOST,
        port=OCR_SERVER_PORT,
        log_level="warning",
        lifespan="on",
        # Don't open a browser / show the startup banner.
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    _server_instance = server

    def _run() -> None:
        try:
            _logger.info(
                "Starting OCR server on http://%s:%d (background thread)...",
                OCR_SERVER_HOST, OCR_SERVER_PORT,
            )
            server.run()
        except Exception as exc:
            _logger.exception("OCR server thread crashed: %s", exc)

    _server_thread = threading.Thread(
        target=_run,
        name="ocr-server",
        daemon=True,
    )
    _server_thread.start()
    _logger.info("OCR server thread launched.")


def _stop_ocr_server() -> None:
    """Signal the embedded uvicorn server to shut down (best-effort)."""
    global _server_instance
    if _server_instance is None:
        return
    try:
        _server_instance.should_exit = True
    except Exception:
        pass


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

    # ── Boot the OCR server in the background before the UI starts ──────────
    _start_ocr_server()

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
        _stop_ocr_server()
        # ── Watchdog: if shutdown takes >5s, force-kill ────────────
        watchdog = threading.Thread(target=lambda: (
            threading.Event().wait(5) or _forceful_exit()
        ), daemon=True)
        watchdog.start()
        sys.exit(0)
        os._exit(0)


if __name__ == "__main__":
    main()

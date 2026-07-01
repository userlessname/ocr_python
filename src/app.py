"""
SnipOCR – Application orchestrator.
Wires event bus, state machine, hotkey, overlay, OCR engine, tray, indicator.
"""
from __future__ import annotations

import atexit
import logging
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import tkinter as tk

from src.config import get_pics_dir
from src.core.bus import EventBus
from src.core.state import StateMachine, AppState
from src.core.hotkey import HotkeyListener
from src.core.processor import ImageProcessor
from src.engine.base import BaseOCREngine
from src.engine.remote_engine import RemoteOCREngine
from src.overlay.snipping import SnippingOverlay
from src.ui.tray import TrayController
from src.ui.indicator import OCRIndicator
from src.platform.windows import (
    register_shutdown_listener,
    set_dpi_awareness,
)

_logger = logging.getLogger(__name__)


class SnipOCRApp:
    """
    Main application class. Owns all components and coordinates the
    hotkey → overlay → OCR → save lifecycle.
    """

    def __init__(self, root: tk.Tk, pics_dir: Optional[str] = None):
        self._root = root
        self._pics_dir = pics_dir or get_pics_dir()

        # ── Core infrastructure ───────────────────────────────────────────────
        self._bus = EventBus()
        self._state_machine = StateMachine(on_transition=self._on_state_transition)
        self._processor = ImageProcessor(self._pics_dir)

        # ── OCR engine (HTTP client for the FastAPI server) ──────────────────
        self._ocr_engine: BaseOCREngine = RemoteOCREngine()
        self._ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")

        # ── UI components ─────────────────────────────────────────────────────
        self._indicator = OCRIndicator(root)
        self._tray = TrayController(on_capture=self.request_snipping, on_exit=self.shutdown)
        self._hotkey = HotkeyListener(on_trigger=self.request_snipping)

        # ── Shutdown flag ─────────────────────────────────────────────────────
        self._shutdown_requested = False

        # ══ Subscribe bus events ══════════════════════════════════════════════

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the application (hotkey, tray, preload models)."""
        set_dpi_awareness()
        self._hotkey.start()

        # Start tray in background thread
        tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        tray_thread.start()

        # Preload models in background
        threading.Thread(target=self._preload_models, daemon=True).start()

        # Register exit handlers
        atexit.register(self.shutdown)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        register_shutdown_listener(self.shutdown)

        _logger.info("SnipOCR started. Press Pause to capture.")

    def request_snipping(self) -> None:
        """Request a new snipping session (thread-safe)."""
        self._root.after(0, self._try_start_snipping)

    def shutdown(self, signum=None, frame=None) -> None:
        """Graceful shutdown – stop all components.
        Every sub-step is isolated in try/except so one failure does not
        prevent the next component from being cleaned up.
        """
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        _logger.info("Shutting down SnipOCR...")

        # ── Hotkey listener ───────────────────────────────────────────────────
        try:
            self._hotkey.stop()
        except Exception as exc:
            _logger.warning("Error stopping hotkey: %s", exc)

        # ── System tray icon ──────────────────────────────────────────────────
        try:
            self._tray.stop()
        except Exception as exc:
            _logger.warning("Error stopping tray: %s", exc)

        # ── OCR engine (heavy model unload) ───────────────────────────────────
        try:
            self._ocr_engine.unload()
        except Exception as exc:
            _logger.warning("Error unloading OCR engine: %s", exc)

        # ── Thread pool executor – try graceful, then force ──────────────────
        try:
            self._ocr_executor.shutdown(wait=True, timeout=2)
        except Exception:
            _logger.warning("Executor did not finish within 2 s; forcing shutdown.")
            self._ocr_executor.shutdown(wait=False)

        # ── Event bus ─────────────────────────────────────────────────────────
        try:
            self._bus.clear()
        except Exception as exc:
            _logger.warning("Error clearing bus: %s", exc)

        # ── Tk root ───────────────────────────────────────────────────────────
        try:
            self._root.quit()
        except Exception:
            pass
        try:
            self._root.destroy()
        except Exception:
            pass

    # ── Internal: State machine ──────────────────────────────────────────────

    def _on_state_transition(self, old: AppState, new: AppState) -> None:
        """React to state changes for UI updates."""
        if new == AppState.PROCESSING:
            self._tray.set_busy()
            self._indicator.show()
        elif new == AppState.IDLE:
            self._tray.set_idle()
            self._indicator.hide()

    # ── Internal: Snipping flow ──────────────────────────────────────────────

    def _try_start_snipping(self) -> None:
        """Start snipping if in IDLE state."""
        if not self._state_machine.transition_to(AppState.SNIPPING):
            _logger.debug("Snipping ignored (not IDLE).")
            return

        overlay = SnippingOverlay(
            parent_root=self._root,
            on_result=self._on_image_captured,
        )
        try:
            overlay.start()
        except Exception as e:
            _logger.exception("Failed to start snipping overlay")
            self._on_image_captured(None)

    def _on_image_captured(self, image) -> None:
        """Handle the snipping overlay result."""
        if image is None:
            # User cancelled
            self._state_machine.reset()
            self._hotkey.unblock()
            self._tray.set_idle()
            return

        if not self._state_machine.transition_to(AppState.PROCESSING):
            _logger.warning("Could not transition to PROCESSING state.")
            return

        self._ocr_executor.submit(self._ocr_worker, image)

    # ── Internal: OCR pipeline ───────────────────────────────────────────────

    def _ocr_worker(self, image) -> None:
        """Run OCR in background thread, then schedule result on main thread."""
        try:
            text = self._ocr_engine.recognize(image)
            self._processor.save_image(image)
            self._processor.save_text(text)
            _logger.info(f"OCR completed ({len(text)} chars).")
            self._root.after(0, self._on_ocr_success, text)
        except Exception as e:
            _logger.exception("OCR failed")
            self._root.after(0, self._on_ocr_error, e)

    def _on_ocr_success(self, text: str) -> None:
        """Called on main thread after successful OCR."""
        self._state_machine.transition_to(AppState.IDLE)
        self._hotkey.unblock()

    def _on_ocr_error(self, error: Exception) -> None:
        """Called on main thread after failed OCR."""
        _logger.error(f"OCR Error: {error}")
        self._state_machine.reset()
        self._hotkey.unblock()

    # ── Internal: Model preloading ───────────────────────────────────────────

    def _preload_models(self) -> None:
        """Preload OCR models in background so first capture is fast."""
        try:
            _logger.info("Preloading OCR models...")
            self._ocr_engine.load()
            _logger.info("Models preloaded.")
        except Exception as e:
            _logger.warning(f"Model preloading failed (will load on demand): {e}")

    # ── Internal: Signal handling ────────────────────────────────────────────

    def _signal_handler(self, signum, frame) -> None:
        self.shutdown()

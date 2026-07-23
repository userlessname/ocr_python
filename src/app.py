"""
SnipOCR – Application orchestrator.
Wires event bus, state machine, hotkey, overlay, OCR engine, tray, indicator.
"""
from __future__ import annotations

import atexit
import logging
import signal
import threading
import time
from typing import Optional

import tkinter as tk

from src.config import get_pics_dir
from src.core.bus import EventBus
from src.core.state import StateMachine, AppState
from src.core.hotkey import HotkeyListener
from src.core.processor import ImageProcessor
from src.engine.base import BaseOCREngine
from src.engine.local_engine import LocalOCREngine, shutdown_engine
from src.overlay.snipping import SnippingOverlay
from src.ui.tray import TrayController
from src.ui.indicator import OCRIndicator
from src.platform.windows import (
    register_shutdown_listener,
    set_dpi_awareness,
)

_logger = logging.getLogger(__name__)

# Maximum seconds to wait for models to load before giving up
MODEL_LOAD_TIMEOUT = 120
# Maximum seconds for a single OCR call before watchdog resets state
OCR_WATCHDOG_SECONDS = 60


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

        # ── OCR engine (RapidOCR in-process) ─────────────────────────────────
        self._ocr_engine: BaseOCREngine = LocalOCREngine()
        self._ocr_ready = threading.Event()  # set when models are loaded
        self._ocr_thread: Optional[threading.Thread] = None
        self._ocr_lock = threading.Lock()  # guard _ocr_thread

        # ── UI components ─────────────────────────────────────────────────────
        self._indicator = OCRIndicator(root)
        self._tray = TrayController(on_capture=self.request_snipping, on_exit=self.shutdown)
        self._hotkey = HotkeyListener(on_trigger=self.request_snipping)

        # ── Shutdown flag ─────────────────────────────────────────────────────
        self._shutdown_requested = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the application (hotkey, tray, preload models)."""
        set_dpi_awareness()
        self._hotkey.start()

        # Start tray in background thread
        tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        tray_thread.start()

        # Preload RapidOCR models in background
        threading.Thread(target=self._preload_models, daemon=True).start()

        # Register exit handlers
        atexit.register(self.shutdown)
        atexit.register(shutdown_engine)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        register_shutdown_listener(self.shutdown)

        _logger.info("SnipOCR started. Press Pause to capture.")

    def request_snipping(self) -> None:
        """Request a new snipping session (thread-safe)."""
        self._root.after(0, self._try_start_snipping)

    def shutdown(self, signum=None, frame=None) -> None:
        """Graceful shutdown – stop all components."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        _logger.info("Shutting down SnipOCR...")

        # Signal OCR thread to stop
        self._ocr_ready.set()

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

        # ── Wait for OCR thread to finish ────────────────────────────────────
        with self._ocr_lock:
            ocr_thread = self._ocr_thread
        if ocr_thread is not None and ocr_thread.is_alive():
            _logger.info("Waiting for OCR thread to finish...")
            ocr_thread.join(timeout=3)

        # ── OCR engine (RapidOCR model unload) ───────────────────────────────
        try:
            self._ocr_engine.unload()
        except Exception as exc:
            _logger.warning("Error unloading OCR engine: %s", exc)

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
            self._state_machine.reset()
            self._hotkey.unblock()
            self._tray.set_idle()
            return

        if not self._state_machine.transition_to(AppState.PROCESSING):
            _logger.warning("Could not transition to PROCESSING state.")
            return

        # Check if another OCR is already running
        with self._ocr_lock:
            if self._ocr_thread is not None and self._ocr_thread.is_alive():
                _logger.warning("OCR already in progress, skipping new capture.")
                self._state_machine.reset()
                self._hotkey.unblock()
                return
            self._ocr_thread = threading.Thread(
                target=self._ocr_worker,
                args=(image,),
                daemon=True,
                name="ocr-worker",
            )
            self._ocr_thread.start()

    # ── Internal: OCR pipeline ───────────────────────────────────────────────

    def _wait_for_models(self) -> bool:
        """Wait for the preloader to finish loading RapidOCR models.

        Returns True if models are ready, False on timeout.
        """
        if self._ocr_ready.is_set():
            return True
        _logger.info("Waiting for RapidOCR models to load...")
        if not self._ocr_ready.wait(timeout=MODEL_LOAD_TIMEOUT):
            _logger.error("Model loading timed out after %ds!", MODEL_LOAD_TIMEOUT)
            return False
        return True

    def _ocr_worker(self, image) -> None:
        """Run RapidOCR in background thread, then dispatch to main thread."""
        try:
            # Wait for models if still loading
            if not self._wait_for_models():
                self._root.after(0, self._on_ocr_error, RuntimeError("Model loading timed out"))
                return

            _logger.info("OCR starting on %dx%d image...", image.width, image.height)

            # Start watchdog timer on main thread
            self._root.after(OCR_WATCHDOG_SECONDS * 1000, self._ocr_watchdog)

            t0 = time.time()
            text = self._ocr_engine.recognize(image)
            elapsed = time.time() - t0

            _logger.info("OCR completed in %.1fs (%d chars).", elapsed, len(text))

            # Clipboard first: the user gets the OCR result as early as possible.
            # The PNG disk save can take 100ms+ on large snips, so it runs after.
            self._processor.save_text(text)
            self._processor.save_image(image)
            self._root.after(0, self._on_ocr_success, text)
        except Exception as e:
            _logger.exception("OCR failed: %s", e)
            self._root.after(0, self._on_ocr_error, e)

    def _ocr_watchdog(self) -> None:
        """Check if OCR is stuck and recover if needed."""
        if self._state_machine.state != AppState.PROCESSING:
            return  # already done
        _logger.error("OCR watchdog fired — OCR seems stuck, resetting state.")
        self._state_machine.reset()
        self._hotkey.unblock()
        self._tray.set_idle()
        self._indicator.hide()

    def _on_ocr_success(self, text: str) -> None:
        """Called on main thread after successful OCR."""
        self._state_machine.transition_to(AppState.IDLE)
        self._hotkey.unblock()

    def _on_ocr_error(self, error: Exception) -> None:
        """Called on main thread after failed OCR."""
        _logger.error("OCR Error: %s", error)
        self._state_machine.reset()
        self._hotkey.unblock()

    # ── Internal: Model preloading ───────────────────────────────────────────

    def _preload_models(self) -> None:
        """Preload RapidOCR models in background so first capture is fast."""
        try:
            _logger.info("Preloading RapidOCR models (this may take a few seconds)...")
            t0 = time.time()
            self._ocr_engine.load()
            elapsed = time.time() - t0
            _logger.info("RapidOCR models loaded in %.1fs.", elapsed)
            self._ocr_ready.set()
        except Exception as e:
            _logger.exception("Model preloading FAILED: %s", e)
            self._ocr_ready.set()  # Set anyway so OCR worker doesn't hang forever

    # ── Internal: Signal handling ────────────────────────────────────────────

    def _signal_handler(self, signum, frame) -> None:
        self.shutdown()

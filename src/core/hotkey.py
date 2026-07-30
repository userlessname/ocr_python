"""
Pause-key hotkey listener with debounce protection.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from pynput import keyboard

from src.config import HOTKEY_DEBOUNCE_INTERVAL, HOTKEY_SESSION_RECOVERY

_logger = logging.getLogger(__name__)


class HotkeyListener:
    """
    Listens for a single Pause key press and fires `on_trigger()` exactly once.
    Includes debounce (phantom-key suppression) and session-recovery protection.
    """

    def __init__(self, on_trigger: Optional[Callable] = None):
        self._on_trigger = on_trigger
        self._listener: Optional[keyboard.Listener] = None
        self._lock = threading.Lock()
        self._blocked: bool = False
        self._last_pause_press: float = 0.0
        self._last_trigger: float = 0.0

    def __del__(self) -> None:
        """Safety net: ensure the listener is stopped on garbage collection."""
        try:
            self.stop()
        except Exception:
            pass

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()
        _logger.info("Hotkey listener started (Pause key).")

    def stop(self) -> None:
        """Idempotent, exception-safe stop."""
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception as exc:
                _logger.warning("Error stopping hotkey listener: %s", exc)
            else:
                _logger.info("Hotkey listener stopped.")

    def unblock(self) -> None:
        """Re-enable the listener after a trigger has been handled."""
        with self._lock:
            self._blocked = False
            now = time.time()
            self._last_trigger = now
            self._last_pause_press = now  # also flush any queued phantom events

    def _on_press(self, key) -> None:
        try:
            if key != keyboard.Key.pause:
                return
        except AttributeError:
            return

        now = time.time()

        with self._lock:
            if self._blocked:
                return
            if now - self._last_trigger < HOTKEY_SESSION_RECOVERY:
                return
            if now - self._last_pause_press < HOTKEY_DEBOUNCE_INTERVAL:
                return
            self._last_pause_press = now
            self._last_trigger = now
            self._blocked = True

        self._fire()

    def _fire(self) -> None:
        if self._on_trigger:
            try:
                self._on_trigger()
            except Exception as e:
                _logger.exception(f"Hotkey trigger handler error: {e}")
        else:
            _logger.warning("No on_trigger callback configured.")

"""
System tray icon with right-click menu (Capture Now / Exit).
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

from src.config import TRAY_TOOLTIP_IDLE, TRAY_TOOLTIP_BUSY


def _create_tray_icon(color: tuple) -> Image.Image:
    """Create a 64x64 RGBA icon with a filled circle and letter 'T'."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=(*color, 255))
    draw.text((size // 2 - 8, size // 2 - 14), "T", fill=(255, 255, 255, 255))
    return img


def _idle_icon() -> Image.Image:
    return _create_tray_icon((30, 144, 255))  # blue


def _busy_icon() -> Image.Image:
    return _create_tray_icon((255, 140, 0))  # orange


class TrayController:
    """
    Manages the system tray icon lifecycle and appearance.
    Safe to call set_busy() / set_idle() from any thread.
    """

    def __init__(self, on_capture: Optional[Callable] = None, on_exit: Optional[Callable] = None):
        self._on_capture = on_capture
        self._on_exit = on_exit
        self._icon: Optional[Icon] = None
        self._lock = threading.Lock()
        self._busy = False

    def __del__(self) -> None:
        """Safety net: ensure tray icon is removed on garbage collection."""
        self.stop()

    def run(self) -> None:
        """Start the tray icon (blocking – runs its own message loop)."""
        icon = self._build_icon()
        with self._lock:
            self._icon = icon
        icon.run()
        with self._lock:
            self._icon = None

    def stop(self) -> None:
        """Stop the tray icon and remove it from the system tray."""
        with self._lock:
            if self._icon is not None:
                # ── Hide before stopping to prevent ghost icon ─────
                try:
                    self._icon.visible = False
                except Exception:
                    pass
                try:
                    self._icon.stop()
                except Exception:
                    pass
                try:
                    self._icon.remove()
                except Exception:
                    pass
                self._icon = None

    def set_busy(self) -> None:
        """Switch to the busy (orange) icon."""
        with self._lock:
            self._busy = True
            if self._icon is not None:
                self._icon.icon = _busy_icon()
                self._icon.title = TRAY_TOOLTIP_BUSY

    def set_idle(self) -> None:
        """Switch to the idle (blue) icon."""
        with self._lock:
            self._busy = False
            if self._icon is not None:
                self._icon.icon = _idle_icon()
                self._icon.title = TRAY_TOOLTIP_IDLE

    def _build_icon(self) -> Icon:
        menu = Menu(
            MenuItem("Capture Now", self._on_capture_clicked),
            Menu.SEPARATOR,
            MenuItem("Exit", self._on_exit),
        )
        return Icon(
            "snip_ocr",
            _idle_icon(),
            TRAY_TOOLTIP_IDLE,
            menu,
        )

    def _on_capture_clicked(self) -> None:
        if self._on_capture:
            self._on_capture()

    def _on_exit(self, icon: Icon) -> None:
        icon.stop()
        if self._on_exit:
            self._on_exit()

"""
Windows-specific platform abstractions.
Encapsulates all ctypes.windll.* calls in one place.
Includes console control handler for forced shutdown detection.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from threading import Event
from typing import Callable, Optional, Tuple

_DPI_SET = False

# ── Console Control Handler ─────────────────────────────────────────────────
_CTRL_C_EVENT = 0
_CTRL_BREAK_EVENT = 1
_CTRL_CLOSE_EVENT = 2
_CTRL_LOGOFF_EVENT = 5
_CTRL_SHUTDOWN_EVENT = 6

_shutdown_listeners: list[Callable[[], None]] = []
_console_handler_installed = False


def register_shutdown_listener(fn: Callable[[], None]) -> None:
    """Register a callback to be invoked when the console is closing."""
    global _console_handler_installed
    _shutdown_listeners.append(fn)
    if not _console_handler_installed:
        _install_console_handler()
        _console_handler_installed = True


def _install_console_handler() -> None:
    """Install a ConsoleCtrlHandler that fires on console close/logoff/shutdown."""
    kernel32 = ctypes.windll.kernel32

    handler_type = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def handler(dw_ctrl_type: int) -> bool:
        """Called by Windows on console events."""
        if dw_ctrl_type in (
            _CTRL_CLOSE_EVENT,
            _CTRL_LOGOFF_EVENT,
            _CTRL_SHUTDOWN_EVENT,
        ):
            for fn in _shutdown_listeners:
                try:
                    fn()
                except Exception:
                    pass
            return True  # We handled it; do not let the default handler run
        return False  # Let default handler process CTRL+C, CTRL+BREAK etc.

    c_handler = handler_type(handler)
    if not kernel32.SetConsoleCtrlHandler(c_handler, 1):
        logging.getLogger(__name__).warning("Failed to install console control handler.")
    # Keep the callback alive (prevent garbage collection)
    _install_console_handler._handler = c_handler


_logger = logging.getLogger(__name__)


def set_dpi_awareness() -> None:
    """Set process DPI awareness (PerMonitorV2 → fallback system DPI aware)."""
    global _DPI_SET
    if _DPI_SET:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_SET = True


def get_screen_size() -> Tuple[int, int]:
    """Return (width, height) of the primary screen."""
    set_dpi_awareness()
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_cursor_pos() -> Tuple[int, int]:
    """Return (x, y) of the current cursor position."""
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def set_window_ex_style(hwnd: int, extra_flags: int) -> None:
    """Add extra flags to the window's extended style."""
    GWL_EXSTYLE = -20
    try:
        cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur | extra_flags)
    except Exception:
        pass


def set_layered_alpha(hwnd: int, alpha: int) -> None:
    """Set the window's layered transparency (0-255)."""
    try:
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 0x00000002)
    except Exception:
        pass


def force_topmost(hwnd: int) -> None:
    """Force a window to topmost position."""
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    try:
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def get_monitor_at_point(x: int, y: int) -> Optional[dict]:
    """
    Find the monitor containing (x, y).
    Uses screeninfo to iterate monitors; returns None if no match.
    """
    try:
        from screeninfo import get_monitors
        monitors = get_monitors()
        for m in monitors:
            if m.x <= x < m.x + m.width and m.y <= y < m.y + m.height:
                return {
                    "x": m.x, "y": m.y,
                    "width": m.width, "height": m.height,
                    "name": m.name,
                    "is_primary": m.is_primary,
                }
        # fallback: primary monitor
        for m in monitors:
            if m.is_primary:
                return {
                    "x": m.x, "y": m.y,
                    "width": m.width, "height": m.height,
                    "name": m.name,
                    "is_primary": True,
                }
        if monitors:
            m = monitors[0]
            return {
                "x": m.x, "y": m.y,
                "width": m.width, "height": m.height,
                "name": m.name,
                "is_primary": m.is_primary,
            }
    except Exception:
        pass
    return None


def get_primary_monitor() -> dict:
    """
    Return the primary monitor dict. Fallback to full desktop if screeninfo fails.
    """
    try:
        from screeninfo import get_monitors
        for m in get_monitors():
            if m.is_primary:
                return {
                    "x": m.x, "y": m.y,
                    "width": m.width, "height": m.height,
                    "name": m.name,
                }
        m = get_monitors()[0]
        return {
            "x": m.x, "y": m.y,
            "width": m.width, "height": m.height,
            "name": m.name,
        }
    except Exception:
        w, h = get_screen_size()
        return {"x": 0, "y": 0, "width": w, "height": h, "name": "default"}

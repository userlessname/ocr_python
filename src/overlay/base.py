"""
Base overlay window using Tkinter Toplevel + Windows API.
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional

from src.platform.windows import (
    set_window_ex_style,
    set_layered_alpha,
    force_topmost,
)


class BaseOverlay:
    """
    Manages a borderless, topmost, layered Toplevel window.

    Subclasses implement layout and event handling.
    """

    WS_EX_LAYERED = 0x00080000
    WS_EX_TOPMOST = 0x00000008

    def __init__(self, parent_root: tk.Tk):
        self.parent_root = parent_root
        self.root: Optional[tk.Toplevel] = None
        self._cleaned_up = False

    def __del__(self) -> None:
        """Safety net: destroy the overlay window if GC reclaims us."""
        self.close()

    def create(self, width: int, height: int, x: int, y: int) -> None:
        """Create and show the overlay window."""
        self.root = tk.Toplevel(self.parent_root)
        self.root.withdraw()
        self._configure_window(width, height, x, y)
        self._apply_window_styles()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.update_idletasks()
        self.root.update()

    def close(self) -> None:
        """Destroy the overlay window."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        try:
            self.root.grab_release()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.root = None

    def _configure_window(self, width: int, height: int, x: int, y: int) -> None:
        """Set geometry, overrideredirect, topmost, cursor, and background."""
        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass
        self.root.overrideredirect(True)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.attributes("-topmost", True)
        self.root.config(cursor="cross", bg="black")

    def _apply_window_styles(self) -> None:
        """Apply Windows layered + topmost extended styles."""
        hwnd = self._hwnd()
        if hwnd is None:
            return
        flags = self.WS_EX_LAYERED | self.WS_EX_TOPMOST
        set_window_ex_style(hwnd, flags)
        set_layered_alpha(hwnd, 255)

    def _force_topmost(self) -> None:
        hwnd = self._hwnd()
        if hwnd is not None:
            force_topmost(hwnd)

    def _grab_input(self) -> None:
        try:
            self.root.grab_set()
        except Exception:
            pass

    def _hwnd(self) -> Optional[int]:
        try:
            return self.root.winfo_id()
        except Exception:
            return None

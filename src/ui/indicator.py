"""
Floating "OCR" indicator window shown during processing.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Optional

import tkinter as tk

from src.config import (
    FONT_SIZE,
    INDICATOR_TEXT,
    INDICATOR_BG,
    INDICATOR_FG,
    INDICATOR_PAD_X,
    INDICATOR_PAD_Y,
    INDICATOR_MARGIN_RIGHT,
    INDICATOR_MARGIN_TOP,
    INDICATOR_ALPHA,
)
from src.platform.windows import get_screen_size, set_window_ex_style, set_layered_alpha

_logger = logging.getLogger(__name__)


class OCRIndicator:
    """
    Displays a small, always-on-top "OCR" label in the top-right corner
    while processing is in progress.
    """

    def __init__(self, parent_root: tk.Tk):
        self._parent_root = parent_root
        self._toplevel: Optional[tk.Toplevel] = None

    def show(self) -> None:
        if self._toplevel is not None:
            return
        self._create_and_show()

    def hide(self) -> None:
        if self._toplevel is None:
            return
        try:
            self._toplevel.destroy()
        except Exception:
            pass
        self._toplevel = None

    def _create_and_show(self) -> None:
        screen_width, screen_height = get_screen_size()

        # Measure text size with a temporary label
        temp = tk.Toplevel(self._parent_root)
        temp.withdraw()
        label = tk.Label(temp, text=INDICATOR_TEXT, font=("Segoe UI", FONT_SIZE, "bold"))
        label.update_idletasks()
        text_width = label.winfo_reqwidth()
        text_height = label.winfo_reqheight()
        temp.destroy()

        win_width = text_width + INDICATOR_PAD_X * 2
        win_height = text_height + INDICATOR_PAD_Y * 2
        x = screen_width - win_width - INDICATOR_MARGIN_RIGHT
        y = INDICATOR_MARGIN_TOP

        self._toplevel = tk.Toplevel(self._parent_root)
        self._toplevel.withdraw()
        self._toplevel.overrideredirect(True)
        self._toplevel.geometry(f"{win_width}x{win_height}+{x}+{y}")
        self._toplevel.attributes("-topmost", True)
        self._toplevel.attributes("-transparentcolor", "black")
        self._toplevel.config(bg="black")
        self._toplevel.lift()

        label = tk.Label(
            self._toplevel,
            text=INDICATOR_TEXT,
            font=("Segoe UI", FONT_SIZE, "bold"),
            fg=INDICATOR_FG,
            bg=INDICATOR_BG,
            padx=INDICATOR_PAD_X,
            pady=INDICATOR_PAD_Y,
        )
        label.pack(fill="both", expand=True)

        # Apply Windows styles: layered + transparent + tool window
        try:
            hwnd = self._toplevel.winfo_id()
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            flags = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            set_window_ex_style(hwnd, flags)
            set_layered_alpha(hwnd, INDICATOR_ALPHA)
        except Exception:
            pass

        self._toplevel.deiconify()

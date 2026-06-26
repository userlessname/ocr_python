"""
On-screen OCR indicator: shows "OCR" text at top-right while processing.
Uses tkinter for a small always-on-top overlay window.
Click-through enabled so it doesn't interfere with user interaction.
"""

import tkinter as tk
import threading
import ctypes
from utils.helpers import get_screen_size

# Window constants
FONT_SIZE = 24
TEXT = "OCR"
BG_COLOR = "#1a1a1a"  # Dark semi-transparent background
FG_COLOR = "#ffffff"   # White text
PAD_X = 12
PAD_Y = 6
MARGIN_RIGHT = 20
MARGIN_TOP = 15

_root = None
_root_lock = threading.Lock()


def _create_and_show():
    """Create the tkinter window and position it at top-right."""
    global _root

    screen_width, screen_height = get_screen_size()

    # Create a temporary label to measure text size
    dummy = tk.Tk()
    dummy.withdraw()
    dummy.update_idletasks()
    label = tk.Label(dummy, text=TEXT, font=("Segoe UI", FONT_SIZE, "bold"))
    label.update_idletasks()
    text_width = label.winfo_reqwidth()
    text_height = label.winfo_reqheight()
    dummy.destroy()

    win_width = text_width + PAD_X * 2
    win_height = text_height + PAD_Y * 2
    x = screen_width - win_width - MARGIN_RIGHT
    y = MARGIN_TOP

    # Create the overlay window
    _root = tk.Tk()
    _root.withdraw()
    _root.overrideredirect(True)  # No title bar
    _root.geometry(f"{win_width}x{win_height}+{x}+{y}")
    _root.attributes("-topmost", True)
    _root.attributes("-transparentcolor", "black")  # Black = transparent
    _root.config(bg="black")
    _root.lift()

    # Create the label with rounded-rectangle feel via padding
    label = tk.Label(
        _root,
        text=TEXT,
        font=("Segoe UI", FONT_SIZE, "bold"),
        fg=FG_COLOR,
        bg=BG_COLOR,
        padx=PAD_X,
        pady=PAD_Y,
    )
    label.pack(fill="both", expand=True)

    # Make window click-through via Windows API
    try:
        hwnd = _root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        # 70% opacity
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 180, 0x00000002)
    except Exception:
        pass

    _root.deiconify()
    _root.mainloop()


def show():
    """Show the OCR indicator overlay (non-blocking)."""
    global _root
    with _root_lock:
        if _root is not None:
            return  # Already showing

    thread = threading.Thread(target=_create_and_show, daemon=True)
    thread.start()


def hide():
    """Hide the OCR indicator overlay."""
    global _root
    with _root_lock:
        if _root is None:
            return
        try:
            _root.quit()
            _root.destroy()
        except Exception:
            pass
        _root = None

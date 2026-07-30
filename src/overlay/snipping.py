"""
Region-selection overlay. User drags to select a screen area for OCR.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import tkinter as tk
from PIL import Image, ImageGrab, ImageTk

from src.config import (
    SELECTION_MIN_SIZE,
    HINT_TEXT,
    HINT_FONT,
    HINT_COLOR,
    SELECTION_COLOR,
    SELECTION_WIDTH,
    DIM_ALPHA,
)
from src.overlay.base import BaseOverlay
from src.platform.windows import get_cursor_pos, get_monitor_at_point

_logger = logging.getLogger(__name__)


class SnippingOverlay(BaseOverlay):
    """
    Full-screen overlay that lets the user drag a rectangle to select a region.
    Calls `on_result(image)` with the cropped PIL Image, or `None` if cancelled.
    """

    def __init__(self, parent_root: tk.Tk, on_result: Callable):
        super().__init__(parent_root)
        self.on_result = on_result
        self.start_x: Optional[int] = None
        self.start_y: Optional[int] = None
        self.end_x: Optional[int] = None
        self.end_y: Optional[int] = None
        self._canvas: Optional[tk.Canvas] = None
        self._sel_rect: Optional[int] = None
        self._screenshot: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._mon = {"x": 0, "y": 0, "width": 0, "height": 0}

    def start(self) -> None:
        try:
            self._start_impl()
        except Exception as e:
            _logger.exception("Failed to start snipping overlay")
            self._cancel()

    def _start_impl(self) -> None:
        # Guard: if the main window is already gone, abort immediately
        try:
            if not self.parent_root.winfo_exists():
                _logger.warning("Main window gone; aborting snip.")
                self._cancel()
                return
        except Exception:
            self._cancel()
            return

        cursor_x, cursor_y = get_cursor_pos()
        monitor = get_monitor_at_point(cursor_x, cursor_y)
        if monitor is None:
            from src.platform.windows import get_primary_monitor
            monitor = get_primary_monitor()

        self._mon = monitor
        _logger.info(
            f"Opening snipping on {monitor['name']} "
            f"({monitor['width']}x{monitor['height']} at {monitor['x']},{monitor['y']})"
        )

        # Grab only the target monitor's region. Pillow crops inside its C-level
        # grab using virtual-screen offsets, so this avoids the Python-side full
        # virtual-screen crop and the extra screeninfo monitor enumeration.
        self._screenshot = ImageGrab.grab(
            bbox=(
                monitor["x"],
                monitor["y"],
                monitor["x"] + monitor["width"],
                monitor["y"] + monitor["height"],
            ),
            all_screens=True,
        )

        self.create(monitor["width"], monitor["height"], monitor["x"], monitor["y"])

        self._canvas = tk.Canvas(
            self.root,
            width=monitor["width"],
            height=monitor["height"],
            highlightthickness=0,
            bg="black",
        )
        self._canvas.pack()

        self._photo = ImageTk.PhotoImage(self._dimmed_screenshot())
        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

        self._canvas.create_text(
            monitor["width"] // 2, 30,
            text=HINT_TEXT, fill=HINT_COLOR, font=HINT_FONT,
        )

        self.root.bind("<Button-1>", self._on_mouse_down)
        self.root.bind("<B1-Motion>", self._on_mouse_drag)
        self.root.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.root.bind("<Escape>", self._on_escape)

        self._force_topmost()
        self._grab_input()

    def _dimmed_screenshot(self) -> Image.Image:
        """Return a darkened copy of the screenshot for the overlay background."""
        darkened = self._screenshot.copy()
        darkened = Image.blend(
            darkened,
            Image.new("RGB", darkened.size, (0, 0, 0)),
            DIM_ALPHA,
        )
        if darkened.size != (self._mon["width"], self._mon["height"]):
            darkened = darkened.resize(
                (self._mon["width"], self._mon["height"]),
                Image.Resampling.LANCZOS,
            )
        return darkened

    def _crop_selection(self) -> Image.Image:
        """Crop the original screenshot to the selected rectangle."""
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)
        sw, sh = self._screenshot.size
        sx = sw / self._mon["width"]
        sy = sh / self._mon["height"]
        return self._screenshot.crop((
            int(x1 * sx), int(y1 * sy),
            int(x2 * sx), int(y2 * sy),
        ))

    def _is_valid_selection(self) -> bool:
        if None in (self.start_x, self.end_x):
            return False
        w = abs(self.end_x - self.start_x)
        h = abs(self.end_y - self.start_y)
        return w > SELECTION_MIN_SIZE and h > SELECTION_MIN_SIZE

    def _on_mouse_down(self, event: tk.Event) -> None:
        self.start_x = event.x
        self.start_y = event.y
        self.end_x = event.x
        self.end_y = event.y
        self._sel_rect = self._canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline=SELECTION_COLOR, width=SELECTION_WIDTH,
        )

    def _on_mouse_drag(self, event: tk.Event) -> None:
        self.end_x = event.x
        self.end_y = event.y
        if self._sel_rect is not None:
            self._canvas.coords(
                self._sel_rect,
                self.start_x, self.start_y,
                self.end_x, self.end_y,
            )

    def _on_mouse_up(self, event: tk.Event) -> None:
        self.end_x = event.x
        self.end_y = event.y
        if self._is_valid_selection():
            cropped = self._crop_selection()
            self.close()
            self.on_result(cropped)
        else:
            _logger.info(
                f"Selection too small "
                f"({abs(self.end_x - self.start_x)}x{abs(self.end_y - self.start_y)}), cancelled."
            )
            self._cancel()

    def _on_escape(self, _event: tk.Event) -> None:
        _logger.info("Snipping cancelled by user (ESC).")
        self._cancel()

    def _cancel(self) -> None:
        self.close()
        try:
            self.on_result(None)
        except Exception:
            pass

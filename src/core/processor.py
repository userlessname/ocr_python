"""
Handles saving captured images and OCR results (clipboard + file).
"""
from __future__ import annotations

import os
import time
import logging
from typing import Optional

import pyperclip
from PIL import Image

_logger = logging.getLogger(__name__)


class ImageProcessor:
    """
    Saves captured images to disk and writes OCR text to clipboard + text file.
    """

    def __init__(self, pics_dir: str):
        self.pics_dir = pics_dir
        os.makedirs(pics_dir, exist_ok=True)
        self._timestamp: Optional[str] = None

    def save_image(self, image: Image.Image) -> str:
        """Save the captured image as PNG. Returns the file path."""
        self._timestamp = str(time.time()).replace(".", "")
        image_path = os.path.join(self.pics_dir, f"{self._timestamp}_clipboard_image.png")
        image.save(image_path, "PNG")
        _logger.debug(f"Image saved: {image_path}")
        return image_path

    def save_text(self, text: str) -> str:
        """
        Copy text to clipboard and save to a .txt sidecar file.
        Returns the file path.
        """
        self._copy_to_clipboard(text)
        text_path = os.path.join(self.pics_dir, f"{self._timestamp}_clipboard_text.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text)
        _logger.info(f"OCR result ({len(text)} chars) saved.")
        return text_path

    def cleanup_pics_dir(self) -> None:
        """Remove all files in the pics directory."""
        for f in os.listdir(self.pics_dir):
            path = os.path.join(self.pics_dir, f)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        """Copy text to clipboard with retry logic."""
        for attempt in range(3):
            try:
                pyperclip.copy(text)
                return
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.1)
                else:
                    _logger.error(f"Clipboard copy failed after 3 attempts: {e}")

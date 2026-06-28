"""
PaddleOCR engine implementation (CPU-only on AMD; no native CUDA support).
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
from PIL import Image

from src.config import (
    PADDLE_LANGUAGES,
    PADDLE_USE_GPU,
    PADDLE_DET_DB_THRESH,
    PADDLE_DET_BOX_THRESH,
    PADDLE_REC_BATCH_SIZE,
    PADDLE_PREPROCESS_ENABLE,
    PADDLE_USE_DILATION,
)
from src.core.preprocessor import preprocess
from src.engine.base import BaseOCREngine

_logger = logging.getLogger(__name__)


# ── bbox helper functions ────────────────────────────────────────────────
def _bbox_left_x(bbox):
    """Extract left X coordinate from a PaddleOCR bbox."""
    return bbox[0][0]


def _bbox_top_y(bbox):
    """Extract top Y coordinate from a PaddleOCR bbox."""
    return bbox[0][1]


class PaddleOCREngine(BaseOCREngine):
    """
    Wraps PaddleOCR with lazy loading and thread safety.
    Runs on CPU for AMD GPUs; CUDA would require ``paddlepaddle-gpu``.
    """

    def __init__(self):
        self._ocr = None
        self._lock = threading.Lock()
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            from paddleocr import PaddleOCR

            _logger.info("Loading PaddleOCR (languages=%s, gpu=%s)…",
                         PADDLE_LANGUAGES, PADDLE_USE_GPU)
            start = time.time()
            # ── detection params ───────────────────────────────────────
            det_kwargs = dict(
                det_db_thresh=PADDLE_DET_DB_THRESH,
                det_db_box_thresh=PADDLE_DET_BOX_THRESH,
                use_dilation=PADDLE_USE_DILATION,
                det_db_unclip_ratio=2.5,  # Expand text boxes — catches punctuation, thin chars
            )
            self._ocr = PaddleOCR(
                lang=PADDLE_LANGUAGES[0] if PADDLE_LANGUAGES else "en",
                use_angle_cls=True,
                use_gpu=PADDLE_USE_GPU,
                rec_batch_num=PADDLE_REC_BATCH_SIZE,
                show_log=False,
                **det_kwargs,
            )
            self._loaded = True
            _logger.info("PaddleOCR loaded in %.1fs", time.time() - start)

    def unload(self) -> None:
        with self._lock:
            self._ocr = None
            self._loaded = False
            _logger.info("PaddleOCR unloaded.")

    def recognize(self, image: Image.Image) -> str:
        """Run PaddleOCR and return extracted text as a single string."""
        self.load()

        img = image
        if PADDLE_PREPROCESS_ENABLE:
            img = preprocess(img)

        img_np = np.array(img.convert("RGB"))
        results = self._ocr.ocr(img_np, cls=True)

        return self._assemble_text(results)

    def recognize_with_confidence(self, image: Image.Image) -> tuple[str, float]:
        """
        Run OCR and return (text, avg_confidence).
        Used by CompositeEngine (if ever needed) or for diagnostics.
        """
        self.load()

        img = image
        if PADDLE_PREPROCESS_ENABLE:
            img = preprocess(img)

        img_np = np.array(img.convert("RGB"))
        results = self._ocr.ocr(img_np, cls=True)

        text = self._assemble_text(results)
        confidence = self._avg_confidence(results)
        return text, confidence

    # ── internal helpers ────────────────────────────────────────────────────

    _LINE_Y_TOLERANCE = 15  # max px diff to consider same line

    @staticmethod
    def _assemble_text(results) -> str:
        """
        PaddleOCR returns: list[list[(bbox, (text, conf))]]

        Groups detections by Y-coordinate, sorts LTR, and preserves
        approximate indentation based on the left edge of the bbox.
        """
        if not results or not results[0]:
            return ""

        # Flatten: collect (bbox, text)
        items: list[tuple[list[list[float]], str]] = []
        for block in results:
            if block is None:
                continue
            for item in block:
                if item is None:
                    continue
                try:
                    bbox, (text, _conf) = item
                    if text and text.strip():
                        items.append((bbox, text.strip()))
                except (ValueError, TypeError, IndexError):
                    continue

        if not items:
            return ""

        # Sort by top-Y then left-X
        items.sort(key=lambda x: (_bbox_top_y(x[0]), _bbox_left_x(x[0])))

        # Group into lines by Y-proximity
        lines_grouped: list[list[tuple[list[list[float]], str]]] = []
        current_group: list[tuple[list[list[float]], str]] = [items[0]]
        current_y = _bbox_top_y(items[0][0])

        for bbox, text in items[1:]:
            top_y = _bbox_top_y(bbox)
            if abs(top_y - current_y) < PaddleOCREngine._LINE_Y_TOLERANCE:
                current_group.append((bbox, text))
            else:
                lines_grouped.append(current_group)
                current_group = [(bbox, text)]
                current_y = top_y

        if current_group:
            lines_grouped.append(current_group)

        # Find global minimum left-X for indentation reference
        all_left_x = [_bbox_left_x(bbox) for group in lines_grouped for bbox, _ in group]
        global_min_left = min(all_left_x) if all_left_x else 0.0

        # Compute median character width for indent calculation
        char_widths: list[float] = []
        for bbox, text in items:
            line_width = bbox[1][0] - bbox[0][0]
            if len(text) > 0 and text.strip():
                char_widths.append(line_width / len(text))
        median_char_width = float(np.median(char_widths)) if char_widths else 8.0

        # Assemble final output with indentation
        output_lines: list[str] = []
        for group in lines_grouped:
            # Sort within group left-to-right
            group.sort(key=lambda x: _bbox_left_x(x[0]))
            line_text = " ".join(t for _, t in group)

            # Calculate indentation from the first box in the group
            first_left = _bbox_left_x(group[0][0])
            indent_px = first_left - global_min_left
            indent_spaces = int(round(indent_px / median_char_width)) if median_char_width > 0 else 0

            output_lines.append(" " * indent_spaces + line_text)

        return "\n".join(output_lines)

    @staticmethod
    def _avg_confidence(results) -> float:
        """Compute average confidence over all detected text boxes."""
        confidences = []
        if not results or not results[0]:
            return 0.0
        for block in results:
            if block is None:
                continue
            for item in block:
                if item is None:
                    continue
                try:
                    _, (_text, conf) = item
                    confidences.append(conf)
                except (ValueError, TypeError, IndexError):
                    continue
        return float(np.mean(confidences)) if confidences else 0.0
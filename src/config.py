"""
SnipOCR – Centralized configuration and constants.
All magic numbers, timeouts, colors, and paths live here.
"""
from __future__ import annotations

import os

# ── Selection overlay ─────────────────────────────────────────────────────────
SELECTION_MIN_SIZE = 10
OVERLAY_OPACITY = 255
HINT_TEXT = "Click and drag to select area. Press ESC to cancel."
HINT_FONT = ("Arial", 16, "bold")
HINT_COLOR = "white"
SELECTION_COLOR = "#00ff00"
SELECTION_WIDTH = 2
DIM_ALPHA = 0.4

# ── Floating OCR indicator ────────────────────────────────────────────────────
FONT_SIZE = 24
INDICATOR_TEXT = "OCR"
INDICATOR_BG = "#1a1a1a"
INDICATOR_FG = "#ffffff"
INDICATOR_PAD_X = 12
INDICATOR_PAD_Y = 6
INDICATOR_MARGIN_RIGHT = 20
INDICATOR_MARGIN_TOP = 15
INDICATOR_ALPHA = 180

# ── Tray icon ─────────────────────────────────────────────────────────────────
TRAY_TOOLTIP_IDLE = "Snip & OCR (Pause)"
TRAY_TOOLTIP_BUSY = "\u23f3 OCR processing..."  # hourglass emoji

# ── Hotkey ────────────────────────────────────────────────────────────────────
HOTKEY_DEBOUNCE_INTERVAL = 2.0
HOTKEY_SESSION_RECOVERY = 1.5

# ── OCR engine ────────────────────────────────────────────────────────────────
PADDLE_LANGUAGES = ["en"]
PADDLE_USE_GPU = False
PADDLE_DET_DB_THRESH = 0.2       # Lower threshold to catch more text boxes
PADDLE_DET_BOX_THRESH = 0.1      # Include low-confidence detections too
PADDLE_REC_BATCH_SIZE = 6
PADDLE_PREPROCESS_ENABLE = True   # Our preprocessor (CLAHE + binarize) improves small-text detection for code screenshots
PADDLE_USE_DILATION = True        # Morphological dilation for small/narrow characters

# ── Image Preprocessing (only safe, fast, quality-boosting ops) ──────────────
PREPROCESS_ENABLE = True              # Master switch: ON
PREPROCESS_CLAHE_CLIP_LIMIT = 1.5    # Mild contrast boost — safe & fast (~2ms)
PREPROCESS_CLAHE_GRID_SIZE = 8
PREPROCESS_BILATERAL_D = 0           # DISABLED — can blur small text
PREPROCESS_BILATERAL_SIGMA_COLOR = 75
PREPROCESS_BILATERAL_SIGMA_SPACE = 75
PREPROCESS_SHARPEN_STRENGTH = 0.3    # Gentle unsharp masking — safe, improves edge definition
PREPROCESS_DESKEW_ENABLE = False     # DISABLED — can distort text
PREPROCESS_BINARIZE_ENABLE = True    # Adaptive binarization — best for mixed screenshots
PREPROCESS_BINARIZE_METHOD = "adaptive_gaussian"
PREPROCESS_UPSCALE_FACTOR = 1.5      # Gentle upscale for tiny images
PREPROCESS_UPSCALE_MIN_DIM = (100, 30) # Only upsacle truly tiny scans

# ── Text Post-Processing — SAFE improvements ──────────────────────────────────
POSTPROCESS_ENABLE = True
POSTPROCESS_FIX_COMMON_ERRORS = True   # 0↔O, 1↔l, pipe fixes
POSTPROCESS_COLLAPSE_WHITESPACE = True # Clean up extra spaces
POSTPROCESS_FIX_PUNCTUATION = True     # Period/comma spacing fixes

# ── Paths ─────────────────────────────────────────────────────────────────────
def get_pics_dir() -> str:
    """Return the pics directory path (relative to project root)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "pics")
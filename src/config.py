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

# ── OCR engine tuning ─────────────────────────────────────────────────────────
# Detection runs at native resolution (limit_type="max") instead of upscaling
# every snip to a 960px minimum side. Removes interpolation blur (quality) and
# cuts detection input pixels by up to 40x on wide command-line snips (speed).
DET_LIMIT_TYPE = "max"
DET_LIMIT_SIDE_LEN = 960
DET_BOX_THRESH = 0.55
DET_UNCLIP_RATIO = 1.5
DET_SCORE_MODE = "slow"

# Recognition batch size: more crops per ONNX run call -> less per-call overhead.
REC_BATCH_NUM = 12

# Height-sensitive upscale tiers for tiny text (bigger glyphs -> better rec).
SMALL_TEXT_2X_MAX_HEIGHT = 36
SMALL_TEXT_15X_MAX_HEIGHT = 80
MAX_UPSCALE_DIM = 2800

# Run one dummy det+rec inference at load time so the first real capture does
# not pay ONNX Runtime graph-initialization cost.
ENGINE_WARMUP_ENABLED = True

# Minimum image height (px) for applying the Turkish NLP spell-correction pass.
TURKISH_CORRECTION_MIN_HEIGHT = 60
# Upper bound for the per-session spell-correction cache.
TURKISH_CACHE_MAX_SIZE = 4096

# ── Paths ─────────────────────────────────────────────────────────────────────
def get_pics_dir() -> str:
    """Return the pics directory path (relative to project root)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "pics")

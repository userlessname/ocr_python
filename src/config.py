"""
SnipOCR – Centralized configuration and constants.
All magic numbers, timeouts, colors, and paths live here.
"""
from __future__ import annotations

import os
import socket

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

# ── OCR FastAPI server (consumed by main.py + src/engine/remote_engine.py) ────
OCR_SERVER_HOST = "127.0.0.1"          # Loopback only for the bundled server
OCR_SERVER_PORT = 8000                 # Matches the canonical port from the guide
OCR_SERVER_HEALTH_PATH = "/health"     # Liveness probe used by RemoteOCREngine.load()
OCR_SERVER_STARTUP_TIMEOUT = 120.0     # Generous: covers first-run model download
OCR_SERVER_REQUEST_TIMEOUT = 30.0      # Per-request timeout for /ocr/process
OCR_SERVER_AUTO_START = True           # If True, main.py spawns the server in a thread

# ── Paths ─────────────────────────────────────────────────────────────────────
def get_pics_dir() -> str:
    """Return the pics directory path (relative to project root)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "pics")


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_port_free(host: str, port: int) -> bool:
    """Return True if the TCP port is currently free (safe to bind)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect((host, port))
        except (ConnectionRefusedError, OSError, socket.timeout):
            return True
        return False

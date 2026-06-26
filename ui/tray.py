"""
System tray icon module.
Uses pystray to display an icon and run the event loop.
"""

import threading
import pystray
from pystray import MenuItem, Menu
from utils.helpers import create_tray_icon_idle, create_tray_icon_busy

# Global icon reference for notification functions
_icon = None
_icon_lock = threading.Lock()

TOOLTIP_IDLE = "Snip & OCR (Pause \u00d73)"
TOOLTIP_BUSY = "\u23f3 OCR processing..."


def create_icon(on_capture=None):
    """Create and return a tray icon with menu."""
    icon_image = create_tray_icon_idle()
    menu = Menu(
        MenuItem('Capture Now', lambda: on_capture() if on_capture else None),
        Menu.SEPARATOR,
        MenuItem('Exit', lambda icon: icon.stop())
    )
    icon = pystray.Icon("snip_tool", icon_image, TOOLTIP_IDLE, menu)
    return icon


def set_busy():
    """Switch tray icon to busy state: orange icon + 'OCR processing' tooltip."""
    with _icon_lock:
        if _icon:
            _icon.icon = create_tray_icon_busy()
            _icon.title = TOOLTIP_BUSY


def set_idle():
    """Restore tray icon to idle state: blue icon + normal tooltip."""
    with _icon_lock:
        if _icon:
            _icon.icon = create_tray_icon_idle()
            _icon.title = TOOLTIP_IDLE


def main(on_capture=None):
    """Start the tray icon event loop."""
    global _icon
    icon = create_icon(on_capture=on_capture)
    with _icon_lock:
        _icon = icon
    try:
        icon.run()
    finally:
        with _icon_lock:
            _icon = None

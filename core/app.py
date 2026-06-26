"""
Core module: handles the pause trigger action.
"""

import os
import atexit
from core.processor import ImageProcessor
from core.hotkey import HotkeyManager
from core.inference import shutdown as shutdown_surya

# Determine pics directory relative to this file's location (or absolute)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PICS_DIR = os.path.join(BASE_DIR, '..', 'pics')

_processor = None

# Register Surya cleanup on exit
atexit.register(shutdown_surya)


def get_processor():
    global _processor
    if _processor is None:
        _processor = ImageProcessor(PICS_DIR)
    return _processor

def on_snipped_callback(image):
    processor = get_processor()
    processor.process_image(image)

def start_hotkey_listener():
    manager = HotkeyManager(on_snipped_callback)
    manager.start()

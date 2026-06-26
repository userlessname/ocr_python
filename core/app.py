"""
Core module: provides ImageProcessor singleton (backward compat / tests).
SnippingQueue ve HotkeyManager yönetimi core/snipping.py'ye taşındı.
"""

import os
import atexit
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
        from core.processor import ImageProcessor
        _processor = ImageProcessor(PICS_DIR)
    return _processor
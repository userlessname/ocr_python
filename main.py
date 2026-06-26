import os
import glob
import threading
from core import app
from ui import tray
from ui.overlay import SnippingTool
from utils.helpers import set_sound_enabled

set_sound_enabled(False)

# Clear pics folder on startup
pics_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pics")
for f in glob.glob(os.path.join(pics_dir, "*")):
    try:
        os.remove(f)
    except Exception:
        pass

def trigger_snipping():
    # Called when user clicks 'Capture Now' in tray menu
    def on_snipped(image):
        app.on_snipped_callback(image)
    snipping_tool = SnippingTool(on_snipped)
    snipping_tool.start()

if __name__ == "__main__":
    # Start hotkey listener in a daemon thread
    listener_thread = threading.Thread(target=app.start_hotkey_listener, daemon=True)
    listener_thread.start()
    # Run tray icon (blocks)
    tray.main(on_capture=trigger_snipping)

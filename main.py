import os
import glob
import warnings
import threading
import signal
import sys
import ctypes
import tkinter as tk

# Set DPI awareness before any GUI operations
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Suppress noisy warnings from dependencies — must be set before importing TF
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", message=".*np\\.object.*")

from core.snipping import SnippingManager
from core.inference import load_models, shutdown as shutdown_surya
from ui import tray
from ui.ocr_indicator import set_root as set_indicator_root
from utils.helpers import set_sound_enabled

set_sound_enabled(False)

# Ensure pics folder exists
pics_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pics")
os.makedirs(pics_dir, exist_ok=True)

# Clear old screenshots on startup
for f in glob.glob(os.path.join(pics_dir, "*")):
    try:
        os.remove(f)
    except Exception:
        pass


if __name__ == "__main__":
    # ── Graceful shutdown handler ──────────────────────────────────
    _shutdown_requested = False

    def _shutdown(signum=None, frame=None):
        """Handle external signals (Ctrl+C, SIGTERM) or unexpected KeyboardInterrupt."""
        global _shutdown_requested
        if _shutdown_requested:
            return  # avoid double-shutdown
        _shutdown_requested = True
        print("\n[SnipOCR] Shutting down gracefully...")
        try:
            shutdown_surya()
        except Exception:
            pass
        try:
            snipping_mgr._hotkey_manager.stop()
        except Exception:
            pass
        try:
            root.quit()
        except Exception:
            pass

    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Create a persistent Tk root that stays alive for the entire app lifetime.
    # All Tkinter windows (snipping overlay, OCR indicator) use Toplevel on this root.
    root = tk.Tk()
    root.withdraw()

    # Share the persistent root with the OCR indicator module
    set_indicator_root(root)

    # Start loading Surya models in background (will be ready by first OCR)
    threading.Thread(target=load_models, daemon=True).start()

    # ── SnippingManager: tek elden snipping + OCR pipeline yönetimi ──
    # on_ocr_result callback artık sadece opsiyonel log/notification içindir.
    # State yönetimi (tray idle, indicator hide) SnippingManager içinde yapılır.
    def on_ocr_result(text, error):
        """OCR bittiğinde main thread'de çağrılır (opsiyonel ek işlemler)."""
        if error:
            ctypes.windll.user32.MessageBeep(0x00000010)
        # else: play_sound_done() — artık SnippingManager._on_ocr_completed içinde

    snipping_mgr = SnippingManager(
        root=root,
        pics_dir=pics_dir,
        on_ocr_result=on_ocr_result,
    )
    snipping_mgr.start()

    # Run tray icon in a background thread (pystray uses its own event loop)
    tray_thread = threading.Thread(
        target=tray.main,
        args=(snipping_mgr.request_snipping,),
        daemon=True
    )
    tray_thread.start()

    # Enter the Tk event loop (runs forever)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        _shutdown()
    finally:
        _shutdown()
        print("[SnipOCR] Exited.")
        sys.exit(0)

"""
HotkeyManager test script – pynput tabanlı, threading.Lock korumalı.
Çalıştır → 5 saniye içinde Pause×3 bas → trigger mesajı gör.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
import threading
import time

root = tk.Tk()
root.withdraw()

from core.hotkey import HotkeyManager

trigger_count = 0
trigger_lock = threading.Lock()

def on_trigger():
    global trigger_count
    with trigger_lock:
        trigger_count += 1
        count = trigger_count
    print(f"[TEST] TRIGGER #{count} CALLED!")
    root.after(0, root.quit)

hm = HotkeyManager(trigger_callback=on_trigger)
hm.start()

print("[TEST] HotkeyManager started. Press Pause×3 within 0.8s...")
print("[TEST] Auto-closes after first trigger. Ctrl+C to abort.")

# Wait up to 15 seconds
root.after(15000, root.quit)
root.mainloop()

hm.stop()

with trigger_lock:
    if trigger_count == 0:
        print("[TEST] FAIL: No trigger fired within 15 seconds.")
    elif trigger_count == 1:
        print("[TEST] SUCCESS: Trigger fired exactly once.")
    else:
        print(f"[TEST] FAIL: Trigger fired {trigger_count} times (should be 1).")

hm.stop()
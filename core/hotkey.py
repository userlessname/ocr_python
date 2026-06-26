import threading
import time
from pynput import keyboard
from ui.overlay import SnippingTool

class HotkeyManager:
    PAUSE_TIMEOUT = 0.5  # seconds

    def __init__(self, on_snipped):
        self.on_snipped = on_snipped
        self.pause_press_times = []
        self.listener = None

    def on_press(self, key):
        try:
            if key == keyboard.Key.pause:
                now = time.time()
                # Keep only presses within timeout
                self.pause_press_times = [t for t in self.pause_press_times if now - t < self.PAUSE_TIMEOUT]
                self.pause_press_times.append(now)
                if len(self.pause_press_times) >= 3:
                    # Triple tap detected
                    self.pause_press_times = []  # reset
                    self._trigger_snipping()
        except AttributeError:
            pass

    def _trigger_snipping(self):
        # Ensure callback runs in the same thread; tkinter will be started in this thread
        snipping_tool = SnippingTool(self.on_snipped)
        snipping_tool.start()

    def start(self):
        self.listener = keyboard.Listener(on_press=self.on_press)
        self.listener.daemon = True
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()

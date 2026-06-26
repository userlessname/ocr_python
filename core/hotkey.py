"""
HotkeyManager – pynput tabanlı, threading.Lock ile korunan, sağlam Pause×3 tetikleyicisi.

Mimari:
    pynput.keyboard.Listener (ayrı thread) → on_press callback
    threading.Lock ile korunan atomik state (sadece _blocked flag'i)
    Triple-tap algılama → Lock altında → _blocked = True
    Callback (request_snipping) → root.after(0, ...) ile main thread'e dispatch

    Bu mimaride:
      - HotkeyManager sadece kendi _blocked flag'ini Lock ile korur
      - SnippingManager state'ine asla background thread'den dokunulmaz
      - Tüm state değişiklikleri main thread'de yapılır
      - Race condition, duplicate trigger, phantom event sorunları çözülmüştür

    State:
        _blocked = False   → Hotkey dinleniyor, triple-tap tetikleyebilir
        _blocked = True    → Snipping/OCR devam ediyor, tüm Pause'lar ignore
        unblock() çağrısı  → IDLE dönüşü, _blocked = False
"""

from __future__ import annotations

import threading
import time
from pynput import keyboard


class HotkeyManager:
    """
    Listens for triple-Pause-tap and fires trigger_callback exactly once.

    Güvenlik katmanları:
      1. Lock: _blocked değişkeni threading.Lock ile korunur
      2. Cooldown: trigger sonrası SESSION_RECOVERY süre blok
      3. Dedupe: son 150ms içindeki aynı basışı ignore et
      4. Session block: snipping/OCR süresince tüm Pause'lar ignore
    """

    TRIPLE_TAP_WINDOW = 0.8      # saniye — sliding window for triple-tap
    DEDUPE_INTERVAL = 0.15       # 150ms — phantom repeat event'leri engelle
    SESSION_RECOVERY = 0.3       # saniye — unblock sonrası cooldown

    def __init__(self, trigger_callback=None):
        """
        Args:
            trigger_callback: Triple-tap algılandığında çağrılır.
                              pynput thread'inden çağrılır!
                              Callback NON-BLOCKING olmalı (sadece root.after ile dispatch eder).
                              Kesinlikle state'e dokunmamalıdır!
        """
        self._trigger_callback = trigger_callback
        self._listener: keyboard.Listener | None = None

        # State — Lock korumalı
        self._lock = threading.Lock()
        self._blocked: bool = False          # True iken tüm Pause'lar ignore

        # Timing (Lock altında erişilmeli)
        self._pause_press_times: list[float] = []
        self._last_pause_press: float = 0.0
        self._last_trigger_time: float = 0.0

    # ── Public API ──────────────────────────────────────────────────

    def start(self):
        """pynput listener'ı başlat (background thread)."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        """Listener'ı durdur."""
        if self._listener:
            self._listener.stop()
            self._listener = None

    def unblock(self):
        """Snipping/OCR bittiğinde çağrılır. Hotkey'in tekrar çalışmasına izin ver.
        Thread-safe: Lock altında _blocked = False + cooldown.
        """
        with self._lock:
            self._blocked = False
            self._last_trigger_time = time.time()

    # ── Internal: pynput callback ────────────────────────────────────

    def _on_press(self, key):
        """pynput thread'inde çalışır. Sadece Pause tuşu ile ilgilenir."""
        try:
            if key != keyboard.Key.pause:
                return
        except AttributeError:
            return

        now = time.time()

        with self._lock:
            # ── Layer 1: Session block ──────────────────────────
            # Snipping/OCR aktifken tüm Pause'lar ignore
            if self._blocked:
                return

            # ── Layer 2: Cooldown ──────────────────────────────
            # Trigger sonrası bekleme süresi
            if now - self._last_trigger_time < self.SESSION_RECOVERY:
                return

            # ── Layer 3: Dedupe ──────────────────────────────────
            # Son 150ms içindeki aynı fiziksel basışı ignore et
            if now - self._last_pause_press < self.DEDUPE_INTERVAL:
                return
            self._last_pause_press = now

            # ── Sliding window triple-tap detection ──────────────
            self._pause_press_times = [
                t for t in self._pause_press_times
                if now - t < self.TRIPLE_TAP_WINDOW
            ]
            self._pause_press_times.append(now)

            if len(self._pause_press_times) < 3:
                return  # Henüz 3 basış olmadı

            # Triple tap confirmed!
            self._pause_press_times = []
            self._last_trigger_time = now
            self._blocked = True

        # Lock SERBEST bırakıldıktan sonra callback'i çağır
        # Bu, lock contention'ı minimize eder
        self._fire()

    def _fire(self):
        """Triple-tap doğrulandı – trigger callback'ini çağır.
        Callback sadece root.after(0, ...) ile dispatch etmeli,
        asla state'e dokunmamalıdır."""
        if self._trigger_callback:
            self._trigger_callback()
        else:
            print("[Hotkey] No trigger_callback configured.")
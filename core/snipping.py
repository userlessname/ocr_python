"""
SnippingManager – screenshot alma işleminin tam yaşam döngüsünü yöneten state machine.

States:
    IDLE        → Hiçbir işlem yok, hotkey/tray tetiklemesi bekleniyor
    SNIPPING    → Overlay açık, kullanıcı alan seçiyor
    PROCESSING  → OCR işleniyor (background thread)

Transitions (KESIN tektir):
    IDLE       → request_snipping()  → SNIPPING  (overlay açılır)
    SNIPPING   → ESC/hata           → IDLE      (iptal)
    SNIPPING   → alan seçildi       → PROCESSING (OCR başlar)
    PROCESSING → OCR bitti          → IDLE      (döngü tamam)

Thread-safety:
    Tüm state değişiklikleri sadece MAIN THREAD'de yapılır.
    Hotkey/tray callback'leri (background thread'lerden gelir) sadece
    root.after(0, ...) ile dispatch eder. State'e background thread'de
    asla dokunulmaz. Bu, race condition'ları tamamen ortadan kaldırır.
"""

from __future__ import annotations

import threading
import datetime
import ctypes
from enum import Enum, auto

from core.hotkey import HotkeyManager
from core.processor import ImageProcessor, _ocr_executor
from ui.overlay import SnippingOverlay
from ui.tray import set_busy, set_idle
from ui.ocr_indicator import show as show_indicator, hide as hide_indicator
from utils.helpers import play_sound_start, play_sound_done


class SnippingState(Enum):
    IDLE = auto()
    SNIPPING = auto()
    PROCESSING = auto()


class SnippingManager:
    """
    Tek elden snipping + OCR pipeline yöneticisi.
    Hotkey/tray → overlay → OCR → cleanup zincirinin tamamını bu sınıf yönetir.
    Herhangi bir anda sadece 1 işlem aktiftir.
    """

    def __init__(
        self,
        root,
        pics_dir: str,
        on_ocr_result=None,
    ):
        """
        Args:
            root:            Kalıcı Tk root (Tkinter Toplevel'lar bunun üzerinde açılır)
            pics_dir:        Ekran görüntülerinin kaydedileceği dizin
            on_ocr_result:   Opsiyonel callback, OCR bittiğinde main thread'de çağrılır.
                             İmzası: on_ocr_result(text: str, error: Exception | None)
        """
        self._root = root
        self._pics_dir = pics_dir
        self._on_ocr_result = on_ocr_result

        self.state = SnippingState.IDLE
        self._processor = ImageProcessor(pics_dir)
        self._hotkey_manager: HotkeyManager | None = None
        self._current_overlay: SnippingOverlay | None = None

    # ── Public API ──────────────────────────────────────────────────

    def start(self):
        """Hotkey listener'ı başlat."""
        self._start_hotkey_listener()

    def request_snipping(self):
        """
        Hotkey'den veya tray menüsünden çağrılır.
        Background thread'den (pynput/pystray) gelebilir.
        SADECE root.after(0, ...) ile main thread'e dispatch eder.
        State kontrolü main thread'de _try_start_snipping içinde yapılır.
        Bu sayede state değişkeni asla race condition'a girmez.
        """
        self._root.after(0, self._try_start_snipping)

    # ── State transitions (private) ─────────────────────────────────

    def _try_start_snipping(self):
        """
        Main thread'de çalışır (root.after ile schedule edilir).
        Sadece IDLE state'inde snipping başlatır. Diğer tüm durumlarda
        (SNIPPING, PROCESSING) sessizce reddedilir.
        """
        if self.state != SnippingState.IDLE:
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                f"Snipping ignored (state={self.state.name}). "
                "Already snipping or processing."
            )
            return

        # State'i HEMEN SNIPPING'e çek (başka hiçbir istek giremez)
        self.state = SnippingState.SNIPPING

        # Overlay'i aç (main thread'deyiz, direkt çağırabiliriz)
        self._start_overlay()

    def _on_image_captured(self, image):
        """
        Kullanıcı alan seçti — overlay kapandı, OCR başlıyor.
        image == None → iptal (ESC veya çok küçük seçim).
        image != None → başarılı seçim, OCR'a gönder.
        """
        self._current_overlay = None

        if image is None:
            # İptal → IDLE'a dön
            self.state = SnippingState.IDLE
            self._unblock_hotkey()
            return

        # OCR başlat
        self.state = SnippingState.PROCESSING

        # UI indicator'ları hemen göster
        set_busy()
        show_indicator()
        play_sound_start()

        # OCR'ı background thread'de başlat
        _ocr_executor.submit(self._ocr_worker, image)

    def _on_ocr_completed(self, text: str, error: Exception | None):
        """OCR bittiğinde main thread'de çağrılır (root.after ile schedule edilir)."""
        self.state = SnippingState.IDLE
        self._unblock_hotkey()
        set_idle()
        hide_indicator()

        if error:
            ctypes.windll.user32.MessageBeep(0x00000010)  # MB_ICONHAND
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                f"OCR Error: {error}"
            )
        else:
            play_sound_done()

        if self._on_ocr_result:
            self._on_ocr_result(text, error)

    # ── Internal helpers ────────────────────────────────────────────

    def _start_hotkey_listener(self):
        """
        HotkeyManager'i başlat. pynput kullanır (background thread).
        threading.Lock ile korunmuş state sayesinde duplicate trigger imkansız.
        """
        self._hotkey_manager = HotkeyManager(
            trigger_callback=self.request_snipping,
        )
        self._hotkey_manager.start()

    def _unblock_hotkey(self):
        """Snipping/OCR bittiğinde hotkey'in tekrar çalışmasına izin ver."""
        if self._hotkey_manager:
            self._hotkey_manager.unblock()

    def _start_overlay(self):
        """
        SnippingOverlay'i oluştur ve aç.
        Main thread'de çalışır (state kontrolü yapılmış olarak gelir).
        Eğer state SNIPPING değilse (hata durumu) sessizce dön.
        """
        if self.state != SnippingState.SNIPPING:
            # Race condition koruması — buraya gelinmemeli ama olsun
            self.state = SnippingState.IDLE
            self._unblock_hotkey()
            return

        tool = SnippingOverlay(
            parent_root=self._root,
            on_result=self._on_image_captured,
        )
        self._current_overlay = tool

        try:
            tool.start()
        except Exception as e:
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                f"SnippingOverlay error: {e}"
            )
            self._on_image_captured(None)

    def _ocr_worker(self, image):
        """
        Background thread'de çalışır:
        1. Model yükle (ilk seferde)
        2. OCR çalıştır
        3. Görüntüyü ve metni diske kaydet
        4. Main thread'e sonucu bildir
        """
        try:
            from core.inference import load_models, ocr_image

            load_models()  # Model loading blocking'dir, background thread'de olur
            ocr_text = ocr_image(image)

            # Disk I/O da background thread'de
            self._processor.save_image(image)
            self._processor.save_text(ocr_text)

            chars = len(ocr_text)
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                f"OCR completed! ({chars} chars)"
            )

            # Main thread'e schedule et
            self._root.after(0, lambda: self._on_ocr_completed(ocr_text, None))

        except Exception as e:
            print(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                f"OCR Error: {e}"
            )
            self._root.after(0, lambda: self._on_ocr_completed("", e))
"""
Snipping overlay – ekran bölgesi seçmek için tam ekran Toplevel.

Mimari:
    OverlayWindow (base)        → Temel Toplevel yönetimi, Windows API, lifecycle
        └── SnippingOverlay     → Fare ile bölge seçme, görüntü kırpma

Kullanım:
    tool = SnippingOverlay(parent_root, on_result=callback)
    tool.start()

    on_result(image: PIL.Image | None)
        - image != None → kullanıcı geçerli bir alan seçti
        - image == None → iptal (ESC), hata veya çok küçük seçim
"""

from __future__ import annotations

import datetime
import ctypes
from typing import Callable, Optional

import tkinter as tk
from PIL import Image, ImageGrab, ImageTk
from screeninfo import get_monitors

from utils.helpers import get_monitor_at_cursor


# ── Constants ──────────────────────────────────────────────────────────
SELECTION_MIN_SIZE = 10       # px – minimum seçim boyutu
OVERLAY_OPACITY = 255         # 0-255, 255 = fully opaque
HINT_TEXT = "Click and drag to select area. Press ESC to cancel."
HINT_FONT = ("Arial", 16, "bold")
HINT_COLOR = "white"
SELECTION_COLOR = "#00ff00"
SELECTION_WIDTH = 2
DIM_ALPHA = 0.4               # Karartma miktarı (0 = hiç, 1 = tam siyah)


# ── Base Overlay ───────────────────────────────────────────────────────

class OverlayWindow:
    """
    Tkinter Toplevel tabanlı tam ekran overlay.
    Windows API ile topmost + click-through özelliklerini yönetir.
    """

    def __init__(self, parent_root: tk.Tk):
        self.parent_root = parent_root
        self.root: Optional[tk.Toplevel] = None
        self._cleaned_up = False

    # ── Lifecycle ──────────────────────────────────────────────────

    def create(self, width: int, height: int, x: int, y: int):
        """
        Overlay Toplevel'ini oluştur ve yapılandır.
        Alt sınıflar create()'den sonra canvas vs. ekleyebilir.
        """
        self.root = tk.Toplevel(self.parent_root)
        self.root.withdraw()
        self._configure_window(width, height, x, y)
        self._apply_windows_styles()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.update_idletasks()
        self.root.update()

    def close(self):
        """Overlay'i kapat ve kaynakları temizle. Idempotent."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        try:
            self.root.grab_release()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.root = None

    # ── Internal helpers ───────────────────────────────────────────

    def _configure_window(self, width: int, height: int, x: int, y: int):
        """Temel pencere ayarları: boyut, konum, borderless, topmost."""
        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass
        self.root.overrideredirect(True)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.attributes("-topmost", True)
        self.root.config(cursor="cross", bg="black")

    def _apply_windows_styles(self):
        """Windows API ile WS_EX_LAYERED + WS_EX_TOPMOST stillerini uygula."""
        try:
            hwnd = self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TOPMOST = 0x00000008
            ex_style = ctypes.windll.user32.GetWindowLongA(hwnd, GWL_EXSTYLE)
            new_style = ex_style | WS_EX_LAYERED | WS_EX_TOPMOST
            ctypes.windll.user32.SetWindowLongA(hwnd, GWL_EXSTYLE, new_style)
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, 0, OVERLAY_OPACITY, 0x00000002
            )
        except Exception as e:
            print(f"[Overlay] Could not set window styles: {e}")

    def _force_topmost(self):
        """Pencereyi en üste getir ve odakla."""
        try:
            hwnd = self.root.winfo_id()
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()

    def _grab_input(self):
        """Tüm input'u bu pencereye yönlendir."""
        try:
            self.root.grab_set()
        except Exception:
            pass


# ── Snipping Overlay ───────────────────────────────────────────────────

class SnippingOverlay(OverlayWindow):
    """
    Fare sürükleyerek ekran bölgesi seçme overlay'i.
    """
    def __init__(self, parent_root: tk.Tk, on_result: Callable):
        super().__init__(parent_root)
        self.on_result = on_result

        # Selection state
        self.start_x: Optional[int] = None
        self.start_y: Optional[int] = None
        self.end_x: Optional[int] = None
        self.end_y: Optional[int] = None

        # UI elements
        self.canvas: Optional[tk.Canvas] = None
        self._sel_rect: Optional[int] = None  # canvas rect id
        self._screenshot: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None

        # Monitor info (set during start)
        self._mon_x: int = 0
        self._mon_y: int = 0
        self._mon_width: int = 0
        self._mon_height: int = 0

    # ── Public API ─────────────────────────────────────────────────

    def start(self):
        """Overlay'i oluştur ve göster. Hata durumunda temizlik yap."""
        try:
            self._start_impl()
        except Exception as e:
            print(
                f"[{_now()}] SnippingOverlay start error: {e}"
            )
            self._cancel()

    # ── Implementation ─────────────────────────────────────────────

    def _start_impl(self):
        """Overlay kurulumu: monitör bilgisi → screenshot → canvas → event binding."""
        # 1. Hangi monitörde olduğumuzu bul
        monitor = get_monitor_at_cursor()
        self._mon_x = monitor["x"]
        self._mon_y = monitor["y"]
        self._mon_width = monitor["width"]
        self._mon_height = monitor["height"]

        print(
            f"[{_now()}] Opening snipping tool on monitor: "
            f"{monitor['name']} ({self._mon_width}x{self._mon_height} "
            f"at {self._mon_x},{self._mon_y})"
        )

        # 2. Tüm ekranın screenshot'ını al, sonra monitörü kırp
        full = ImageGrab.grab(all_screens=True)
        virtual_left = min(m.x for m in get_monitors())
        virtual_top = min(m.y for m in get_monitors())
        self._screenshot = full.crop((
            self._mon_x - virtual_left,
            self._mon_y - virtual_top,
            self._mon_x - virtual_left + self._mon_width,
            self._mon_y - virtual_top + self._mon_height,
        ))

        # 3. Overlay penceresini oluştur
        self.create(self._mon_width, self._mon_height, self._mon_x, self._mon_y)

        # 4. Canvas'ı oluştur
        self.canvas = tk.Canvas(
            self.root,
            width=self._mon_width,
            height=self._mon_height,
            highlightthickness=0,
            bg="black",
        )
        self.canvas.pack()

        # 5. Karartılmış screenshot'ı canvas'a yerleştir
        self._photo = ImageTk.PhotoImage(self._dimmed_screenshot())
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

        # 6. Yardım metni
        self.canvas.create_text(
            self._mon_width // 2, 30,
            text=HINT_TEXT,
            fill=HINT_COLOR,
            font=HINT_FONT,
        )

        # 7. Event binding
        self.root.bind("<Button-1>", self._on_mouse_down)
        self.root.bind("<B1-Motion>", self._on_mouse_drag)
        self.root.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.root.bind("<Escape>", self._on_escape)

        # 8. En üste getir ve input'u yakala
        self._force_topmost()
        self._grab_input()

    # ── Screenshot helpers ─────────────────────────────────────────

    def _dimmed_screenshot(self) -> Image.Image:
        """Screenshot'u %40 karartılmış halde döndür."""
        darkened = self._screenshot.copy()
        darkened = Image.blend(
            darkened,
            Image.new("RGB", darkened.size, (0, 0, 0)),
            DIM_ALPHA,
        )
        if darkened.size != (self._mon_width, self._mon_height):
            darkened = darkened.resize(
                (self._mon_width, self._mon_height), Image.Resampling.LANCZOS
            )
        return darkened

    def _crop_selection(self) -> Image.Image:
        """Seçilen alanı orijinal screenshot'dan kırp."""
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)

        # Screenshot vs canvas ölçek farkını düzelt
        sw, sh = self._screenshot.size
        sx = sw / self._mon_width
        sy = sh / self._mon_height

        return self._screenshot.crop((
            int(x1 * sx), int(y1 * sy),
            int(x2 * sx), int(y2 * sy),
        ))

    def _is_valid_selection(self) -> bool:
        """Seçim minimum boyuttan büyük mü?"""
        if None in (self.start_x, self.end_x):
            return False
        w = abs(self.end_x - self.start_x)
        h = abs(self.end_y - self.start_y)
        return w > SELECTION_MIN_SIZE and h > SELECTION_MIN_SIZE

    # ── Event handlers ─────────────────────────────────────────────

    def _on_mouse_down(self, event: tk.Event):
        self.start_x = event.x
        self.start_y = event.y
        self.end_x = event.x
        self.end_y = event.y
        self._sel_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline=SELECTION_COLOR,
            width=SELECTION_WIDTH,
        )

    def _on_mouse_drag(self, event: tk.Event):
        self.end_x = event.x
        self.end_y = event.y
        if self._sel_rect is not None:
            self.canvas.coords(
                self._sel_rect,
                self.start_x, self.start_y,
                self.end_x, self.end_y,
            )

    def _on_mouse_up(self, event: tk.Event):
        self.end_x = event.x
        self.end_y = event.y

        if self._is_valid_selection():
            cropped = self._crop_selection()
            # Overlay'i callback'ten ÖNCE gizle (yeni Toplevel'lar açılabilir)
            self.close()
            self.on_result(cropped)
        else:
            print(
                f"[{_now()}] Selection too small ({abs(self.end_x - self.start_x)}x"
                f"{abs(self.end_y - self.start_y)}), cancelled."
            )
            self._cancel()

    def _on_escape(self, _event: tk.Event):
        print(f"[{_now()}] Snipping cancelled.")
        self._cancel()

    def _cancel(self):
        """İptal/hata: önce overlay'i kapat, SONRA on_result(None) bildir.
        
        Sıralama önemlidir:
        1. Önce close() → overlay Toplevel'i tamamen yok olur
        2. Sonra on_result(None) → SnippingManager state → IDLE + hotkey unblock
        
        Bu sıralama sayesinde yeni bir hotkey gelirse overlay zaten kapanmış
        ve yeni bir overlay sorunsuz açılabilir.
        """
        self.close()
        try:
            self.on_result(None)
        except Exception:
            pass


# ── Helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")
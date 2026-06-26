"""
ImageProcessor – OCR sonrası görüntü/metin/kaydetme işlemleri.
Artık sadece async path mevcuttur. Tüm UI callback'leri SnippingManager
tarafından main thread'de yapılır. Bu dosya sadece:
  1. Görüntüyü diske kaydetme
  2. OCR metnini clipboard'a kopyalama + diske kaydetme
görevlerini üstlenir.
"""

from __future__ import annotations

import os
import time as time_module
from concurrent.futures import ThreadPoolExecutor
import pyperclip
from PIL import Image


# Single shared executor for OCR background processing
_ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")


class ImageProcessor:
    def __init__(self, pics_dir: str):
        self.pics_dir = pics_dir
        self.timestamp = None

    # ── Shared helpers ────────────────────────────────────────────

    def save_image(self, image: Image.Image) -> str:
        """Görüntüyü pics klasörüne PNG olarak kaydet."""
        self.timestamp = str(time_module.time()).replace(".", "")
        image_path = os.path.join(self.pics_dir, f"{self.timestamp}_clipboard_image.png")
        image.save(image_path, "PNG")
        return image_path

    def save_text(self, text: str):
        """
        OCR metnini clipboard'a kopyala ve txt olarak kaydet.
        Clipboard'a yazarken 3 kere dener (başka uygulama kilitli olabilir).
        """
        # Retry clipboard write up to 3 times (other apps may hold the clipboard)
        for attempt in range(3):
            try:
                pyperclip.copy(text)
                break
            except Exception as e:
                if attempt < 2:
                    time_module.sleep(0.1)
                else:
                    print(
                        f"[{time_module.strftime('%H:%M:%S')}] "
                        f"Clipboard copy failed: {e}"
                    )

        with open(
            os.path.join(self.pics_dir, f"{self.timestamp}_clipboard_text.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(text)
        print(
            f"[{time_module.strftime('%H:%M:%S')}] "
            "OCR output copied to clipboard."
        )
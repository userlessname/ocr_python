import os
import time
import datetime
import ctypes
import pyperclip
from PIL import Image
from core.inference import ocr_image, load_models
from ui.tray import set_busy, set_idle
from ui.ocr_indicator import show as show_indicator, hide as hide_indicator
from utils.helpers import play_sound_start, play_sound_done


class ImageProcessor:
    def __init__(self, pics_dir):
        self.pics_dir = pics_dir
        self.timestamp = None

    def process_image(self, image):
        try:
            if image:
                # Load Surya models on first use
                load_models()

                # Visual + audio feedback: OCR starting
                set_busy()
                show_indicator()
                play_sound_start()

                # Save original screenshot before preprocessing
                image_path = self.save_image(image)

                # Run Surya OCR (preprocessing handled internally)
                ocr_text = ocr_image(image)

                self.save_text(ocr_text)

                # Visual + audio feedback: OCR done
                chars = len(ocr_text)
                set_idle()
                hide_indicator()
                play_sound_done()

                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] OCR completed! ({chars} chars)")
            else:
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] No image.")
        except Exception as e:
            set_idle()
            hide_indicator()
            ctypes.windll.user32.MessageBeep(0x00000010)  # MB_ICONHAND = error sound
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Error: {e}")

    def save_image(self, image):
        self.timestamp = str(time.time()).replace(".", "")
        image_path = os.path.join(self.pics_dir, f"{self.timestamp}_clipboard_image.png")
        image.save(image_path, "PNG")
        return image_path

    def save_text(self, text):
        pyperclip.copy(text)
        with open(os.path.join(self.pics_dir, f"{self.timestamp}_clipboard_text.txt"), 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] OCR output copied to clipboard.")

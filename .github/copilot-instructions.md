# GitHub Copilot Instructions for OCR Python

You are an expert Python and Desktop Application Developer. You are assisting in the development of **OCR Python**, an intelligent screen OCR desktop application for Windows. Always follow the architectural design, tech stack, and conventions defined below.

---

## 📖 Reference Docs
- See [README.md](../README.md) for full project overview, features, and quick start.
- See [tests/test_images.py](../tests/test_images.py) for the test harness pattern (subclass `ImageProcessor`).
- See `_versions txt/` for historical evolution snapshots.

---

## 🛑 CRITICAL RULES (NEVER VIOLATE)
1. **NO TESSERACT (Surya OCR only):** Fully migrated as of June 2026. Do NOT suggest, import, or use `pytesseract` or any Tesseract-related code.
2. **Windows-only:** Uses Windows API (`ctypes.windll.*`), DPI awareness, system beeps. Do not add cross-platform abstractions.
3. **Tk root lives forever:** A single hidden `tk.Tk()` root is created in `main.py`. All windows use `tk.Toplevel` on it — never create additional `tk.Tk()` instances.

---

## 🚀 Commands
| Action | Command |
|--------|---------|
| Run app | `python main.py` |
| Run tests | `python tests/test_images.py` |
| Install deps | `pip install -r requirements.txt` |

---

## 🧱 Module Architecture
- **`main.py`:** Entry point. Hidden Tk root, DPI awareness setup, queue polling via `root.after(100, process_queue)`, daemon thread startup (hotkey + tray + model loading).
- **`core/app.py`:** Singleton queue (`snipping_queue = queue.Queue()`), processor singleton via `get_processor()`, `atexit` Surya cleanup.
- **`core/inference.py`:** Surya model lifecycle — lazy singleton globals (`_foundation_predictor`, `_detection_predictor`, `_recognition_predictor`). Functions: `load_models()`, `shutdown()`, `ocr_image(image: PIL.Image) -> str`.
- **`core/processor.py`:** `ImageProcessor` class — orchestrates capture→OCR→clipboard. Methods: `process_image(image, done_callback=None)`, `save_image(image)`, `save_text(text)`. Uses `ThreadPoolExecutor` for async OCR when `done_callback` is provided. Clipboard writes retry up to 3 times.
- **`core/hotkey.py`:** `HotkeyManager` — `pynput` listener tracking 3× `Pause` presses within 0.8s. Accepts only `trigger_callback` (no dead `on_snipped` parameter).
- **`ui/overlay.py`:** `SnippingTool(root, callback)` — Toplevel with canvas for region selection. Uses `grab_set()` (not `grab_set_global()`). `force_topmost()` only touches the overlay window, not other apps.
- **`ui/tray.py`:** `pystray` icon — blue "T" (idle) / orange "T" (busy). Thread-safe via `_icon_lock`. Functions: `set_busy()`, `set_idle()`, `main(on_capture)`.
- **`ui/ocr_indicator.py`:** Click-through "OCR" HUD at top-right. Functions: `set_root(root)`, `show()`, `hide()`.
- **`utils/helpers.py`:** `get_screen_size()`, `get_mouse_position()`, `get_monitor_at_cursor()`, `create_tray_icon_idle/busy()`, `play_sound_start/done()`, `set_sound_enabled()`. DPI awareness uses `SetProcessDpiAwareness(2)` (Per-Monitor v2) with fallback.

---

## 🧵 Threading Model
| Thread | Role |
|--------|------|
| **Main** | Tkinter event loop + `root.after(100, process_queue)` polling. `SnippingTool`, `set_busy/idle`, `show/hide_indicator` run here. |
| **OCR** (from `ThreadPoolExecutor`) | `ocr_image()` runs on a background thread — **does not block the UI**. Completion callback is dispatched to main thread via `root.after()`. |
| **Hotkey** (daemon) | `pynput` listener — detects triple-tap → puts `None` in `snipping_queue`. |
| **Tray** (daemon) | `pystray` event loop — icon + menu ("Capture Now", "Exit"). |
| **Model loader** (daemon) | Starts at boot via `threading.Thread(target=load_models, daemon=True)` — models load asynchronously in background. |

**Async OCR pattern:** `ImageProcessor.process_image(image, done_callback)` submits OCR to `ThreadPoolExecutor`. The `done_callback(text, error)` is called from the worker thread — the caller (usually `main.py`) wraps it in `root.after(0, ...)` to execute on the main thread for UI updates (`set_idle()`, `hide_indicator()`, `play_sound_done()`).

---

## 🎨 Coding Conventions
### Naming
- `PascalCase` classes: `SnippingTool`, `HotkeyManager`, `ImageProcessor`
- `camelCase` methods: `on_mouse_down`, `force_topmost`, `process_image`
- `snake_case` functions & variables: `load_models`, `ocr_image`, `snipping_queue`
- `UPPER_CASE` module constants: `PAUSE_TIMEOUT`, `FONT_SIZE`, `BG_COLOR`

### Surya OCR API
```python
from surya.foundation import FoundationPredictor
from surya.detection import DetectionPredictor
from surya.recognition import RecognitionPredictor

foundation = FoundationPredictor()
det = DetectionPredictor()
rec = RecognitionPredictor(foundation)
predictions = rec([image], det_predictor=det, sort_lines=True)
result: OCRResult = predictions[0]
for line in result.text_lines:  # TextLine objects
    text = line.text
    polygon = line.polygon  # [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
```

### Indentation Reconstruction Algorithm
Surya provides bounding boxes but not indentation. Algorithm in `ocr_image()`:
1. Find `min_left` across all `TextLine.polygon[0][0]`
2. Estimate `char_width = median(line_width / len(text))`
3. For each line: `spaces = round((polygon[0][0] - min_left) / char_width)`

### Image Preprocessing
- 2× LANCZOS upscale (improves small text/underscore detection)
- Dark mode detection: median of edge pixels < 128 → invert colors
- OpenCV pipeline: `cv2.cvtColor` → `cv2.bitwise_not` → convert back to RGB PIL

### Testing Pattern
`TestImageProcessor` subclasses `ImageProcessor`, overrides `save_image()` and `save_text()` to capture output instead of writing to disk. Tests compare OCR output vs expected text using `difflib.SequenceMatcher.ratio()` — pass threshold ≥ 0.95.
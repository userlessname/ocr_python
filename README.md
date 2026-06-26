# OCR Python — Smart Screen OCR Tool

> **OCR any on-screen text with a triple-tap.** Built for developers who need to capture code, terminal output, or documentation from their screen directly to the clipboard.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![Surya OCR](https://img.shields.io/badge/OCR-Surya%202-green)](https://github.com/datalab-to/surya)

---

## ✨ Features

- **Triple-tap `Pause` key** to trigger screen region selection
- **Drag to select** any area of your screen
- **Instant OCR** with [Surya](https://github.com/datalab-to/surya) — state-of-the-art 650M param VLM model
- **Preserves indentation & spacing** — perfect for code snippets
- **Auto-copies** results to clipboard
- **System tray icon** with visual busy indicator and sound feedback
- **Supports 90+ languages** (Turkish characters, special symbols, etc.)

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Windows (uses Windows API for DPI awareness, tray notifications, and system sounds)

### Installation

```bash
# Clone the repository
git clone https://github.com/userlessname/ocr_python.git
cd ocr_python

# Install dependencies
pip install -r requirements.txt
```

### Usage

```bash
# Run the app (starts in system tray)
python main.py
```

1. Look for the **blue "T" icon** in your system tray
2. **Triple-tap the `Pause` key** — the screen will dim
3. **Click and drag** to select a region
4. Wait a moment — the tray icon turns **orange** during OCR
5. The recognized text is automatically **copied to your clipboard** ✅

> **Tip:** You can also right-click the tray icon and select **"Capture Now"** to trigger snipping manually.

### Running Tests

```bash
python tests/test_images.py
```

## 🧠 How It Works

```
User triple-taps Pause
    → Full-screen selection overlay opens
    → User selects region with mouse
    → Surya OCR engine processes the image (2× upscale + dark mode detection)
    → Text is reconstructed with proper indentation from bounding boxes
    → Result copied to clipboard + saved to pics/ folder
```

### OCR Pipeline

| Step | Tool | Purpose |
|------|------|---------|
| Image preprocessing | OpenCV + Pillow | 2× upscale, dark mode inversion |
| Text detection | Surya `DetectionPredictor` | Locates text lines on the image |
| Text recognition | Surya `RecognitionPredictor` | Reads text using 650M param VLM |
| Layout reconstruction | Custom algorithm | Restores indentation from bounding box positions |

## 📁 Project Structure

```
ocrPython/
├── main.py                  # Entry point — tray icon + hotkey listener
├── core/
│   ├── app.py               # Application glue logic
│   ├── processor.py          # Image processing pipeline
│   ├── inference.py          # Surya model lifecycle management
│   └── hotkey.py             # Triple-tap Pause key detector
├── ui/
│   ├── overlay.py            # Screen region selection overlay (tkinter)
│   ├── tray.py               # System tray icon + menu
│   └── ocr_indicator.py      # On-screen "OCR" indicator overlay
├── utils/
│   └── helpers.py            # Screen utilities, icon generation, sound
├── tests/
│   └── test_images.py        # Test suite against saved screenshots
├── pics/                     # Saved screenshots + OCR output (auto-cleaned)
└── _versions txt/            # Historical evolution snapshots
```

## 🔧 Configuration

All settings are currently hardcoded for simplicity. Key values:

| Setting | Value |
|---------|-------|
| OCR engine | Surya v1 (0.17.1) |
| Upscale factor | 2× (LANCZOS) |
| Dark mode detection | Edge pixel median < 128 |
| Hotkey | Triple-tap `Pause` (within 0.5s) |
| Sound feedback | Windows API `Beep` / `MessageBeep` |
| Image storage | `pics/` folder (auto-cleaned on startup) |

### Disabling Sounds

Add this line to `main.py`:

```python
from utils.helpers import set_sound_enabled
set_sound_enabled(False)
```

## 🎯 Why Surya over Tesseract?

| Aspect | Tesseract (old) | Surya (new) |
|--------|----------------|-------------|
| Model size | ~15 MB | 650M param VLM |
| Language support | Limited | 90+ languages |
| Underscore accuracy | Poor | Excellent (with 2× upscale) |
| Layout analysis | Basic (PSM modes) | Built-in layout + reading order |
| Custom code needed | ~180 lines preprocessing + line grouping | ~35 lines (delegated to model) |
| Turkish characters | ❌ Misses ü/ı/ç/ö | ✅ Perfect |

## 📜 License

This project is **MIT** licensed. The underlying Surya OCR model has its own [model license](https://github.com/datalab-to/surya#commercial-usage) (free for research, personal use, and startups under $5M funding/revenue).

---

*Built with [Surya](https://github.com/datalab-to/surya) by Datalab and [Marker](https://github.com/datalab-to/marker).*

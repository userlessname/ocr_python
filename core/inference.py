"""
Inference manager for Surya OCR.
Manages the lifecycle of Surya models (FoundationPredictor, DetectionPredictor, RecognitionPredictor).
Models are loaded once and reused across all OCR calls.
"""

from __future__ import annotations
import threading

# Global singleton instances
_foundation_predictor = None
_detection_predictor = None
_recognition_predictor = None
_device = None
_model_lock = threading.Lock()


def get_device():
    """Detect available device: DirectML (AMD GPU) → CUDA → CPU fallback."""
    global _device
    if _device is not None:
        return _device
    import torch
    try:
        import torch_directml
        _device = torch_directml.device()
        print(f"[Surya] GPU (DirectML): {torch_directml.device_name(0)}")
        return _device
    except ImportError:
        pass
    if torch.cuda.is_available():
        _device = torch.device("cuda")
        print(f"[Surya] GPU (CUDA): {torch.cuda.get_device_name(0)}")
        return _device
    _device = torch.device("cpu")
    print("[Surya] GPU bulunamadı, CPU’da çalışıyor.")
    return _device


def load_models():
    """Initialize all Surya models (called once at startup).
    Thread-safe: uses _model_lock to prevent concurrent loading."""
    global _foundation_predictor, _detection_predictor, _recognition_predictor

    if _foundation_predictor is not None:
        print("[Surya] Models already loaded, reusing.")
        return

    with _model_lock:
        # Double-check after acquiring lock — another thread may have loaded them
        if _foundation_predictor is not None:
            print("[Surya] Models already loaded, reusing.")
            return

        import torch
        from surya.foundation import FoundationPredictor
        from surya.detection import DetectionPredictor
        from surya.recognition import RecognitionPredictor

        print("[Surya] Loading OCR models (first time may take a while)...")
        device = get_device()
        _foundation_predictor = FoundationPredictor(device=device)
        _detection_predictor = DetectionPredictor(device=device)
        _recognition_predictor = RecognitionPredictor(_foundation_predictor)
        print("[Surya] Models loaded successfully.")


def get_recognition_predictor() -> RecognitionPredictor:
    """Get the singleton RecognitionPredictor instance."""
    if _recognition_predictor is None:
        load_models()
    return _recognition_predictor


def get_detection_predictor() -> DetectionPredictor:
    """Get the singleton DetectionPredictor instance."""
    if _detection_predictor is None:
        load_models()
    return _detection_predictor


def shutdown():
    """Clean shutdown of models (frees GPU/CPU memory)."""
    global _foundation_predictor, _detection_predictor, _recognition_predictor
    _foundation_predictor = None
    _detection_predictor = None
    _recognition_predictor = None
    print("[Surya] Models unloaded.")


def ocr_image(image: Image.Image) -> str:
    """
    Run Surya OCR on a PIL Image and return reconstructed text with indentation.

    Preprocessing:
    - Dynamic upscale based on image size (small→2×, medium→1.5×, large→1.25×)
    - Dark mode detection + inversion
    - Optimized color conversion (RGB→GRAY→RGB, ~3 fewer cvtColor calls)

    Returns: str with proper line breaks and indentation
    """
    from PIL import Image
    import numpy as np
    import torch
    from surya.recognition import OCRResult
    import cv2

    # --- Preprocessing ---

    # 1. Dynamic upscale based on image dimensions
    w, h = image.size
    if w < 300 or h < 100:
        scale = 2.0
    elif w < 800 or h < 300:
        scale = 1.5
    else:
        scale = 1.25
    img_resized = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # 2. Convert directly to grayscale (single step, saves 2 cvtColor calls)
    gray = cv2.cvtColor(np.array(img_resized), cv2.COLOR_RGB2GRAY)

    # 3. Check for dark mode (light text on dark background)
    edge_pixels = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    if np.median(edge_pixels) < 128:
        gray = cv2.bitwise_not(gray)

    # 4. Convert grayscale to 3-channel RGB in one step (saves another cvtColor)
    preprocessed = Image.fromarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))

    # --- OCR (with inference_mode for extra perf) ---
    rec = get_recognition_predictor()
    det = get_detection_predictor()

    with torch.no_grad():
        predictions = rec([preprocessed], det_predictor=det, sort_lines=True)

    if not predictions:
        return ""

    result: OCRResult = predictions[0]

    if not result.text_lines:
        return ""

    # --- Reconstruct text with indentation from bounding boxes ---
    min_left = min(line.polygon[0][0] for line in result.text_lines)

    # Estimate character width from median of all line widths
    all_widths = []
    for line in result.text_lines:
        line_width = line.polygon[1][0] - line.polygon[0][0]
        if len(line.text) > 0:
            all_widths.append(line_width / len(line.text))
    char_width = np.median(all_widths) if all_widths else 12

    lines_out = []
    for line in result.text_lines:
        indent_px = line.polygon[0][0] - min_left
        num_spaces = int(round(indent_px / char_width))
        lines_out.append(" " * num_spaces + line.text)

    return "\n".join(lines_out)

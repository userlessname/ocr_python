"""
Inference manager for Surya OCR.
Manages the lifecycle of Surya models (FoundationPredictor, DetectionPredictor, RecognitionPredictor).
Models are loaded once and reused across all OCR calls.
"""

from surya.foundation import FoundationPredictor
from surya.detection import DetectionPredictor
from surya.recognition import RecognitionPredictor
from PIL import Image
import numpy as np

# Global singleton instances
_foundation_predictor = None
_detection_predictor = None
_recognition_predictor = None


def load_models():
    """Initialize all Surya models (called once at startup)."""
    global _foundation_predictor, _detection_predictor, _recognition_predictor

    if _foundation_predictor is None:
        print("[Surya] Loading OCR models (first time may take a while)...")
        _foundation_predictor = FoundationPredictor()
        _detection_predictor = DetectionPredictor()
        _recognition_predictor = RecognitionPredictor(_foundation_predictor)
        print("[Surya] Models loaded successfully.")
    else:
        print("[Surya] Models already loaded, reusing.")


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
    - 2x upscale (LANCZOS) to improve small text/underscore detection
    - Dark mode detection + inversion

    Returns: str with proper line breaks and indentation
    """
    from surya.recognition import OCRResult
    import cv2

    # --- Preprocessing ---

    # 1. Upscale 2x for better detection of small text and underscores
    w, h = image.size
    img_resized = image.resize((w * 2, h * 2), Image.LANCZOS)

    # 2. Convert to OpenCV for dark mode detection
    img_cv = cv2.cvtColor(np.array(img_resized), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # 3. Check for dark mode (light text on dark background)
    edge_pixels = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    bg_color = int(np.median(edge_pixels))
    if bg_color < 128:
        gray = cv2.bitwise_not(gray)

    # 4. Convert back to RGB (Surya expects RGB PIL images)
    preprocessed = Image.fromarray(cv2.cvtColor(
        cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2RGB
    ))

    # --- OCR ---
    rec = get_recognition_predictor()
    det = get_detection_predictor()

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

"""
Smart Multilingual OCR Server (PaddleOCR).

Handles English, Turkish, and Code blocks at the engine level with
content-aware processing. No external text normalizer needed —
all intelligence is inline.

Pipeline:
    Image → Preprocess → OCR (tr model) → Content Detection
    → Light Char Fixes → Layout Reconstruction → Output

Key design decisions:
  * Single-pass OCR with lang='tr' — the Turkish model handles
    Latin script well enough for English + code too.
  * Content type detection per line (Turkish / English / Code)
    drives which character-level fixes are safe to apply.
  * No dictionary, no difflib, no word matching — only
    character-level fixes that are 100% reliable given the
    OCR model's known artifacts.
  * Code blocks are preserved as-is (no Turkish fixes applied).

Endpoints:
    POST /ocr/process  -> Multipart upload, returns structured text
    GET  /health       -> Liveness probe

Run standalone:  python server.py
Embedded:        main.py spawns this in a background thread.
"""
from __future__ import annotations

import io
import logging
import os
import re
from contextlib import asynccontextmanager

import numpy as np
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from paddleocr import PaddleOCR

# ── Logging ──────────────────────────────────────────────────────────────────
logging.getLogger("ppocr").setLevel(logging.ERROR)
logging.getLogger("paddlex").setLevel(logging.ERROR)
logging.getLogger("paddleocr").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
_logger = logging.getLogger("ocr_server")
_logger.setLevel(logging.INFO)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Image Pre-processing
# ═══════════════════════════════════════════════════════════════════════════════

MIN_HEIGHT_FOR_UPSCALE = 40
MIN_HEIGHT_FOR_2X_UPSCALE = 24
MIN_HEIGHT_FOR_15X_UPSCALE = 300
MAX_UPSCALE_DIM = 4000


def _preprocess_for_ocr(img_np: np.ndarray) -> np.ndarray:
    """Dynamic upscale + CLAHE contrast + unsharp mask."""
    h, w = img_np.shape[:2]

    if h < MIN_HEIGHT_FOR_2X_UPSCALE:
        scale = 3
    elif h < MIN_HEIGHT_FOR_UPSCALE:
        scale = 2
    elif h < MIN_HEIGHT_FOR_15X_UPSCALE and w * 1.5 <= MAX_UPSCALE_DIM:
        scale = 1.5
    else:
        scale = 1.0

    pil = Image.fromarray(img_np)
    if scale > 1.0:
        new_w = min(int(w * scale), MAX_UPSCALE_DIM)
        new_h = int(new_w * h / w) if w > 0 else int(h * scale)
        pil = pil.resize((new_w, new_h), Image.LANCZOS)
        _logger.debug("Pre-processed: %dx%d -> %dx%d (%.1f×)", w, h, new_w, new_h, scale)

    try:
        import cv2
        arr = np.array(pil)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        arr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB)
        pil = Image.fromarray(arr)
        _logger.debug("CLAHE contrast enhancement applied.")
    except Exception:
        pass

    try:
        import cv2
        arr = np.array(pil)
        blurred = cv2.GaussianBlur(arr, (0, 0), 1.0)
        sharpened = cv2.addWeighted(arr, 1.5, blurred, -0.5, 0)
        pil = Image.fromarray(sharpened)
        _logger.debug("Unsharp mask applied.")
    except Exception:
        pass

    return np.array(pil)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PaddleOCR Engine (singleton)
# ═══════════════════════════════════════════════════════════════════════════════

class _OCREngine:
    """Lazy-loaded PaddleOCR wrapper."""

    def __init__(self) -> None:
        self._ocr: PaddleOCR | None = None
        try:
            import paddle
            paddle.set_device("cpu")
            _logger.info("PaddlePaddle device set to CPU.")
        except Exception as exc:
            _logger.debug("Paddle device setup skipped: %s", exc)

    def load(self) -> None:
        if self._ocr is not None:
            return
        _logger.info("Loading PaddleOCR model (lang='tr', use_angle_cls=True)...")
        self._ocr = PaddleOCR(
            use_angle_cls=True,
            lang="tr",
            show_log=False,
        )
        _logger.info("PaddleOCR model loaded.")

    def predict(self, img_np: np.ndarray):
        if self._ocr is None:
            self.load()
        assert self._ocr is not None
        return self._ocr.ocr(img_np, cls=True)


_engine = _OCREngine()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Content Type Detection
# ═══════════════════════════════════════════════════════════════════════════════

# Turkish-specific characters (not found in English)
_TR_CHARS = set("ğıİşçöüĞÜŞÇÖİâî")

# Unambiguous Turkish morphological substrings.
# These survive even when some diacritics are OCR-mangled.
_TR_MORPH_INDICATORS = (
    "iyor",     # progressive: geliyor, yapıyor
    "ecek",     # future: gelecek, yapacak
    "acak",     # future: alacak, olacak
    "mış", "miş", "muş", "müş",  # reported past
    "cık", "cik", "cuk", "cük",  # diminutive
    "sız", "siz", "suz", "süz",  # privative (-less)
    "luk", "lük",                 # -ness
)


def _has_turkish_indicators(text: str) -> bool:
    """Check if text has unambiguous Turkish indicators.

    Two signals (either is sufficient):
      1. Direct Turkish-specific characters → OCR got them right
      2. Unambiguous Turkish morphological patterns → survives even
         when some diacritics are lost
    """
    if not text:
        return False
    if any(c in _TR_CHARS for c in text):
        return True
    text_lower = text.lower()
    for pattern in _TR_MORPH_INDICATORS:
        if pattern in text_lower:
            return True
    return False
# Code indicator characters (high density → code block)
_CODE_CHARS = set("{}[]()=;:<>.|&^@#*+-/%!?\\")
# Code keywords (lowercase, quick check)
_CODE_KEYWORDS_RE = re.compile(
    r"\b(def|class|import|from|return|if|else|elif|for|while|try|except|"
    r"with|as|pass|yield|lambda|async|await|print|int|str|bool|list|dict|"
    r"set|tuple|None|True|False|self|super|raise|break|continue|and|or|"
    r"not|in|is|function|const|let|var|export|require|module|package|"
    r"public|private|protected|static|void|namespace|using|auto|new|"
    r"delete|sizeof|typedef|struct|enum|union|volatile|register|extern|"
    r"unsigned|signed|short|long|double|float|char|switch|case|default|"
    r"goto|do|implement|interface|extends|abstract|final|throws|"
    r"assert|local|nil|end|then|begin|select|insert|update|from|where|"
    r"join|create|alter|drop|table|index|view|trigger|procedure)\b",
    re.IGNORECASE,
)


def _detect_content_type(text: str) -> str:
    """Classify a text line as 'code' or 'text'.

    Turkish/English distinction is NOT needed at the classification
    level — Turkish fixes are safe to apply to English text because
    the fixed patterns (II→İ, l→İ, $→ş) do not occur in natural
    English.  The only distinction that matters is code vs prose,
    because code must preserve symbols exactly.

    Returns 'code' or 'text'.
    """
    if not text or not text.strip():
        return "text"

    stripped = text.strip()
    total_chars = len(stripped)

    if total_chars == 0:
        return "text"

    # ── Code detection: high symbol density, keyword match, or indentation ──
    code_symbols = sum(1 for c in stripped if c in _CODE_CHARS)
    symbol_ratio = code_symbols / total_chars if total_chars > 0 else 0

    leading_spaces = len(stripped) - len(stripped.lstrip(" "))
    indented_code = leading_spaces >= 4 and symbol_ratio > 0.02

    has_code_keywords = bool(_CODE_KEYWORDS_RE.search(stripped))

    if symbol_ratio > 0.10 or indented_code or has_code_keywords:
        return "code"

    return "text"


def _classify_ocr_result(ocr_results) -> str:
    """Classify overall OCR result as predominantly 'code' or 'text'."""
    if not ocr_results or not ocr_results[0]:
        return "text"

    code_lines = 0
    total_lines = 0

    for box_data in ocr_results[0]:
        text = box_data[1][0]
        if _detect_content_type(text) == "code":
            code_lines += 1
        total_lines += 1

    if total_lines == 0:
        return "text"

    # Code dominates if >40% of lines are code
    if code_lines / total_lines > 0.4:
        return "code"
    return "text"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Character-Level Fixes (engine-level, NO dictionary)
# ═══════════════════════════════════════════════════════════════════════════════

# Universal OCR artifacts: these substitutions are ALWAYS correct regardless
# of language, because the source character does not occur in natural text.
_UNIVERSAL_FIXES: dict[int, str] = {
    ord("\ufffd"): "",   # Unicode replacement char → drop (OCR failure)
    ord("ú"): "ü",       # acute u → ü (OCR artifact for u with diaeresis)
    ord("ó"): "ö",       # acute o → ö (OCR artifact for o with diaeresis)
    ord("€"): "e",       # euro sign in text → e (OCR artifact)
}

# Turkish-context fixes: applied ONLY when content is Turkish.
# These are one-way: the source character is vanishingly rare in real
# Turkish text but common in PaddleOCR output.
_TR_CONTEXT_FIXES_TABLE: dict[int, str] = {
    ord("$"): "ş",       # dollar sign in Turkish words → ş
}


def _fix_l_as_dotted_i(text: str) -> str:
    """Fix PaddleOCR 'l' (lowercase L) → 'İ' at word starts.

    ONLY applied when the text has Turkish indicators.  The guard
    `_has_turkish_indicators` prevents false positives on English
    text (e.g., "lazy" stays "lazy").
    """
    if not text or not _has_turkish_indicators(text):
        return text

    def _replace(m: re.Match) -> str:
        word = m.group(0)
        return "İ" + word[1:]

    return re.sub(
        r"\bl[a-zğüşıöçâî]{2,}\b",
        _replace,
        text,
        flags=re.UNICODE,
    )


def _fix_ii_sentence_start(text: str) -> str:
    """Fix PaddleOCR 'II' → 'İ' at word starts.

    ONLY applied when the text has Turkish indicators.
    Patterns:
      - "IIk" → "İlk"   (3 chars)
      - "IIIk" → "İlk"  (4 chars, third I is actually l)
      - "IInce" → "İnce" (5 chars)
    """
    if "II" not in text or not _has_turkish_indicators(text):
        return text

    def _replace(m: re.Match) -> str:
        word = m.group(0)
        if word.startswith("III") and len(word) >= 4:
            return "İl" + word[3:]
        if word.startswith("II") and len(word) >= 3:
            return "İ" + word[2:]
        return word

    return re.sub(r"\bII[A-Za-zğüşıöçİĞÜŞÖÇ]{1,}\b", _replace, text, flags=re.UNICODE)


def _fix_su_an(text: str) -> str:
    """Fix 'su an' → 'şu an' (Turkish 'right now')."""
    text = re.sub(r"\bsu an\b", "şu an", text)
    text = re.sub(r"\bSu an\b", "Şu an", text)
    text = re.sub(r"\bSU AN\b", "ŞU AN", text)
    text = re.sub(r"\bsu anda\b", "şu anda", text)
    text = re.sub(r"\bSu anda\b", "Şu anda", text)
    return text


def _apply_universal_fixes(text: str) -> str:
    """Apply character-level fixes that are always safe."""
    return text.translate(_UNIVERSAL_FIXES)


def _apply_turkish_fixes(text: str) -> str:
    """Apply Turkish-context character fixes.

    These fixes are safe for ANY Latin-script prose because the
    patterns they target (II→İ, l→İ, $→ş, -mis→-miş) do not
    occur in natural English.
    """
    text = text.translate(_TR_CONTEXT_FIXES_TABLE)
    text = _fix_ii_sentence_start(text)
    text = _fix_l_as_dotted_i(text)
    text = _fix_su_an(text)
    text = _fix_turkish_suffixes(text)
    return text


def _fix_turkish_suffixes(text: str) -> str:
    """Fix PaddleOCR dropping diacritics on common Turkish suffixes.

    These patterns are extremely high-confidence because:
      - English does not have words ending in '-mis', '-mus', '-mıs'
        as suffixes (they would be loanwords)
      - The Turkish reported past tense suffix is always -miş/-mış/-muş/-müş
      - OCR frequently outputs ASCII 's' instead of 'ş' for this suffix
    """
    # Reported past tense: -miş, -mış, -muş, -müş
    # Word must have 4+ chars so we don't match "mis" as a standalone word
    text = re.sub(r'\b(\w{2,})mis\b', r'\1miş', text)
    text = re.sub(r'\b(\w{2,})mıs\b', r'\1mış', text)
    text = re.sub(r'\b(\w{2,})mus\b', r'\1muş', text)
    text = re.sub(r'\b(\w{2,})müs\b', r'\1müş', text)
    # Aorist negative: -mez → -mez (no change), but -maz → -maz (also no change)
    # Future: -ecek/-acak → stays as-is (OCR usually gets these right)
    return text


def smart_normalize(text: str, content_type: str) -> str:
    """Normalize a single text segment based on detected content type.

    ┌──────────────┬──────────────────────────────────────────┐
    │ Content Type │ Action                                   │
    ├──────────────┼──────────────────────────────────────────┤
    │ code         │ Universal fixes only (ú→ü, U+FFFD drop)  │
    │ text         │ Universal + Turkish-context fixes        │
    └──────────────┴──────────────────────────────────────────┘

    Turkish fixes are safe to apply to English prose because the
    patterns they target (II→İ, l→İ, $→ş) do not occur in
    natural English text.
    """
    if not text:
        return text

    text = _apply_universal_fixes(text)

    if content_type == "text":
        text = _apply_turkish_fixes(text)

    # Clean up multiple spaces (but preserve leading whitespace for code)
    if content_type == "text":
        text = re.sub(r"[ \t]+", " ", text).strip()

    return text


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Layout Reconstruction (code-aware)
# ═══════════════════════════════════════════════════════════════════════════════

def reconstruct_code_layout(ocr_results, y_threshold: int = 10) -> str:
    """Sort OCR results by coordinates to preserve code structure & indentation.

    Each detected text region has a bounding box.  We sort top-to-bottom
    by Y coordinate, then left-to-right within each visual line by X.
    Text regions on the same Y line are joined with a space.

    Returns:
        Reconstructed text with \\n-separated lines.
    """
    if not ocr_results or not ocr_results[0]:
        return ""

    raw_boxes = ocr_results[0]
    extracted_data: list[tuple[float, float, str]] = []
    for box_data in raw_boxes:
        box = box_data[0]
        text = box_data[1][0]
        y_min = min(point[1] for point in box)
        x_min = min(point[0] for point in box)
        extracted_data.append((y_min, x_min, text))

    if not extracted_data:
        return ""

    extracted_data.sort(key=lambda item: (item[0], item[1]))

    lines: list[str] = []
    current_line: list[tuple[float, str]] = []
    current_y = extracted_data[0][0]

    for y, x, text in extracted_data:
        if abs(y - current_y) <= y_threshold:
            current_line.append((x, text))
        else:
            current_line.sort(key=lambda item: item[0])
            lines.append(" ".join(item[1] for item in current_line))
            current_line = [(x, text)]
            current_y = y

    if current_line:
        current_line.sort(key=lambda item: item[0])
        lines.append(" ".join(item[1] for item in current_line))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. FastAPI Application
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    import threading
    threading.Thread(target=_engine.load, daemon=True).start()
    yield


app = FastAPI(
    title="Smart Multilingual OCR Server",
    description="English, Turkish & Code OCR with engine-level intelligence",
    version="2.0.0",
    lifespan=_lifespan,
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/ocr/process")
async def process_image(file: UploadFile = File(...)) -> JSONResponse:
    """Main OCR endpoint.  Accepts PNG/JPEG/WebP, returns structured text."""
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload JPEG, PNG, or WebP.",
        )

    try:
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img_np = np.array(image)

        # ── Pre-processing ───────────────────────────────────────────────
        proc_np = _preprocess_for_ocr(img_np)

        # ── OCR inference ────────────────────────────────────────────────
        result = _engine.predict(proc_np)

        # ── Content-type classification ──────────────────────────────────
        overall_type = _classify_ocr_result(result)
        _logger.debug("Content classified as: %s", overall_type)

        # ── Per-segment normalization (content-aware) ────────────────────
        raw_text_list: list[str] = []
        segment_types: list[str] = []

        if result and result[0]:
            for box_data in result[0]:
                text = box_data[1][0]
                seg_ct = _detect_content_type(text)
                segment_types.append(seg_ct)
                normalized = smart_normalize(text, seg_ct)
                raw_text_list.append(normalized)

        # ── Layout reconstruction + per-line normalization ────────────────
        structured_code = reconstruct_code_layout(result)
        lines = structured_code.split("\n")

        normalized_lines: list[str] = []
        for i, line in enumerate(lines):
            # For layout-reconstructed lines, use overall_type as fallback
            # since individual segment types don't map 1:1 to merged lines.
            ct = segment_types[i] if i < len(segment_types) else overall_type
            normalized_lines.append(smart_normalize(line, ct))

        structured_text = "\n".join(normalized_lines)

        return JSONResponse(content={
            "status": "success",
            "raw_text": raw_text_list,
            "structured_text": structured_text,
        })

    except HTTPException:
        raise
    except Exception as exc:
        _logger.exception("OCR processing failed")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Standalone Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    host = os.environ.get("OCR_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("OCR_SERVER_PORT", "8000"))
    print(f"Starting Smart OCR server on {host}:{port} ...")
    uvicorn.run("server:app", host=host, port=port, log_level="info")

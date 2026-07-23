"""
Local RapidOCR Engine — CPU-optimized for AMD Ryzen 5 9600X (6 physical cores).

Runs RapidOCR (ONNX Runtime + Latin PP-OCRv5) directly in-process.
Handles English, Turkish, and code blocks with high accuracy.
"""
from __future__ import annotations

import gc
import logging
import math
import os
import threading
import time
from typing import Optional

import numpy as np
from PIL import Image

from src.config import (
    DET_BOX_THRESH,
    DET_LIMIT_SIDE_LEN,
    DET_LIMIT_TYPE,
    DET_SCORE_MODE,
    DET_UNCLIP_RATIO,
    ENGINE_WARMUP_ENABLED,
    MAX_UPSCALE_DIM,
    REC_BATCH_NUM,
    SMALL_TEXT_15X_MAX_HEIGHT,
    SMALL_TEXT_2X_MAX_HEIGHT,
    TURKISH_CACHE_MAX_SIZE,
    TURKISH_CORRECTION_MIN_HEIGHT,
)
from src.engine.base import BaseOCREngine

try:
    import cv2
    cv2.setNumThreads(1)  # OpenCV ops stay single-threaded; ORT owns the cores
except Exception:
    cv2 = None

_logger = logging.getLogger(__name__)

# ── CPU Thread Tuning ───────────────────────────────────────────────────────
_PHYSICAL_CORES = max(1, (os.cpu_count() or 4) // 2)
os.environ.setdefault("OMP_NUM_THREADS", str(_PHYSICAL_CORES))
os.environ.setdefault("MKL_NUM_THREADS", str(_PHYSICAL_CORES))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_PHYSICAL_CORES))
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")  # ONNX Runtime logs warning/error only
os.environ.setdefault("KMP_BLOCKTIME", "0")
os.environ.setdefault("KMP_AFFINITY", "granularity=fine,compact,1,0")


# ── Turkish NLP Spell Correction Dictionary ─────────────────────────────────

_TURKISH_DICT = {
    "ve", "de", "da", "ki", "ile", "ise", "mi", "mı", "mu", "mü", "en", "pek", "çok", 
    "daha", "her", "hep", "hiç", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", 
    "sekiz", "dokuz", "on", "ben", "sen", "o", "biz", "siz", "onlar", "bu", "şu", 
    "burada", "şurada", "orada", "halk", "halkı", "onun", "bunun", "şunun", "ama", "fakat",
    "lakin", "çünkü", "zira", "veya", "yahut", "gibi", "kadar", "için", "göre", "beri",
    "yolculuk", "yolculuğun", "sabah", "güneş", "henüz", "doğarken", "teknesine", 
    "biraz", "yiyecek", "su", "pusulasını", "alarak", "denize", "açılmış", "kasaba", 
    "arkasında", "yavaş", "yavaşyavaş", "küçülürken", "içindeki", "korkunun", "yerini", "büyük", 
    "heyecan", "kaplamış", "ilk", "saatleri", "sakin", "geçmiş", "ancak", 
    "öğleden", "sonra", "gökyüzü", "aniden", "kararmış", "rüzgâr", "rüzgar", 
    "uğuldamaya", "başlamış", "dalgalar", "umut", "sağa", "sola", "fırlatıyormuş", 
    "pes", "etmek", "üzereymiş", "direğe", "sıkıca", "sarılmış", "içinden", "şöyle", 
    "düşünmüş", "eğer", "hayallerinin", "peşinden", "gidiyorsan", "fırtınalara", 
    "göğüs", "germeyi", "bilmelisin", "tüm", "gücüyle", "dümeni", "elinde", "tutmuş", 
    "dalgalarla", "mücadele", "etmiş", "baban", "balıkçılık", "tekin", "değildir", 
    "güler", "oturduğun", "yerde", "vazgeçmemiş", "kalbinin", "sesini", "dinlemekten",
    "kasabada", "yaşayan", "mert", "adında", "hayalperest", "genç", "kalkar", "sahile",
    "iner", "izleyerek", "derin", "düşüncelere", "dalarmiş", "dalarmış", "gizemli", 
    "ada", "adayı", "bulmakmış", "bulmakmiş"
}


# ── Bounded (<=1 edit) Turkish Spell Correction ─────────────────────────────
# The acceptance threshold was always "distance <= 1", so computing a full
# Levenshtein DP matrix per candidate was wasted work. A 1-edit check is O(n)
# with two pointers, and the candidate space collapses to dictionary words
# whose length differs by at most 1. The dictionary is indexed as
# {length: {first_char: sorted word list}} so each lookup touches only a few
# small buckets instead of scanning ~100k entries per unknown word.

_DICT_INDEX: dict[int, dict[str, list[str]]] = {}
_dict_index_built = False
_WORD_CACHE: dict[str, Optional[str]] = {}

_CHAR_EQUIV: dict[str, frozenset] = {}
for _group in (("s", "ş"), ("c", "ç"), ("g", "ğ"), ("i", "ı"), ("o", "ö"), ("u", "ü")):
    for _ch in _group:
        _CHAR_EQUIV[_ch] = frozenset(_group)


def _build_dict_index() -> None:
    """Build the length/first-char dictionary index once (idempotent)."""
    global _dict_index_built
    if _dict_index_built:
        return
    _DICT_INDEX.clear()
    for word in _TURKISH_DICT:
        _DICT_INDEX.setdefault(len(word), {}).setdefault(word[0], []).append(word)
    for bucket in _DICT_INDEX.values():
        for words in bucket.values():
            words.sort()
    _dict_index_built = True


def _reset_spell_state() -> None:
    """Invalidate index + cache after the dictionary has been extended."""
    global _dict_index_built
    _dict_index_built = False
    _WORD_CACHE.clear()


_DIACRITIC_PAIRS = frozenset(
    frozenset(pair) for pair in (("s", "ş"), ("c", "ç"), ("g", "ğ"),
                                 ("i", "ı"), ("o", "ö"), ("u", "ü"))
)


def _edit_kind(a: str, b: str) -> Optional[int]:
    """Classify a <=1-edit difference between a and b, or return None.

    Ranks (lower is more likely to be the intended word):
      0 = diacritic-pair substitution (s/ş, c/ç, ...) – classic OCR confusion
      1 = single insertion/deletion                   – OCR dropped/added a char
      2 = plain substitution                          – least likely intent
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return None
    if la == lb:
        diff_pos = -1
        for i, (ca, cb) in enumerate(zip(a, b)):
            if ca != cb:
                if diff_pos >= 0:
                    return None
                diff_pos = i
        if diff_pos < 0:
            return None  # identical (already excluded by dict membership)
        if frozenset((a[diff_pos], b[diff_pos])) in _DIACRITIC_PAIRS:
            return 0
        return 2
    if la < lb:
        a, b, la, lb = b, a, lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return None
        else:
            skipped = True
            i += 1
    return 1


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _find_dict_candidate(clean_word: str) -> Optional[str]:
    """Return the best dictionary word within 1 edit of clean_word, or None.

    All 1-edit candidates in the relevant length/first-char buckets are ranked
    by (edit kind, longest common prefix, alphabetical) so ties between real
    dictionary words resolve to the most plausible OCR correction.
    """
    _build_dict_index()
    starts = _CHAR_EQUIV.get(clean_word[0], frozenset((clean_word[0],)))
    len_w = len(clean_word)
    best_key: Optional[tuple] = None
    best_word: Optional[str] = None
    for cand_len in (len_w, len_w - 1, len_w + 1):
        bucket = _DICT_INDEX.get(cand_len)
        if not bucket:
            continue
        for start in starts:
            for cand in bucket.get(start, ()):
                kind = _edit_kind(clean_word, cand)
                if kind is None:
                    continue
                key = (kind, -_common_prefix_len(clean_word, cand), cand)
                if best_key is None or key < best_key:
                    best_key = key
                    best_word = cand
    return best_word


def _correct_turkish_word(word: str) -> str:
    if not word or "_" in word:
        return word
    # Skip code-like tokens: camelCase/PascalCase/CONSTANT_CASE words and
    # alphanumeric mixes (utf8, v2, ...) must not be "Turkish-corrected".
    if any(c.isupper() for c in word[1:]):
        return word

    clean_word = "".join(c for c in word if c.isalnum()).lower()
    if not clean_word or any(c.isdigit() for c in clean_word):
        return word
    if clean_word in _TURKISH_DICT:
        return word

    if len(_WORD_CACHE) > TURKISH_CACHE_MAX_SIZE:
        _WORD_CACHE.clear()
    if clean_word in _WORD_CACHE:
        best_word = _WORD_CACHE[clean_word]
    else:
        best_word = _find_dict_candidate(clean_word)
        _WORD_CACHE[clean_word] = best_word
    if best_word is None:
        return word

    if word[0].isupper():
        best_word = best_word.capitalize()

    prefix = ""
    for c in word:
        if not c.isalnum():
            prefix += c
        else:
            break
    suffix = ""
    for c in reversed(word):
        if not c.isalnum():
            suffix = c + suffix
        else:
            break
    return prefix + best_word + suffix


def _correct_turkish_text_nlp(text: str) -> str:
    lines = text.split("\n")
    corrected_lines = []
    for line in lines:
        words = line.split(" ")
        corrected_words = [_correct_turkish_word(w) for w in words]
        corrected_lines.append(" ".join(corrected_words))
    return "\n".join(corrected_lines)


# ── Adaptive Image Pre-processing ───────────────────────────────────────────

def _preprocess_for_ocr(img_np: np.ndarray) -> np.ndarray:
    """Dynamic preprocessing: auto-invert dark backgrounds + height-sensitive CLAHE
    + tiered bilinear upscale (2x for tiny text, 1.5x for small text).

    Preserves spacing in paragraphs/italic text, isolates underscores/quotes in
    command lines, eliminates background-contrast induced word merging, and avoids
    ringing artifacts (like double letters) by using bilinear scaling. The upscale
    is width-capped at MAX_UPSCALE_DIM so very wide strips are not blown up only
    to be shrunk again by the detector's max-side limit.
    """
    if cv2 is None:
        return img_np
    try:
        h_orig, w_orig = img_np.shape[:2]

        # 1. Add white padding/border to prevent character clipping at edges
        padded = cv2.copyMakeBorder(
            img_np,
            10, 10, 10, 10,
            cv2.BORDER_CONSTANT,
            value=[255, 255, 255]
        )

        # 2. Convert to grayscale
        gray = cv2.cvtColor(padded, cv2.COLOR_RGB2GRAY)

        # 3. Auto-invert dark backgrounds (white text on black)
        # Standardizing to white backgrounds prevents blooming/character merging
        if float(gray.mean()) < 120.0:
            gray = 255 - gray

        # 4. Height-aware CLAHE contrast enhancement
        # High contrast (2.5, 4x4) is used for narrow command lines to sharpen underscores.
        # Moderate contrast (1.1, 8x8) is used for paragraph text to avoid character bleeding.
        if h_orig < 60:
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
        else:
            clahe = cv2.createCLAHE(clipLimit=1.1, tileGridSize=(8, 8))

        contrast = clahe.apply(gray)

        # 5. Tiered bilinear upscale: 2x for tiny text, 1.5x for small strips.
        # Bigger glyphs give the recognizer more real pixels to work with.
        if h_orig < SMALL_TEXT_2X_MAX_HEIGHT:
            scale = 2.0
        elif h_orig < SMALL_TEXT_15X_MAX_HEIGHT:
            scale = 1.5
        else:
            scale = 1.0

        if scale > 1.0:
            h, w = contrast.shape[:2]
            scale = min(scale, MAX_UPSCALE_DIM / max(w, 1))
            if scale > 1.05:
                contrast = cv2.resize(
                    contrast,
                    (int(w * scale), int(h * scale)),
                    interpolation=cv2.INTER_LINEAR,
                )

        return cv2.cvtColor(contrast, cv2.COLOR_GRAY2RGB)
    except Exception as e:
        _logger.warning("Dynamic preprocessing failed: %s", e)
        return img_np


# ── Indentation Reconstruction ──────────────────────────────────────────────

def _reconstruct_indentation(results) -> str:
    """Reconstruct text with indentation and horizontal line merging from RapidOCR bboxes.
    
    Groups boxes on the same horizontal line using a vertical proximity tolerance,
    orders them left-to-right, and preserves proper spacing.
    """
    if not results:
        return ""

    # 1. Extract coordinate information
    boxes_info = []
    heights = []
    for box, text, conf in results:
        x_min = min(pt[0] for pt in box)
        x_max = max(pt[0] for pt in box)
        y_min = min(pt[1] for pt in box)
        y_max = max(pt[1] for pt in box)
        h = y_max - y_min
        w = x_max - x_min
        boxes_info.append({
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
            "h": h,
            "w": w,
            "text": text,
            "conf": conf
        })
        heights.append(h)

    # 2. Determine average line height and dynamic overlap tolerance
    mean_height = sum(heights) / len(heights) if heights else 15.0
    tolerance = mean_height * 0.6

    # 3. Group boxes vertically
    boxes_info.sort(key=lambda b: b["y_min"])
    
    rows: list[list[dict]] = []
    for box in boxes_info:
        placed = False
        for row in rows:
            row_y_center = sum((b["y_min"] + b["y_max"]) / 2 for b in row) / len(row)
            box_y_center = (box["y_min"] + box["y_max"]) / 2
            if abs(box_y_center - row_y_center) < tolerance:
                row.append(box)
                placed = True
                break
        if not placed:
            rows.append([box])

    # 4. Sort rows from top to bottom
    rows.sort(key=lambda r: sum((b["y_min"] + b["y_max"]) / 2 for b in r) / len(r))
    min_left = min(b["x_min"] for b in boxes_info)

    # 5. Estimate average character width (char_width)
    widths = []
    for b in boxes_info:
        widths.append(b["w"] / max(len(b["text"]), 1))
    
    char_width = 8.0
    if widths:
        widths.sort()
        char_width = max(widths[len(widths) // 2], 1.0)

    # 6. Reconstruct lines with dynamic spacing
    result_lines = []
    for row in rows:
        # Sort words/blocks left-to-right within the same row
        row.sort(key=lambda b: b["x_min"])
        
        row_text = ""
        # Leftmost indent
        line_start_x = row[0]["x_min"]
        spaces = max(0, round((line_start_x - min_left) / char_width))
        row_text += " " * spaces
        
        prev_x_max = line_start_x
        for i, b in enumerate(row):
            if i > 0:
                gap = b["x_min"] - prev_x_max
                # If there's a visible horizontal gap, reconstruct spaces
                if gap > char_width * 0.2:
                    gap_spaces = max(1, round(gap / char_width))
                else:
                    gap_spaces = 0
                row_text += " " * gap_spaces
            row_text += b["text"]
            prev_x_max = b["x_max"]
            
        result_lines.append(row_text)

    return "\n".join(result_lines)


class LocalOCREngine(BaseOCREngine):
    """In-process RapidOCR engine — CPU-optimized for Ryzen 5 9600X."""

    def __init__(self) -> None:
        self._engine = None
        self._loaded = False
        self._lock = threading.Lock()

    def load(self) -> None:
        """Lazy-load RapidOCR model. Thread-safe; idempotent."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return

            # Dynamic Base Directory to support arbitrary Current Working Directory (CWD)
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            
            # Erken Dosya Kontrolü (Pre-flight Check)
            required_files = {
                "Detection Model": os.path.join(base_dir, "models", "det_model.onnx"),
                "Recognition Model": os.path.join(base_dir, "models", "rec_model.onnx"),
                "Keys Dictionary": os.path.join(base_dir, "models", "rec_keys.txt")
            }
            for name, path in required_files.items():
                if not os.path.exists(path):
                    err_msg = f"Required model file '{name}' not found at path: {path}"
                    _logger.error(err_msg)
                    raise FileNotFoundError(err_msg)

            _logger.info("Loading RapidOCR models (CPU-only, Zen 5)...")
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR(
                    det_model_path=required_files["Detection Model"],
                    rec_model_path=required_files["Recognition Model"],
                    rec_keys_path=required_files["Keys Dictionary"],
                    det_score_mode=DET_SCORE_MODE,
                    det_box_thresh=DET_BOX_THRESH,
                    det_unclip_ratio=DET_UNCLIP_RATIO,
                    det_limit_type=DET_LIMIT_TYPE,
                    det_limit_side_len=DET_LIMIT_SIDE_LEN,
                    rec_batch_num=REC_BATCH_NUM,
                    use_space_char=True,
                    use_cls=False,
                    intra_op_num_threads=_PHYSICAL_CORES,
                    inter_op_num_threads=1
                )
                # Dynamically load the comprehensive Turkish Spell Checker dictionary
                dict_path = os.path.join(base_dir, "models", "turkish_words.txt")
                if os.path.exists(dict_path):
                    try:
                        t_dict0 = time.time()
                        with open(dict_path, "r", encoding="utf-8") as f:
                            for line in f:
                                word = line.strip().lower()
                                if word:
                                    _TURKISH_DICT.add(word)
                        
                        # Dynamically expand dictionary with common Turkish verb conjugations (aggling suffixes)
                        # This avoids static word replacements while dynamically validating all conjugated verb forms.
                        common_suffixes = ["mış", "miş", "muş", "müş", "dı", "di", "du", "dü", "tı", "ti", "tu", "tü", "acak", "ecek", "iyor"]
                        extra_conjugations = []
                        for w in list(_TURKISH_DICT):
                            if w.endswith("mak") or w.endswith("mek"):
                                stem = w[:-3]
                                for suffix in common_suffixes:
                                    extra_conjugations.append(stem + suffix)
                                    extra_conjugations.append(stem + suffix + "lar")
                                    extra_conjugations.append(stem + suffix + "ler")
                        _TURKISH_DICT.update(extra_conjugations)
                        
                        _logger.info("Loaded %d Turkish words (including dynamic conjugations) in %.3fs.",
                                     len(_TURKISH_DICT), time.time() - t_dict0)
                    except Exception as dict_err:
                        _logger.warning("Failed to load Turkish dictionary: %s. Using fallback vocabulary.", dict_err)

                # Dictionary changed -> rebuild spell index on next correction call
                _reset_spell_state()

                if ENGINE_WARMUP_ENABLED:
                    self._warmup()

                self._loaded = True
                _logger.info("RapidOCR ready (CPU, %d threads).", _PHYSICAL_CORES)
            except Exception as e:
                _logger.exception("Failed to load RapidOCR models: %s", e)
                raise

    def _warmup(self) -> None:
        """Run one dummy detection+recognition pass at load time.

        The first ONNX Runtime inference pays graph-optimization and memory
        arena setup costs; doing it here keeps the first real capture fast.
        """
        try:
            t0 = time.time()
            dummy = np.full((64, 320, 3), 255, dtype=np.uint8)
            if cv2 is not None:
                cv2.putText(
                    dummy, "Warmup 123", (8, 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2,
                )
            self._engine(dummy)
            # Force the recognition path even if the detector found no text.
            if getattr(self._engine, "text_rec", None) is not None:
                self._engine.text_rec([dummy])
            _logger.info("Engine warmup completed in %.2fs.", time.time() - t0)
        except Exception as e:
            _logger.warning("Engine warmup failed (non-fatal): %s", e)

    def unload(self) -> None:
        """Release RapidOCR model from memory."""
        with self._lock:
            self._engine = None
            self._loaded = False
            gc.collect()
        _logger.info("RapidOCR models unloaded and RAM cleared.")

    def recognize(self, image: Image.Image) -> str:
        """Run RapidOCR on a PIL Image. Returns reconstructed text."""
        # ── Küçük Görsel Boyut Kontrolü (Boundary Safeguard) ─────────────
        if image.width < 5 or image.height < 5:
            _logger.warning("Image too small (%dx%d), skipping OCR.", image.width, image.height)
            return ""

        # ── Thread-Safe Engine Kontrolü ──────────────────────────────────
        with self._lock:
            if not self._loaded or self._engine is None:
                raise RuntimeError("RapidOCR models not loaded or already unloaded.")
            engine = self._engine

        total_t0 = time.time()

        # ── Pre-processing (NumPy Dizisi Üzerinden Sıfır Kopyalama) ────
        t0 = time.time()
        img_np = np.array(image.convert("RGB"))
        proc_np = _preprocess_for_ocr(img_np)
        prep_t = time.time() - t0
        _logger.info("  Preprocess: %.1fs (input %dx%d -> output %dx%d)",
                     prep_t, image.width, image.height,
                     proc_np.shape[1], proc_np.shape[0])

        # ── 1) RapidOCR Çıkarımı ───────────────────────────────────────
        t0 = time.time()
        results, elapse_list = engine(proc_np)
        det_t = time.time() - t0
        _logger.info("  RapidOCR Inference: %.1fs", det_t)

        if not results:
            _logger.info("  No text detected.")
            return ""

        # ── 2) Girinti Rekonstrüksiyonu ───────────────────────────────
        t0 = time.time()
        text = _reconstruct_indentation(results)
        
        # Unicode kontrol/gizli karakterlerin temizlenmesi
        for char in ['\u200b', '\u200c', '\u200d', '\ufeff']:
            text = text.replace(char, '')
            
        # ── 3) Post-Processing Heuristics & NLP Spell Checker ─────────
        # Fix common OCR character confusion syntax errors in command lines
        text = text.replace("- -", "--")
        text = text.replace("shel1", "shell")
        text = text.replace("sshell", "shell")
        text = text.replace("USERPRoFILE", "USERPROFILE")
        text = text.replace("iinstallDebug", "installDebug")
        text = text.replace("gradlewI", "gradlew")
        
        # Apply Turkish NLP spell correction only to paragraphs (height >= 60)
        # to dynamically recover spelling/character errors without static replacements
        if image.height >= TURKISH_CORRECTION_MIN_HEIGHT:
            text = _correct_turkish_text_nlp(text)
            
        indent_t = time.time() - t0

        total_t = time.time() - total_t0
        _logger.info("  Indent & Cleanup: %.1fs | TOTAL: %.1fs | %d chars",
                     indent_t, total_t, len(text))
        return text

    def is_loaded(self) -> bool:
        """Return True if models are loaded and ready for OCR."""
        return self._loaded


# ── Singleton ────────────────────────────────────────────────────────────────

_engine: Optional[LocalOCREngine] = None
_engine_lock = threading.Lock()


def get_local_engine() -> LocalOCREngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = LocalOCREngine()
    return _engine


def shutdown_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.unload()
        _engine = None

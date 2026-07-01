"""
Local Surya OCR Engine — optimized for AMD Ryzen 5 9600X (6-core) + RX 6650 XT.

Runs Surya OCR directly in-process.  Handles English, Turkish, and code
blocks with high accuracy.  Surya's multilingual recognition model is
significantly better than PaddleOCR at Turkish diacritics.

Optimizations (2026-07-01):
  * 90% CPU utilization — threads = max(1, floor(cores * 0.9))
  * FOUNDATION_MODEL_QUANTIZE=True — int8 quantization for CPU speed
  * FOUNDATION_MAX_TOKENS=512 — limit recognition output length
  * torch.inference_mode() — disable autograd overhead
  * attention_implementation="sdpa" — faster scaled dot-product attention
  * DETECTOR_BATCH_SIZE=1, RECOGNITION_BATCH_SIZE=32 — tuned for single-image
  * DETECTOR_IMAGE_CHUNK_HEIGHT=1400 — balanced memory/speed
  * Adaptive preprocessing: skip upscale for >=250px height
  * Indentation reconstructed from TextLine polygon x-coordinates
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Optional

import numpy as np
from PIL import Image

from src.engine.base import BaseOCREngine

_logger = logging.getLogger(__name__)

# ── Torch / GPU Tuning ──────────────────────────────────────────────────────
# Physical cores for CPU fallback (Ryzen 5 9600X: 6)
_PHYSICAL_CORES = max(1, (os.cpu_count() or 4) // 2)
os.environ.setdefault("OMP_NUM_THREADS", str(_PHYSICAL_CORES))
os.environ.setdefault("MKL_NUM_THREADS", str(_PHYSICAL_CORES))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_PHYSICAL_CORES))
os.environ.setdefault("KMP_BLOCKTIME", "0")
os.environ.setdefault("KMP_AFFINITY", "granularity=fine,compact,1,0")


def _get_torch_device() -> str:
    """Detect best available torch device.

    DirectML (AMD GPU) is DISABLED — torch-directml 0.2.5 cannot run
    Surya's transformer model correctly (float→bool corruption, uint8
    overflow, version_counter, device mismatch, masked_fill scatter bugs).
    CPU-only for now.  See git history for DirectML attempts.
    """
    return "cpu"


def _configure_torch() -> None:
    """Configure torch for CPU or GPU + apply compatibility patches."""
    try:
        import torch
        import torch.nn.utils.rnn as rnn_utils

        # ── Patch pad_sequence for torch<2.7 + surya compatibility ───
        # surya-ocr requires padding_side param (torch>=2.7 API).
        # torch-directml ships torch 2.4.1 which lacks it.
        _orig_pad = rnn_utils.pad_sequence
        def _patched_pad(sequences, batch_first=False, padding_value=0.0,
                         padding_side='right', **kw):
            if padding_side == 'left':
                rev = [s.flip(0) for s in sequences]
                p = _orig_pad(rev, batch_first=batch_first, padding_value=padding_value)
                return p.flip(1) if batch_first else p.flip(0)
            return _orig_pad(sequences, batch_first=batch_first,
                           padding_value=padding_value)
        rnn_utils.pad_sequence = _patched_pad

        # ── Patch CUDA bf16 check for non-CUDA GPU devices ───────────
        # Surya calls torch.cuda.is_bf16_supported() even on DirectML.
        _orig_bf16 = torch.cuda.is_bf16_supported
        def _patched_bf16(*args, **kw):
            try:
                return _orig_bf16(*args, **kw)
            except (AssertionError, RuntimeError):
                return False
        torch.cuda.is_bf16_supported = _patched_bf16

        # ── Patch masked_fill for DirectML ───────────────────────────
        # DirectML masked_fill internally casts to uint8 and overflows
        # on large float values.  Catch and retry on CPU.
        _orig_masked_fill = torch.Tensor.masked_fill
        def _dml_masked_fill(self, mask, value):
            try:
                return _orig_masked_fill(self, mask, value)
            except RuntimeError as e:
                if "uint8" in str(e) and self.device.type == "privateuseone":
                    return _orig_masked_fill(
                        self.to("cpu"), mask.to("cpu"), value
                    ).to(self.device)
                raise
        torch.Tensor.masked_fill = _dml_masked_fill

        _orig_masked_fill_ = torch.Tensor.masked_fill_
        def _dml_masked_fill_(self, mask, value):
            try:
                return _orig_masked_fill_(self, mask, value)
            except RuntimeError as e:
                if "uint8" in str(e) and self.device.type == "privateuseone":
                    cpu_result = _orig_masked_fill_(
                        self.to("cpu"), mask.to("cpu"), value
                    )
                    self.copy_(cpu_result.to(self.device))
                    return self
                raise
        torch.Tensor.masked_fill_ = _dml_masked_fill_

        # ── Patch scatter for DirectML dtype mismatch ────────────────
        # DirectML is strict about dtype matching; torch CPU is lenient.
        # Fix: auto-cast src to self's dtype before scatter.
        _orig_scatter = torch.Tensor.scatter
        def _dml_scatter(self, dim, index, src, *args, **kwargs):
            if src.dtype != self.dtype:
                src = src.to(self.dtype)
            try:
                return _orig_scatter(self, dim, index, src, *args, **kwargs)
            except RuntimeError as e:
                if self.device.type == "privateuseone":
                    return _orig_scatter(
                        self.to("cpu"), dim, index.to("cpu"),
                        src.to("cpu"), *args, **kwargs
                    ).to(self.device)
                raise
        torch.Tensor.scatter = _dml_scatter

        _orig_scatter_ = torch.Tensor.scatter_
        def _dml_scatter_(self, dim, index, src, *args, **kwargs):
            # DirectML sometimes produces bool tensors where float expected.
            # Don't cast bool→float (True→1.0 corrupts KV cache). 
            if src.dtype == torch.bool and self.dtype != torch.bool:
                # Try on CPU directly — DML corrupted the tensor
                cpu_self = self.to("cpu")
                cpu_result = _orig_scatter_(
                    cpu_self, dim, index.to("cpu"),
                    src.to(torch.float32).to("cpu"), *args, **kwargs
                )
                self.copy_(cpu_result.to(self.device))
                return self
            if src.dtype != self.dtype:
                src = src.to(self.dtype)
            try:
                return _orig_scatter_(self, dim, index, src, *args, **kwargs)
            except RuntimeError as e:
                if self.device.type == "privateuseone":
                    cpu_self = self.to("cpu")
                    cpu_result = _orig_scatter_(
                        cpu_self, dim, index.to("cpu"),
                        src.to("cpu"), *args, **kwargs
                    )
                    self.copy_(cpu_result.to(self.device))
                    return self
                raise
        torch.Tensor.scatter_ = _dml_scatter_

        # CPU thread tuning
        torch.set_num_threads(_PHYSICAL_CORES)
        if hasattr(torch, "set_num_interop_threads"):
            torch.set_num_interop_threads(_PHYSICAL_CORES)
        if hasattr(torch, "set_float32_matmul_precision"):
            torch.set_float32_matmul_precision("high")
        if hasattr(torch.backends, "mkldnn"):
            torch.backends.mkldnn.enabled = True
    except Exception:
        pass


def _boost_process_priority() -> int:
    """Process priority manager. Returns old priority for restore.

    We stay at NORMAL — the thread count already drives CPU to 90%.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        return kernel32.GetPriorityClass(kernel32.GetCurrentProcess())
    except Exception:
        return -1


def _restore_process_priority(old: int) -> None:
    """Restore process priority (no-op — we don't change it)."""
    pass


# ── Adaptive Image Pre-processing ───────────────────────────────────────────

MIN_HEIGHT_FOR_2X_UPSCALE = 36
MIN_HEIGHT_FOR_15X_UPSCALE = 80
MAX_DETECTION_WIDTH = 900     # balance speed/quality
MAX_DETECTION_HEIGHT = 600
MAX_UPSCALE_DIM = 2800


def _preprocess_for_ocr(img_np: np.ndarray) -> np.ndarray:
    """Smart resize + light sharpen for all images.

    Detection scales with pixel count, but recognition quality benefits
    from sharper edges.  Light unsharp mask applied to ALL images.
    """
    h, w = img_np.shape[:2]
    scale = 1.0

    if h < MIN_HEIGHT_FOR_2X_UPSCALE:
        scale = 2.0
    elif h < MIN_HEIGHT_FOR_15X_UPSCALE and w * 1.5 <= MAX_UPSCALE_DIM:
        scale = 1.5

    if scale == 1.0:
        if w > MAX_DETECTION_WIDTH:
            scale = MAX_DETECTION_WIDTH / w
        if h > MAX_DETECTION_HEIGHT:
            scale = min(scale, MAX_DETECTION_HEIGHT / h) if scale < 1.0 else MAX_DETECTION_HEIGHT / h

    pil = Image.fromarray(img_np)
    if scale != 1.0:
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        pil = pil.resize((new_w, new_h), Image.LANCZOS)

    # ── Light sharpen for medium/small images ────────────────────────
    # Large images (≥800px wide) already have clear text edges.
    if w < 800 or h < 200:
        try:
            import cv2
            cv2.setNumThreads(1)
            arr = np.array(pil)
            blurred = cv2.GaussianBlur(arr, (0, 0), 0.6)
            arr = cv2.addWeighted(arr, 1.2, blurred, -0.2, 0)
            pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        except Exception:
            pass

    # ── Extra enhancement for upscaled tiny images ─────────────────────
    if scale > 1.0:
        try:
            import cv2
            cv2.setNumThreads(1)
            arr = np.array(pil)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
            pil = Image.fromarray(cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2RGB))
        except Exception:
            pass

    return np.array(pil)


# ── Indentation Reconstruction ──────────────────────────────────────────────

def _reconstruct_indentation(text_lines) -> str:
    """Reconstruct text with indentation from Surya TextLine polygons."""
    if not text_lines:
        return ""

    lines_info: list[tuple[float, float, str]] = []
    for line in text_lines:
        poly = line.polygon
        x_min = min(p[0] for p in poly)
        y_min = min(p[1] for p in poly)
        lines_info.append((y_min, x_min, line.text))

    if not lines_info:
        return ""

    lines_info.sort(key=lambda item: item[0])
    min_left = min(item[1] for item in lines_info)

    # Estimate char width from median
    widths: list[float] = []
    for line in lines_info:
        for tl in text_lines:
            if tl.text == line[2]:
                poly = tl.polygon
                w = max(p[0] for p in poly) - min(p[0] for p in poly)
                widths.append(w / max(len(line[2]), 1))
                break

    char_width = 8.0
    if widths:
        widths.sort()
        char_width = max(widths[len(widths) // 2], 1.0)

    result_lines: list[str] = []
    for y, x, text in lines_info:
        spaces = max(0, round((x - min_left) / char_width))
        result_lines.append(" " * spaces + text)

    return "\n".join(result_lines)


# ── DirectML GPU patch: vision encoder → CPU ─────────────────────────────────

def _patch_vision_encoder_cpu(foundation_predictor) -> None:
    """Patch: entire prediction_loop on CPU, model back to GPU after."""
    import torch
    model = foundation_predictor.model
    device = next(model.parameters()).device
    if device.type != "privateuseone":
        return

    _orig_loop = foundation_predictor.prediction_loop

    def _cpu_loop(*args, **kwargs):
        model.to("cpu")
        for name in ("device_pad_token", "device_beacon_token", "special_token_ids"):
            t = getattr(foundation_predictor, name, None)
            if isinstance(t, torch.Tensor):
                setattr(foundation_predictor, name, t.to("cpu"))
        try:
            return _orig_loop(*args, **kwargs)
        finally:
            model.to(device)
            for name in ("device_pad_token", "device_beacon_token", "special_token_ids"):
                t = getattr(foundation_predictor, name, None)
                if isinstance(t, torch.Tensor):
                    setattr(foundation_predictor, name, t.to(device))

    foundation_predictor.prediction_loop = _cpu_loop
    _logger.info("  Recognition → CPU (DirectML safety), detection → GPU skipped.")

class LocalOCREngine(BaseOCREngine):
    """In-process Surya OCR engine — CPU-optimized for Ryzen 5 9600X."""

    def __init__(self) -> None:
        self._foundation = None
        self._detection = None
        self._recognition = None
        self._loaded = False
        self._lock = threading.Lock()

    def load(self) -> None:
        """Lazy-load Surya models. Thread-safe; idempotent."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return

            _configure_torch()
            _logger.info("Loading Surya OCR models (CPU)...")

            from surya.settings import settings as surya_settings

            surya_settings.DISABLE_TQDM = True
            surya_settings.DETECTOR_BATCH_SIZE = 1
            surya_settings.DETECTOR_IMAGE_CHUNK_HEIGHT = 800
            surya_settings.DETECTOR_TEXT_THRESHOLD = 0.6  # fewer false detections
            surya_settings.DETECTOR_POSTPROCESSING_CPU_WORKERS = _PHYSICAL_CORES
            surya_settings.RECOGNITION_BATCH_SIZE = 64  # more context per batch
            surya_settings.FOUNDATION_MODEL_QUANTIZE = False
            surya_settings.FOUNDATION_MAX_TOKENS = 256
            surya_settings.FOUNDATION_PAD_TO_NEAREST = 256
            surya_settings.COMPILE_DETECTOR = False
            surya_settings.COMPILE_FOUNDATION = False

            from surya.foundation import FoundationPredictor
            from surya.detection import DetectionPredictor
            from surya.recognition import RecognitionPredictor

            self._foundation = FoundationPredictor(device="cpu")
            self._detection = DetectionPredictor(device="cpu")
            self._recognition = RecognitionPredictor(self._foundation)

            self._loaded = True
            _logger.info("Surya OCR ready (CPU, %d threads).", _PHYSICAL_CORES)

    def unload(self) -> None:
        """Release Surya models from memory."""
        with self._lock:
            self._foundation = None
            self._detection = None
            self._recognition = None
            self._loaded = False
        _logger.info("Surya OCR models unloaded.")

    def recognize(self, image: Image.Image) -> str:
        """Run Surya OCR on a PIL Image. Returns reconstructed text."""
        if not self._loaded:
            raise RuntimeError("Surya models not loaded. Call load() first.")
        assert self._recognition is not None and self._detection is not None

        total_t0 = time.time()
        import torch

        # ── Pre-processing ─────────────────────────────────────────────
        t0 = time.time()
        img_np = np.array(image.convert("RGB"))
        proc_np = _preprocess_for_ocr(img_np)
        proc_image = Image.fromarray(proc_np)
        prep_t = time.time() - t0
        _logger.info("  Preprocess: %.1fs (input %dx%d -> output %dx%d)",
                     prep_t, image.width, image.height,
                     proc_image.width, proc_image.height)

        # ── 1) Detection ───────────────────────────────────────────────
        det_t = 0.0
        rec_t = 0.0
        old_priority = _boost_process_priority()
        try:
            t0 = time.time()
            with torch.inference_mode():
                det_results = self._detection([proc_image])
            det_t = time.time() - t0
            _logger.info("  Detection: %.1fs", det_t)

            # ── 2) Recognition ─────────────────────────────────────────
            t0 = time.time()
            det_bboxes = [[b.bbox for b in det.bboxes] for det in det_results]
            total_lines = sum(len(b) for b in det_bboxes)
            _logger.info("  Recognition: %d lines to process...", total_lines)

            # Enable Surya DEBUG logging with explicit handler
            surya_handler = logging.StreamHandler()
            surya_handler.setLevel(logging.DEBUG)
            surya_handler.setFormatter(logging.Formatter(
                "  [SURYA] %(name)s | %(message)s"
            ))
            surya_loggers: list[logging.Logger] = []
            for name in ("surya", "surya.recognition", "surya.foundation",
                         "surya.common", "surya.detection", "surya.input",
                         "surya.model", "surya.postprocessing"):
                lg = logging.getLogger(name)
                lg.setLevel(logging.DEBUG)
                lg.addHandler(surya_handler)
                lg.propagate = False  # Don't double-log via root
                surya_loggers.append(lg)

            with torch.inference_mode():
                predictions = self._recognition(
                    [proc_image],
                    bboxes=det_bboxes,
                    sort_lines=True,
                )

            # Clean up: remove handler, restore levels
            for lg in surya_loggers:
                lg.removeHandler(surya_handler)
                lg.setLevel(logging.WARNING)
                lg.propagate = True

            rec_t = time.time() - t0
            _logger.info("  Recognition done: %.1fs (%.1fs/line)",
                         rec_t, rec_t / max(total_lines, 1))
        finally:
            _restore_process_priority(old_priority)

        if not predictions:
            _logger.info("  No text detected.")
            return ""

        result = predictions[0]
        text_lines = result.text_lines
        if not text_lines:
            _logger.info("  No text lines found.")
            return ""

        # Per-line confidence stats
        confs = [line.confidence for line in text_lines if hasattr(line, 'confidence')]
        if confs:
            avg_conf = sum(confs) / len(confs)
            _logger.info("  Lines: %d (avg confidence: %.2f)", len(text_lines), avg_conf)
        else:
            _logger.info("  Lines: %d", len(text_lines))

        # ── 3) Indentation reconstruction ──────────────────────────────
        t0 = time.time()
        text = _reconstruct_indentation(text_lines)
        indent_t = time.time() - t0

        total_t = time.time() - total_t0
        _logger.info("  Indent: %.1fs | TOTAL: %.1fs (det=%.1f rec=%.1f) | %d chars",
                     indent_t, total_t, det_t, rec_t, len(text))
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

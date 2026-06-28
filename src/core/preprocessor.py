"""
Universal image preprocessing pipeline for OCR quality improvement.
Applies contrast enhancement, denoising, sharpening, and more.
Only processes when features are enabled — otherwise returns image unchanged.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np
from PIL import Image

from src.config import (
    PREPROCESS_ENABLE,
    PREPROCESS_CLAHE_CLIP_LIMIT,
    PREPROCESS_CLAHE_GRID_SIZE,
    PREPROCESS_BILATERAL_D,
    PREPROCESS_BILATERAL_SIGMA_COLOR,
    PREPROCESS_BILATERAL_SIGMA_SPACE,
    PREPROCESS_SHARPEN_STRENGTH,
    PREPROCESS_DESKEW_ENABLE,
    PREPROCESS_BINARIZE_ENABLE,
    PREPROCESS_BINARIZE_METHOD,
    PREPROCESS_UPSCALE_FACTOR,
    PREPROCESS_UPSCALE_MIN_DIM,
)

_logger = logging.getLogger(__name__)


def preprocess(image: Image.Image) -> Image.Image:
    """
    Full preprocessing pipeline. Returns the enhanced PIL image.
    Returns the ORIGINAL image unchanged if PREPROCESS_ENABLE is False
    OR if all individual features are disabled.
    """
    if not PREPROCESS_ENABLE:
        return image

    img_np = np.array(image.convert("RGB"))

    # Check if any feature is actually active
    has_work = (
        PREPROCESS_CLAHE_CLIP_LIMIT > 0
        or PREPROCESS_BILATERAL_D > 0
        or PREPROCESS_SHARPEN_STRENGTH > 0
        or PREPROCESS_DESKEW_ENABLE
        or PREPROCESS_BINARIZE_ENABLE
        or _needs_upscale(img_np)
    )

    if not has_work:
        return image

    # 1. Upscale small images (RGB)
    img_np = _upscale_if_small(img_np)

    # 2. Convert to grayscale for most operations
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

    # 3. Denoise
    gray = _denoise(gray)

    # 4. Contrast enhancement (CLAHE)
    gray = _clahe(gray)

    # 5. Sharpen
    gray = _sharpen(gray)

    # 6. Deskew
    gray = _deskew(gray)

    # 7. Binarization — skip on dark-background images to avoid destroying text
    mean_val = float(np.mean(gray))
    if mean_val >= 100:  # light/white background — binarize safely
        gray = _binarize(gray)

    # Convert back to 3-channel RGB (most OCR engines expect RGB)
    result = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

    _logger.debug(
        f"Preprocess: {image.size} -> {result.shape[1]}x{result.shape[0]} "
        f"(bg_mean={mean_val:.0f}, binarized={mean_val >= 100})"
    )
    return Image.fromarray(result)


def _needs_upscale(img: np.ndarray) -> bool:
    """Check if image dimensions are below the upscale threshold."""
    h, w = img.shape[:2]
    min_dim = PREPROCESS_UPSCALE_MIN_DIM
    return w < min_dim[0] or h < min_dim[1]


def _upscale_if_small(img: np.ndarray) -> np.ndarray:
    """Upscale very small images for better OCR accuracy."""
    h, w = img.shape[:2]
    min_dim = PREPROCESS_UPSCALE_MIN_DIM
    if w < min_dim[0] or h < min_dim[1]:
        scale = PREPROCESS_UPSCALE_FACTOR
        new_w = max(int(w * scale), min_dim[0])
        new_h = max(int(h * scale), min_dim[1])
        _logger.debug(f"Upscaling image: {w}x{h} -> {new_w}x{new_h}")
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    return img


def _denoise(gray: np.ndarray) -> np.ndarray:
    """Apply bilateral filter to reduce noise while preserving edges."""
    d = PREPROCESS_BILATERAL_D
    if d < 1:
        return gray
    return cv2.bilateralFilter(
        gray,
        d=d,
        sigmaColor=PREPROCESS_BILATERAL_SIGMA_COLOR,
        sigmaSpace=PREPROCESS_BILATERAL_SIGMA_SPACE,
    )


def _clahe(gray: np.ndarray) -> np.ndarray:
    """Contrast Limited Adaptive Histogram Equalization."""
    clip = PREPROCESS_CLAHE_CLIP_LIMIT
    grid = PREPROCESS_CLAHE_GRID_SIZE
    if clip <= 0:
        return gray
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
    return clahe.apply(gray)


def _sharpen(gray: np.ndarray) -> np.ndarray:
    """Unsharp masking for sharper text edges."""
    strength = PREPROCESS_SHARPEN_STRENGTH
    if strength <= 0:
        return gray
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.0)
    sharpened = cv2.addWeighted(gray, 1.0 + strength, blurred, -strength, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Correct skew angle of text in the image."""
    if not PREPROCESS_DESKEW_ENABLE:
        return gray

    binary = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(binary, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    coords = np.column_stack(np.where(binary > 0))
    if len(coords) < 10:
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return gray

    _logger.debug(f"Deskew: correcting {angle:.2f} degrees")
    h, w = gray.shape
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        gray, matrix, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Adaptive or Otsu thresholding for clean black-on-white text."""
    if not PREPROCESS_BINARIZE_ENABLE:
        return gray

    method = PREPROCESS_BINARIZE_METHOD

    if method == "otsu":
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary

    if method == "adaptive_mean":
        return cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY, 21, 4,
        )

    if method == "adaptive_gaussian":
        return cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 21, 4,
        )

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary
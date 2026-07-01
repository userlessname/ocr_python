"""
RemoteOCREngine - thin HTTP client for the PaddleOCR FastAPI server.

The actual PaddleOCR model lives in `server.py` (run either standalone or
embedded as a background thread by main.py). This class is responsible for:
    * Blocking on the server's `/health` endpoint during load() so that
      the first capture doesn't race the model download.
    * Uploading the snipped PIL.Image to `/ocr/process` as multipart
      form data and returning the `structured_text` payload.
    * Exposing the same `BaseOCREngine` interface used by the rest of
      the app (recognize / load / unload).
"""
from __future__ import annotations

import io
import logging
import threading
import time
from typing import Optional

import requests
from PIL import Image

from src.config import (
    OCR_SERVER_HEALTH_PATH,
    OCR_SERVER_HOST,
    OCR_SERVER_PORT,
    OCR_SERVER_REQUEST_TIMEOUT,
    OCR_SERVER_STARTUP_TIMEOUT,
)
from src.engine.base import BaseOCREngine

_logger = logging.getLogger(__name__)


class RemoteOCREngine(BaseOCREngine):
    """HTTP client wrapper around the PaddleOCR FastAPI server."""

    def __init__(self) -> None:
        self._base_url = f"http://{OCR_SERVER_HOST}:{OCR_SERVER_PORT}"
        self._process_url = f"{self._base_url}/ocr/process"
        self._health_url = f"{self._base_url}{OCR_SERVER_HEALTH_PATH}"
        self._session = requests.Session()
        self._ready = False
        self._lock = threading.Lock()

    # ── BaseOCREngine interface ─────────────────────────────────────────────

    def load(self) -> None:
        """Block until the OCR server is reachable and healthy."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            _logger.info("Waiting for OCR server at %s ...", self._base_url)
            deadline = time.time() + OCR_SERVER_STARTUP_TIMEOUT
            attempt = 0
            while time.time() < deadline:
                attempt += 1
                try:
                    resp = self._session.get(
                        self._health_url,
                        timeout=min(2.0, OCR_SERVER_REQUEST_TIMEOUT),
                    )
                    if resp.status_code == 200 and resp.json().get("status") == "healthy":
                        _logger.info(
                            "OCR server ready (after %d attempt(s)).", attempt,
                        )
                        self._ready = True
                        return
                except requests.RequestException:
                    pass
                time.sleep(0.5)
            raise RuntimeError(
                f"OCR server at {self._base_url} did not become healthy "
                f"within {OCR_SERVER_STARTUP_TIMEOUT} seconds.",
            )

    def unload(self) -> None:
        """No-op: the server manages its own lifecycle."""
        with self._lock:
            self._ready = False
            try:
                self._session.close()
            except Exception:
                pass
        _logger.info("RemoteOCREngine unloaded.")

    def recognize(self, image: Image.Image) -> str:
        """POST the image to the OCR server and return the structured text."""
        self.load()

        # Re-encode the PIL image as PNG in-memory; PNG is lossless and
        # is accepted by the server's MIME validator.
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        buf.seek(0)

        files = {"file": ("snip.png", buf, "image/png")}

        try:
            resp = self._session.post(
                self._process_url,
                files=files,
                timeout=OCR_SERVER_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"OCR server request failed: {exc}",
            ) from exc

        if resp.status_code != 200:
            # Try to surface the server's error detail if any.
            try:
                detail = resp.json().get("detail") or resp.json().get("detail")
            except ValueError:
                detail = resp.text
            raise RuntimeError(
                f"OCR server returned {resp.status_code}: {detail}",
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"OCR server returned non-JSON payload: {resp.text[:200]}",
            ) from exc

        if payload.get("status") != "success":
            raise RuntimeError(
                f"OCR server error: {payload.get('detail', 'unknown')}",
            )

        return payload.get("structured_text", "") or ""

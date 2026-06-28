"""
Abstract base class for OCR engines.
"""
from __future__ import annotations

import abc
from PIL import Image


class BaseOCREngine(abc.ABC):
    """
    Every OCR engine must implement `recognize(self, image: Image.Image) -> str`.
    """

    @abc.abstractmethod
    def recognize(self, image: Image.Image) -> str:
        """
        Run OCR on the given PIL image and return extracted text.
        """
        ...

    @abc.abstractmethod
    def load(self) -> None:
        """
        Lazy-load any heavyweight resources (model weights, etc.).
        Safe to call multiple times.
        """
        ...

    @abc.abstractmethod
    def unload(self) -> None:
        """
        Release loaded resources.
        """
        ...

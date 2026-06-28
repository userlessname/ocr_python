"""
Lightweight internal event bus (pub/sub).
Enables loose coupling between components.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Any

_logger = logging.getLogger(__name__)


class EventBus:
    """
    Simple topic-based event bus.

    Usage:
        bus = EventBus()
        bus.subscribe("hotkey.pressed", handler)
        bus.emit("hotkey.pressed")
        bus.emit("capture.complete", image=my_image)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, topic: str, callback: Callable) -> None:
        """Register a callback for the given topic."""
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable) -> None:
        """Unregister a callback from the given topic."""
        with self._lock:
            if topic in self._subscribers:
                try:
                    self._subscribers[topic].remove(callback)
                except ValueError:
                    pass

    def emit(self, topic: str, **kwargs: Any) -> None:
        """
        Emit an event on the given topic.
        All registered callbacks are called synchronously.
        """
        with self._lock:
            callbacks = list(self._subscribers.get(topic, []))
        for cb in callbacks:
            try:
                cb(**kwargs)
            except Exception:
                _logger.exception(f"EventBus handler error on topic '{topic}'")

    def clear(self) -> None:
        """Remove all subscribers."""
        with self._lock:
            self._subscribers.clear()

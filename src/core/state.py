"""
Thread-safe finite state machine for the snipping workflow.
"""
from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Callable, Optional


class AppState(Enum):
    IDLE = auto()
    SNIPPING = auto()
    PROCESSING = auto()


class StateMachine:
    """
    Manages application state transitions.

    Valid transitions:
        IDLE → SNIPPING
        SNIPPING → PROCESSING | IDLE
        PROCESSING → IDLE
    """

    def __init__(self, on_transition: Optional[Callable] = None):
        self._state = AppState.IDLE
        self._lock = threading.Lock()
        self._on_transition = on_transition

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    def transition_to(self, new_state: AppState) -> bool:
        """
        Attempt a transition. Returns True if successful, False if invalid.
        """
        with self._lock:
            old = self._state
            if not self._is_valid(old, new_state):
                return False
            self._state = new_state
        if self._on_transition:
            try:
                self._on_transition(old, new_state)
            except Exception:
                pass
        return True

    @staticmethod
    def _is_valid(old: AppState, new: AppState) -> bool:
        transitions = {
            AppState.IDLE: {AppState.SNIPPING},
            AppState.SNIPPING: {AppState.PROCESSING, AppState.IDLE},
            AppState.PROCESSING: {AppState.IDLE},
        }
        return new in transitions.get(old, set())

    def is_idle(self) -> bool:
        return self.state == AppState.IDLE

    def reset(self) -> None:
        """Force back to IDLE (use in error recovery)."""
        with self._lock:
            self._state = AppState.IDLE

"""
SnipOCR Stress / Stability Harness.

Launches main.py as a subprocess, simulates hotkey presses and Escape keys,
and forces termination via CTRL_C_EVENT. Asserts clean exit + no crash log entries.

Usage:
    python scripts/stress_snipocr.py
"""
from __future__ import annotations

import logging
import os
import random
import signal
import subprocess
import sys
import time

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger("stress")

# ── Constants ────────────────────────────────────────────────────────────────
ITERATIONS = 50
CANCEL_EVERY = 10  # send Escape every N presses
KILL_AT = 35       # send CTRL_C_EVENT at this iteration
MIN_INTERVAL = 0.1
MAX_INTERVAL = 3.0
CRASH_LOG = "crash.log"
SLEEP_AFTER_KILL = 15  # seconds to wait for clean exit after CTRL_C_EVENT
HANG_TIMEOUT = 10       # seconds of no progress before considering hang

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAIN_PY = os.path.join(PROJECT_DIR, "main.py")


def _get_crash_log_size() -> int:
    """Return the current size of crash.log (0 if missing/empty)."""
    try:
        return os.path.getsize(CRASH_LOG)
    except OSError:
        return 0


def _assert_no_new_crash_content(before_size: int) -> None:
    """Assert that crash.log has not grown since before_size."""
    after_size = _get_crash_log_size()
    if after_size > before_size:
        # Read the new content for diagnostic output
        try:
            with open(CRASH_LOG, "r", encoding="utf-8") as f:
                new_content = f.read()
        except Exception:
            new_content = "<unreadable>"
        _logger.error("crash.log grew from %d to %d bytes. Content:\n%s",
                      before_size, after_size, new_content)
        sys.exit(1)
    _logger.info("crash.log is clean (%d bytes)", after_size)


def _send_ctrl_c(proc: subprocess.Popen) -> None:
    """Send CTRL_C_EVENT to the subprocess on Windows."""
    if sys.platform == "win32":
        try:
            proc.send_signal(signal.CTRL_C_EVENT)
            _logger.info("Sent CTRL_C_EVENT to PID %d", proc.pid)
        except Exception as exc:
            _logger.warning("Failed to send CTRL_C_EVENT: %s", exc)
            proc.terminate()
    else:
        proc.terminate()
        _logger.info("Sent SIGTERM to PID %d", proc.pid)


def main() -> None:
    crash_before = _get_crash_log_size()
    _logger.info("Initial crash.log size: %d bytes", crash_before)

    # ── Launch SnipOCR ───────────────────────────────────────────────────────
    _logger.info("Launching: %s", MAIN_PY)
    proc = subprocess.Popen(
        [sys.executable, MAIN_PY],
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # On Windows, CREATE_NEW_PROCESS_GROUP is needed so CTRL_C_EVENT
        # reaches the process group.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    _logger.info("SnipOCR started (PID %d)", proc.pid)

    # Give the app time to initialise its hotkey listener and tray
    time.sleep(3.0)

    try:
        from pynput.keyboard import Controller as KeyboardController, Key
        kb = KeyboardController()
    except ImportError:
        _logger.error("pynput is required. Install it with: pip install pynput")
        sys.exit(1)

    # ── Main stress loop ─────────────────────────────────────────────────────
    last_activity = time.time()

    for i in range(1, ITERATIONS + 1):
        interval = random.uniform(MIN_INTERVAL, MAX_INTERVAL)
        time.sleep(interval)

        # Detect hang: if no activity push happened for HANG_TIMEOUT
        if time.time() - last_activity > HANG_TIMEOUT:
            _logger.error("Process appears hung (no activity for %.0fs).", HANG_TIMEOUT)
            sys.exit(1)

        # Every CANCEL_EVERY iterations, send Escape to cancel any open overlay
        if i % CANCEL_EVERY == 0:
            _logger.info("[%02d/%d] Sending Escape...", i, ITERATIONS)
            try:
                kb.tap(Key.esc)
            except Exception as exc:
                _logger.warning("Failed to send Escape: %s", exc)
            time.sleep(0.3)  # short wait for overlay to process Escape

        # Send Pause key to trigger a snip
        _logger.info("[%02d/%d] Sending Pause...", i, ITERATIONS)
        try:
            kb.tap(Key.pause)
        except Exception as exc:
            _logger.warning("Failed to send Pause: %s", exc)

        last_activity = time.time()

        # At iteration KILL_AT, force termination
        if i == KILL_AT:
            _logger.info("=== Forced shutdown at iteration %d ===", KILL_AT)
            _send_ctrl_c(proc)
            break

    # ── Wait for clean exit ──────────────────────────────────────────────────
    _logger.info("Waiting up to %ds for process to exit...", SLEEP_AFTER_KILL)
    try:
        stdout_data, stderr_data = proc.communicate(timeout=SLEEP_AFTER_KILL)
        exit_code = proc.returncode
        _logger.info("Process exited with code %d", exit_code)
    except subprocess.TimeoutExpired:
        _logger.error("Process did not exit within %ds — killing.", SLEEP_AFTER_KILL)
        proc.kill()
        stdout_data, stderr_data = proc.communicate(timeout=5)
        exit_code = proc.returncode
        _logger.warning("Killed process (exit code %d)", exit_code)
    except Exception as exc:
        _logger.error("Unexpected error during communicate: %s", exc)
        proc.kill()
        exit_code = -1

    # ── Log stdout/stderr tails for diagnostics ──────────────────────────────
    if stdout_data:
        _logger.info("-- stdout tail (last 500 chars) --\n%s", stdout_data[-500:].decode("utf-8", errors="replace"))
    if stderr_data:
        _logger.info("-- stderr tail (last 500 chars) --\n%s", stderr_data[-500:].decode("utf-8", errors="replace"))

    # ── Assertions ───────────────────────────────────────────────────────────
    if exit_code != 0:
        _logger.error("FAIL: exit code is %d, expected 0.", exit_code)
        sys.exit(1)

    _assert_no_new_crash_content(crash_before)

    _logger.info("PASS: Stress driver completed successfully (exit code 0, crash.log clean).")


if __name__ == "__main__":
    main()

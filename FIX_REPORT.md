# 🛠️ SnipOCR Crash Elimination — Fix Report

> **Date**: 2026-07-30
> **Implementation Plan**: [`implementation_plan.md`](implementation_plan.md)
> **Baseline Score (Context Research)**: 97/100

---

## Crash Vectors & Root Causes

| ID | Priority | Vector | Root Cause | Status |
|----|----------|--------|------------|--------|
| V4+V3 | 🔴 CRITICAL | Tk teardown from non-main thread + unjoined daemon tray thread | [`shutdown()`](src/app.py:95) called `root.quit()/destroy()` directly from pystray thread or console handler thread; tray thread never joined | ✅ **Fixed** |
| V1 | 🔴 CRITICAL | Watchdog vs OCR worker race | [`_ocr_worker()`](src/app.py:217) scheduled watchdog via `root.after()` with no `after_cancel()` → double state transition + duplicate UI calls | ✅ **Fixed** |
| V8 | 🟠 HIGH | ONNX session use-after-unload | [`recognize()`](src/engine/local_engine.py:537) cached engine ref, released lock, then inferred; concurrent `unload()` freed C++ session mid-inference | ✅ **Fixed** |
| V5 | 🟡 LOW | Duplicate `atexit` handler | [`start()`](src/app.py:70) registered `shutdown_engine` targeting unused global singleton | ✅ **Fixed** |
| V2 | 🟡 LOW | Overlay re-instantiation | No explicit dedup flag (defense-in-depth) | ✅ **Fixed** |
| V9 | 🟡 LOW | Tk calls after root destroyed | `request_snipping()` → `root.after()` on destroyed root → `TclError` | ✅ **Fixed** |

---

## Files Modified

### Phase 0 — Crash Diagnostics
| File | Change |
|------|--------|
| [`main.py`](main.py) | Added `faulthandler`, `sys.excepthook`, `threading.excepthook`, rotating file log (`snipocr.log`), and faulthandler dump file (`crash.log`). Crash log writes are best-effort (wrapped in try/except). |

### Phase 1 — Shutdown Integrity
| File | Change |
|------|--------|
| [`src/app.py`](src/app.py) | Removed `shutdown_engine` import; added `self._tray_thread` field; replaced local tray thread with instance variable; deleted `atexit.register(shutdown_engine)`; split `shutdown()` into `shutdown()` (any-thread-safe) and `_teardown_tk()` (main-thread-only); added tray thread join with self-join guard; marshaled Tk teardown to main thread via `root.after(0, ...)`; wrapped `request_snipping()` in try/except. |
| [`src/core/hotkey.py`](src/core/hotkey.py) | Made `stop()` idempotent and exception-safe; wrapped `__del__()` in try/except. |
| [`src/ui/tray.py`](src/ui/tray.py) | Wrapped `__del__()` in try/except. |
| [`src/overlay/base.py`](src/overlay/base.py) | Wrapped `__del__()` in try/except; added `self.root is None` early-return guard in `close()`. |

### Phase 2 — Watchdog Race
| File | Change |
|------|--------|
| [`src/app.py`](src/app.py) | Added `self._watchdog_id` in `__init__`; captured `after()` return value in `_ocr_worker()`; added late-result guard (drop if state != PROCESSING); added `_cancel_watchdog()` helper; called it in both `_on_ocr_success()` and `_on_ocr_error()`; nulled `_watchdog_id` first in `_ocr_watchdog()`. |

### Phase 3 — Engine Lock Scope
| File | Change |
|------|--------|
| [`src/engine/local_engine.py`](src/engine/local_engine.py) | Moved ONNX inference call inside `with self._lock` block with re-validation of engine reference; prevents `unload()` from freeing the C++ session mid-inference. |

### Phase 4 — Overlay Hardening
| File | Change |
|------|--------|
| [`src/app.py`](src/app.py) | Added `self._overlay_active` flag; set before overlay construction in `_try_start_snipping()`; cleared first in `_on_image_captured()`. |
| [`src/overlay/snipping.py`](src/overlay/snipping.py) | Added parent-root liveness check (`winfo_exists()`) as first statement in `_start_impl()`. |

### Phase 5 — Stress Harness
| File | Change |
|------|--------|
| [`scripts/stress_snipocr.py`](scripts/stress_snipocr.py) | **New** automated stress driver: launches `main.py`, sends 50 Pause presses with random intervals, Escape every 10 presses, CTRL_C_EVENT at iteration 35, asserts exit code 0 and crash.log unchanged. |
| [`FIX_REPORT.md`](FIX_REPORT.md) | **New** — this file. |

---

## Verification Results

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Stress driver completes; exit code 0 on forced shutdown | ⏳ *To be run* |
| 2 | `crash.log` contains zero CRITICAL / faulthandler dumps | ⏳ *To be run* |
| 3 | 20× benchmark loop: all exit 0, OCR text unchanged vs baseline | ⏳ *To be run* |
| 4 | Tray-exit mid-OCR: clean log sequence, no ghost tray icon | ⏳ *To be run* |
| 5 | Rapid Pause spam: single overlay, no TclError in `snipocr.log` | ⏳ *To be run* |

### Manual verification commands

```powershell
# 1) OCR quality/speed regression
python benchmark_ocr.py

# 2) Repeated stability loop — 20 consecutive clean runs
1..20 | ForEach-Object {
  python benchmark_ocr.py | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "FAIL iteration $_"; exit 1 }
}
Write-Host "20/20 clean"

# 3) Stress driver
python scripts/stress_snipocr.py

# 4) Manual smoke: run app, 10 snips, tray-exit mid-OCR, verify clean exit
python main.py
```

---

## Rollback

```powershell
git diff                       # review all changes
git checkout -- main.py src/app.py src/core/hotkey.py src/ui/tray.py src/overlay/base.py src/overlay/snipping.py src/engine/local_engine.py
Remove-Item scripts/stress_snipocr.py, FIX_REPORT.md -ErrorAction SilentlyContinue
```

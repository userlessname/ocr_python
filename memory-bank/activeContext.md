# Active Context

## Current Focus
PaddleOCR migration complete. All other OCR engines removed.

## Key Changes
- Removed: `easyocr_engine.py`, `surya_engine.py`, `composite.py`
- Removed dependencies: `easyocr`, `surya-ocr`, `transformers`, `torch`, `torchvision`, `torchaudio`
- Added: `paddleocr_engine.py` — wraps PaddleOCR with lazy loading, thread safety
- Updated: `src/app.py` — uses PaddleOCREngine directly (no CompositeEngine)
- Updated: `src/config.py` — PaddleOCR-specific settings
- Updated: `requirements.txt` — paddlepaddle==2.6.2, paddleocr==2.9.1

## Next Steps
1. Wait for PaddleOCR model download to complete (~1-2 min)
2. Verify PaddleOCR works with a test image
3. Test full application flow (hotkey → snipping → OCR)

## Known Notes
- PaddleOCR runs on CPU (AMD GPU has no CUDA support; DirectML not available for PaddlePaddle on Windows)
- Ryzen 5 9600X + 16 GB DDR5 is sufficient for CPU-based OCR
- Model files are cached in `~/.paddleocr/whl/` after first download
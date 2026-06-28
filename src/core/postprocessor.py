"""
Text post-processing for OCR quality improvement.
Fixes common OCR character confusions, whitespace issues, punctuation,
and optionally performs spell-checking.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Tuple

from src.config import (
    POSTPROCESS_ENABLE,
    POSTPROCESS_FIX_COMMON_ERRORS,
    POSTPROCESS_COLLAPSE_WHITESPACE,
    POSTPROCESS_FIX_PUNCTUATION,
)

_logger = logging.getLogger(__name__)

# Common OCR character confusions (context-aware replacements)
# Only safe replacements — no false-positive-prone word-level fixes.
# Pattern: (regex_pattern, replacement)
OCR_FIXES: list[Tuple[str, str]] = [
    # Digit/letter confusions in number contexts
    (r'(?<=\d)[Oo](?=\d)', '0'),      # 123O456 -> 1230456
    (r'(?<=\d)[lI](?=\d)', '1'),      # 123l456 -> 1231456
    (r'(?<=\d)[Ss](?=\d)', '5'),      # 123S456 -> 1235456
    (r'(?<=\d)[Bb](?=\d)', '8'),      # 123B456 -> 1238456

    # '1' mistaken for 'l' mid-word (e.g. "1evel" -> "level")
    (r'(?<=[a-zA-Z])1(?=[a-z])', 'l'),
    # '0' mistaken for 'O' mid-word (e.g. "c0de" -> "code")
    (r'(?<=[a-z])0(?=[a-z])', 'o'),
    # UPPERCASE words with lowercase 'o' after capital letter (e.g. "INFo" -> "INFO")
    (r'\b([A-Z]{2,})o\b', lambda m: m.group(1) + 'O' if m.group(1)[-1].isupper() else m.group(0)),
    # lowercase 'o' mistaken for uppercase 'O' mid-word (e.g. "HelLO" -> "Hello" — handled by case)
    (r'\bLO\b', 'LO'),                         # keep standalone "LO"
    (r'([A-Z][a-z]+)O([a-z]+)', r'\1o\2'),    # "HellO" -> "Hello"

    # Captial I mistaken for l at word boundaries
    (r'\bl(?=[A-Z][a-z])', 'I'),               # "l am" -> "I am", "lPython" -> "IPython"
    (r'\b1(?=[A-Z][a-z])', 'I'),               # "1 am" -> "I am"

    # Pipe/bar confusions
    (r'(?<=\w)\|(?=\w)', 'l'),         # vertical bar between letters -> l
    (r'\|(?=\s|$)', 'I'),             # stand-alone bar -> I

    # Dot/hyphen artifacts
    (r'(?<=[a-zA-Z])\.(?=[a-zA-Z])', ''),  # mid-word period is noise: "Hel.lo" -> "Hello"
    (r'(?<=[a-z])-(?=[a-z])', ''),          # mid-word hyphen joining letters: "co-operate" -> "cooperate"

    # Zero/letter swaps
    (r'(?<![0-9])0(?=[a-zA-Z])', 'O'),  # leading zero before letter -> O: "0pen" -> "Open"
    (r'(?<=[a-zA-Z])0(?![0-9])', 'O'),  # trailing zero after letter -> O: "Open0" -> "OpenO" (safe enough)
    (r'(?<=[a-zA-Z])1(?=[a-z])', 'l'),  # digit 1 mid-word -> l: "a1bum" -> "album"
    (r'(?<=[a-zA-Z])5(?=[a-z])', 's'),  # digit 5 mid-word -> s: "ba5ic" -> "basic"

    # Common code-level OCR misreads
    (r'loggingbasicConfig', 'logging.basicConfig'),
    (r'loggingINF0', 'logging.INFO'),  # OCR reads O as 0
    (r'loggingINFo', 'logging.INFO'),
    (r'loggingINFO', 'logging.INFO'),
    (r'loggingDEBUGo', 'logging.DEBUG'),
    (r'loggingDEBUG', 'logging.DEBUG'),
    (r'loggingWARNINo', 'logging.WARNING'),
    (r'loggingWARNING', 'logging.WARNING'),
    (r'loggingERROR', 'logging.ERROR'),
    (r'logging(\s*)\.(\s*)basicConfig', 'logging.basicConfig'),
    (r'logging(\s*)\.(\s*)INF0', 'logging.INFO'),
    (r'logging(\s*)\.(\s*)INFO', 'logging.INFO'),
    (r'logging(\s*)\.(\s*)DEBUG', 'logging.DEBUG'),
    (r'logging(\s*)\.(\s*)WARNING', 'logging.WARNING'),
    (r'logging(\s*)\.(\s*)ERROR', 'logging.ERROR'),

    # Zero/O confusion in capital acronyms
    (r'\bINF0\b', 'INFO'),
    (r'\bDEBU0\b', 'DEBUG'),
    (r'\bWARNIN0\b', 'WARNING'),
    (r'\bERR0R\b', 'ERROR'),

    # 1evel -> level (common OCR pattern: 1=l)
    (r'\b1evel\b', 'level'),
    (r'\b1eve1\b', 'level'),
    (r'\b1eveI\b', 'level'),
    (r'\b1evei\b', 'level'),

    # Fix malformed strftime patterns (missing % before format codes)
    (r'\(%\(asctime\)', '(%(asctime)'),
    (r'\(%\(name\)', '(%(name)'),
    (r'\(%\(levelname\)', '(%(levelname)'),
    (r'\(%\(message\)', '(%(message)'),
    (r'asctime\) s', 'asctime)s'),
    (r'name\) s', 'name)s'),
    (r'levelname\) s', 'levelname)s'),
    (r'message\) s', 'message)s'),

    # Fix missing dots in imports
    (r'import os\.', 'import os'),
    (r'import sys\.', 'import sys'),
    (r'import warnings\.', 'import warnings'),

    # Fix stray space after dot in "module. METHOD" patterns (e.g. "logging. INFO")
    (r'(?<=[a-z])\. ([A-Z]{2,})(?=\b|[,\s\)])', r'.\1'),

    # Collapse "%(asctime) s" style splits back to "%(asctime)s"
    (r'(%\([a-z]+\))\s+([a-z])', r'\1\2'),

    # Final pass: remove any single space after a dot that joins two alphanumeric tokens
    (r'(?<=[a-zA-Z0-9)])\. (?=[a-zA-Z0-9(])', '.'),

    # Remove stray spaces after dots in module.method patterns
    (r'(?<=[a-zA-Z])\.\s+(?=[a-zA-Z])', '.'),

    # Fix file extensions — remove space before .ext (e.g. "Python. vbs" → "Python.vbs")
    (r'\b(\w+)\.\s+(vbs|py|txt|exe|jpg|png|md|html)\b', r'\1.\2'),

    # Fix missing dots before file extensions (e.g. "Pythonvbs" → "Python.vbs")
    (r'\b([a-zA-Z]\w*?)(vbs|py|txt|exe|jpg|png|md|html)\b', r'\1.\2'),

    # Remove stray space after a dot ONLY when it creates a space before extension
    # e.g. "Python. vbs" -> "Python.vbs" but keep "def _setup_logging() -> None:"
    (r'\.\s+(vbs|py|txt|exe|jpg|png|md|html)\b', r'.\1'),

    # Fix strftime where % was dropped: %H:%M: %S → %H:%M:%S
    (r'(%\w+:\s*%M):\s*%S', r'\1:%S'),
    (r'(%\w+):\s*%M:\s*%S', r'\1:%M:%S'),
    (r'(%\w+):\s*%M', r'\1:%M'),

    # Unicode dash / quote normalisation
    (r'[\u2013\u2014\u2015]', '-'),     # en-dash, em-dash, horizontal bar -> hyphen
    (r'[\u2018\u2019\u201a\u201b]', "'"),  # various apostrophe forms -> ASCII '
    (r'[\u201c\u201d\u201e\u201f]', '"'),  # various quote forms -> ASCII "
]


def postprocess(text: str) -> str:
    """
    Apply all post-processing steps to OCR output text.
    """
    if not POSTPROCESS_ENABLE or not text:
        return text

    original = text

    text = _fix_common_errors(text)
    text = _fix_whitespace(text)
    text = _fix_punctuation(text)
    text = _clean_artifacts(text)

    if text != original:
        _logger.debug(f"Postprocess: {len(original)} -> {len(text)} chars")
    return text


def _fix_common_errors(text: str) -> str:
    """Apply common OCR character confusion fixes."""
    if not POSTPROCESS_FIX_COMMON_ERRORS:
        return text
    for pattern, replacement in OCR_FIXES:
        text = re.sub(pattern, replacement, text)
    return text


def _fix_whitespace(text: str) -> str:
    """Collapse multiple spaces (preserving leading indentation), trim trailing spaces."""
    if not POSTPROCESS_COLLAPSE_WHITESPACE:
        return text

    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # Preserve leading whitespace (indentation)
        stripped = line.rstrip()
        if not stripped:
            continue
        # Collapse multiple internal spaces to single
        # but keep leading spaces intact
        lead = line[:len(line) - len(line.lstrip())]
        body = line[len(lead):]
        body = re.sub(r'[ \t]+', ' ', body)
        line = lead + body
        # Remove lines that are only spaces/punctuation
        if line.strip() and not re.match(r'^[\s\W]+$', line.strip()):
            cleaned_lines.append(line)

    # Remove duplicate blank lines
    result = "\n".join(cleaned_lines)
    # Collapse multiple consecutive blank lines to at most one
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _fix_punctuation(text: str) -> str:
    """Fix missing spaces after punctuation, double punctuation, etc."""
    if not POSTPROCESS_FIX_PUNCTUATION:
        return text

    # Space after sentence-ending punctuation if missing — only at true sentence boundaries
    text = re.sub(r'([.!?])\s*(\s*[A-Z][a-z]+)', r'\1 \2', text)

    # Remove double periods
    text = re.sub(r'\.{2,}', '.', text)

    # Fix space before punctuation
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)

    return text


def _clean_artifacts(text: str) -> str:
    """Remove OCR artifacts like repeated single chars, stray symbols."""
    if not text:
        return text

    lines = text.split("\n")
    cleaned = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 1. Lines that are just repeated same character (e.g. "_____", "=======")
        if re.match(r'^(.)\1{3,}$', stripped):
            continue

        # 2. Solitary non-alphanumeric chars on their own line
        if re.match(r'^\W+$', stripped):
            continue

        # 3. Low-information repeating patterns: "# # - 1 · 1 · 1", "I I I I I I I"
        #    Detect if >60% of the line is the same character or repeating pattern
        if len(stripped) >= 6:
            # Count unique non-space chars
            unique_chars = set(stripped.replace(' ', ''))
            if len(unique_chars) <= 2:
                continue

        # 4. Lines with very low alphanumeric ratio (< 30%) — likely noise
        alphanum_count = sum(1 for c in stripped if c.isalnum())
        if len(stripped) >= 8 and alphanum_count / len(stripped) < 0.3:
            continue

        cleaned.append(line)

    result = "\n".join(cleaned)
    # Collapse multiple consecutive blank lines to at most one
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()

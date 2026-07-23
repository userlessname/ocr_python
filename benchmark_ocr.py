"""
Verification & benchmark for the OCR quality/speed improvements.

1. Unit-tests the new bounded (<=1 edit) Turkish spell corrector against the
   old full-Levenshtein implementation for correctness and speed.
2. Runs the real LocalOCREngine end-to-end on synthetic images (Turkish
   paragraph, command line, code block) and reports text + timings.
"""
from __future__ import annotations

import logging
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
_logger = logging.getLogger("benchmark")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.engine import local_engine as le


# ── Old implementation (kept inline for A/B comparison) ─────────────────────

def _old_levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _old_levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _old_correct_word(word: str) -> str:
    clean_word = "".join(c for c in word if c.isalnum()).lower()
    if not clean_word or clean_word.isdigit():
        return word
    if clean_word in le._TURKISH_DICT:
        return word
    len_w = len(clean_word)
    first_char = clean_word[0]
    similar_starts = {first_char}
    if first_char in ("s", "ş"): similar_starts.update(("s", "ş"))
    if first_char in ("c", "ç"): similar_starts.update(("c", "ç"))
    if first_char in ("g", "ğ"): similar_starts.update(("g", "ğ"))
    if first_char in ("i", "ı"): similar_starts.update(("i", "ı"))
    if first_char in ("o", "ö"): similar_starts.update(("o", "ö"))
    if first_char in ("u", "ü"): similar_starts.update(("u", "ü"))
    candidates = [
        dw for dw in le._TURKISH_DICT
        if abs(len(dw) - len_w) <= 2 and dw[0] in similar_starts
    ]
    if not candidates:
        return word
    best_word, min_dist = None, 999
    for dw in candidates:
        dist = _old_levenshtein(clean_word, dw)
        if dist < min_dist:
            min_dist, best_word = dist, dw
    if min_dist <= 1 and best_word:
        if word[0].isupper():
            best_word = best_word.capitalize()
        prefix = ""
        for c in word:
            if not c.isalnum():
                prefix += c
            else:
                break
        suffix = ""
        for c in reversed(word):
            if not c.isalnum():
                suffix = c + suffix
            else:
                break
        return prefix + best_word + suffix
    return word


def _load_full_dict() -> None:
    """Mirror the runtime dictionary expansion done in LocalOCREngine.load()."""
    dict_path = os.path.join("models", "turkish_words.txt")
    with open(dict_path, "r", encoding="utf-8") as f:
        for line in f:
            w = line.strip().lower()
            if w:
                le._TURKISH_DICT.add(w)
    suffixes = ["mış", "miş", "muş", "müş", "dı", "di", "du", "dü",
                "tı", "ti", "tu", "tü", "acak", "ecek", "iyor"]
    extra = []
    for w in list(le._TURKISH_DICT):
        if w.endswith("mak") or w.endswith("mek"):
            stem = w[:-3]
            for s in suffixes:
                extra.append(stem + s)
                extra.append(stem + s + "lar")
                extra.append(stem + s + "ler")
    le._TURKISH_DICT.update(extra)
    le._reset_spell_state()


def test_spell_correction() -> None:
    _logger.info("Loading full Turkish dictionary ...")
    t0 = time.time()
    _load_full_dict()
    _logger.info("Dictionary ready: %d words (%.2fs)", len(le._TURKISH_DICT), time.time() - t0)

    # ── Correctness: same correction decisions as old implementation ────────
    cases = [
        ("yavaş", "yavaş"),            # already correct -> unchanged
        ("yavş", "yavaş"),             # 1 deletion -> fixed (beats "yave" tie)
        ("saba", "saba"),              # valid dictionary word -> unchanged
        ("sabaah", "sabah"),           # 1 insertion -> fixed
        ("günes", "güneş"),            # s/ş confusion -> fixed (beats "güney" tie)
        ("rüzger", "rüzgar"),          # e/a substitution -> fixed
        ("Sabah", "Sabah"),            # capitalized known word -> unchanged
        ("installDebug", "installDebug"),  # camelCase code token -> untouched
        ("snake_case", "snake_case"),  # snake_case -> untouched
        ("utf8", "utf8"),              # alnum mix -> untouched
        ("USERPROFILE", "USERPROFILE"),  # constant case -> untouched
        ("xyzqw", "xyzqw"),            # no near candidate -> unchanged
        ("(heyecan", "(heyecan"),      # punctuation prefix preserved
        ("korkunun.", "korkunun."),    # punctuation suffix preserved
    ]
    failures = 0
    for given, expected in cases:
        got = le._correct_turkish_word(given)
        status = "OK " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"  [{status}] {given!r:20} -> {got!r:20} (expected {expected!r})")

    # Agreement with old implementation on a sample of near-miss words
    sample = ["yavş", "saba", "günes", "rüzger", "dalgalar", "heyacan",
              "kasab", "umud", "dümeni", "balıkçılık", "teknesine", "mert"]
    agree = 0
    for w in sample:
        old = _old_correct_word(w)
        new = le._correct_turkish_word(w)
        old_fixed = old != w
        new_fixed = new != w
        if old_fixed == new_fixed:
            agree += 1
        else:
            print(f"  [DIFF] {w!r}: old={old!r} new={new!r}")
    print(f"  old/new fix decisions agree on {agree}/{len(sample)} samples")

    # ── Speed: paragraph of unknown-ish words, old vs new ───────────────────
    paragraph = ("yavş saba günes rüzger heyacan kasab umud teknesine "
                 "korkunun yerini büyük heyecan kaplamış dalgalar sağa sola "
                 "installDebug snake_case utf8 gradlew USERPROFILE") * 20
    words = paragraph.split()

    t0 = time.time()
    for w in words:
        _old_correct_word(w)
    old_t = time.time() - t0

    t0 = time.time()
    for w in words:
        le._correct_turkish_word(w)
    new_t = time.time() - t0

    print(f"  speed: {len(words)} words | old={old_t:.3f}s  new={new_t:.3f}s  "
          f"({old_t / max(new_t, 1e-9):.1f}x faster)")
    if failures:
        print(f"  RESULT: {failures} correctness FAILURES")
        sys.exit(1)
    print("  RESULT: spell corrector OK")


# ── End-to-end OCR on synthetic images ──────────────────────────────────────

def _render(text: str, font_size: int, width: int, dark: bool = False,
            font_name: str = "arial.ttf"):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(font_name, font_size)
    lines = text.split("\n")
    line_h = int(font_size * 1.6)
    height = max(line_h * len(lines) + 20, 24)
    bg = (15, 15, 15) if dark else (255, 255, 255)
    fg = (230, 230, 230) if dark else (0, 0, 0)
    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((10, 10 + i * line_h), ln, font=font, fill=fg)
    return img


def test_engine_e2e() -> None:
    from src.engine.local_engine import LocalOCREngine

    engine = LocalOCREngine()
    t0 = time.time()
    engine.load()
    print(f"\n  engine.load() (incl. warmup): {time.time() - t0:.2f}s")

    turkish_paragraph = (
        "Sabah güneş henüz doğarken Mert teknesine biraz yiyecek,\n"
        "su ve pusulasını alarak denize açılmıştı. Kasaba arkasında\n"
        "yavaş yavaş küçülürken içindeki korkunun yerini büyük bir\n"
        "heyecan kaplamıştı. İlk saatleri sakin geçmişti ancak\n"
        "öğleden sonra gökyüzü aniden kararmıştı."
    )
    command_line = r'gradlew installDebug --console=plain -Pandroid.injected.signing.store.file=%USERPROFILE%\debug.keystore'
    code_block = (
        "def calculate_total(items, tax_rate):\n"
        "    subtotal = sum(item.price for item in items)\n"
        "    tax_amount = subtotal * tax_rate\n"
        "    return subtotal + tax_amount"
    )

    cases = [
        ("turkish_paragraph", _render(turkish_paragraph, 22, 900)),
        ("command_line_dark", _render(command_line, 16, 1250, dark=True,
                                      font_name="consola.ttf")),
        ("code_block", _render(code_block, 18, 700, font_name="consola.ttf")),
        ("small_text", _render("Küçük yazı testi: çğıöşü ÇĞİÖŞ 123", 12, 420)),
    ]

    for name, img in cases:
        t0 = time.time()
        text = engine.recognize(img)
        dt = time.time() - t0
        print(f"\n=== {name} ({img.width}x{img.height}) -> {dt:.2f}s ===")
        print(text)

    engine.unload()
    print("\n  engine E2E done")


if __name__ == "__main__":
    test_spell_correction()
    test_engine_e2e()
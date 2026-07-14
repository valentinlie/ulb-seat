"""Captcha solving via OCR."""

import io
import os
from collections import Counter

from PIL import Image
import pytesseract

# No sudo on this host, so the tessdata language files live under the user's
# home dir instead of /usr/share/tessdata. Set this unconditionally (rather
# than relying on a TESSDATA_PREFIX env var being exported by whatever
# process invokes us -- CLI, scheduler daemon, web backend, etc.) so captcha
# solving works no matter how solve_captcha() gets called.
_TESSDATA_DIR = os.path.expanduser("~/tessdata")
os.environ.setdefault("TESSDATA_PREFIX", _TESSDATA_DIR)


CAPTCHA_LENGTH = 6

# (upscale factor, binarization threshold) combinations that are OCRed
# independently; the majority answer wins. Upscaling helps tesseract with the
# small captcha font, but no single threshold cleanly separates the text from
# the colored noise lines on every image, so we vote across several.
_VARIANTS = [(3, 180), (3, 160), (1, 160), (1, 200), (4, 160)]


def _ocr_variant(img: Image.Image, scale: int, threshold: int) -> str:
    gray = img.convert("L")
    if scale != 1:
        gray = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
    # Threshold to isolate white text from dark bg + colored lines
    binary = gray.point(lambda p: 255 if p > threshold else 0)
    return pytesseract.image_to_string(
        binary,
        config=(
            "--psm 7 --tessdata-dir "
            f"{_TESSDATA_DIR} "
            "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        ),
    ).strip()


def solve_captcha(image_bytes: bytes) -> str:
    """Solve a captcha image using OCR. Returns the recognized text.

    Returns "" when no preprocessing variant yields a result of the expected
    length, so callers can re-fetch a fresh captcha instead of submitting
    something that is certainly wrong.
    """
    img = Image.open(io.BytesIO(image_bytes))
    results = [_ocr_variant(img, scale, threshold) for scale, threshold in _VARIANTS]
    valid = [r for r in results if len(r) == CAPTCHA_LENGTH]
    if not valid:
        return ""
    return Counter(valid).most_common(1)[0][0]

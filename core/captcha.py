"""Captcha solving via OCR."""

import io
import os

from PIL import Image
import pytesseract

# No sudo on this host, so the tessdata language files live under the user's
# home dir instead of /usr/share/tessdata. Set this unconditionally (rather
# than relying on a TESSDATA_PREFIX env var being exported by whatever
# process invokes us -- CLI, scheduler daemon, web backend, etc.) so captcha
# solving works no matter how solve_captcha() gets called.
_TESSDATA_DIR = os.path.expanduser("~/tessdata")
os.environ.setdefault("TESSDATA_PREFIX", _TESSDATA_DIR)


def solve_captcha(image_bytes: bytes) -> str:
    """Solve a captcha image using OCR. Returns the recognized text."""
    img = Image.open(io.BytesIO(image_bytes))
    # Convert to grayscale
    gray = img.convert("L")
    # Threshold to isolate white text from dark bg + colored lines
    binary = gray.point(lambda p: 255 if p > 200 else 0)
    text = pytesseract.image_to_string(
        binary,
        config=(
            "--psm 7 --tessdata-dir "
            f"{_TESSDATA_DIR} "
            "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        ),
    ).strip()
    return text

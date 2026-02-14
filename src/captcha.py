"""Captcha solving via OCR."""

import io

from PIL import Image
import pytesseract


def solve_captcha(image_bytes: bytes) -> str:
    """Solve a captcha image using OCR. Returns the recognized text."""
    img = Image.open(io.BytesIO(image_bytes))
    # Convert to grayscale
    gray = img.convert("L")
    # Threshold to isolate white text from dark bg + colored lines
    binary = gray.point(lambda p: 255 if p > 200 else 0)
    text = pytesseract.image_to_string(
        binary,
        config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    ).strip()
    return text

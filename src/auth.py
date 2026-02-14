"""SSO authentication and captcha form handling."""

import base64
import re
import sys
import time
from urllib.parse import urljoin

import requests

from config import BASE_URL, SSO_USERNAME, SSO_PASSWORD, LIBRARY_NUMBER, MAX_CAPTCHA_RETRIES
from captcha import solve_captcha


def login(session: requests.Session) -> str:
    """Log in via SSO. Returns the HTML of the reservation page after login."""
    print("[1/6] Logging in via SSO...")
    # GET login page to establish cookies
    session.get(BASE_URL)
    # POST credentials
    resp = session.post(
        BASE_URL,
        data={
            "httpd_username": SSO_USERNAME,
            "httpd_password": SSO_PASSWORD,
            "httpd_dummy": str(int(time.time() * 1000)),
        },
        allow_redirects=True,
    )
    if "Sie sind angemeldet als" not in resp.text:
        print("ERROR: Login failed. Check credentials.")
        sys.exit(1)
    print("  Logged in successfully.")
    return resp.text


def handle_captcha(session: requests.Session, html: str) -> None:
    """Solve the captcha and submit the confirmation form."""
    print("[2/6] Solving captcha...")

    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        # Extract captcha image (base64 inline JPEG)
        m = re.search(r"data:image/jpeg;base64,([^'\"]+)", html)
        if not m:
            # No captcha on page — might already be registered
            if "Schnellübersicht" in html or "Neue Platzreservierung starten" in html:
                print("  Already registered, no captcha needed.")
                return
            print("ERROR: Could not find captcha image.")
            sys.exit(1)

        image_bytes = base64.b64decode(m.group(1))
        captcha_text = solve_captcha(image_bytes)
        print(f"  Attempt {attempt}: recognized '{captcha_text}'")

        if not captcha_text or len(captcha_text) < 4:
            print("  OCR result too short, retrying...")
            # Re-fetch the page for a new captcha
            html = session.get(BASE_URL).text
            continue

        # Extract form token
        token_match = re.search(
            r'name="sform_token"\s+value="([^"]+)"', html
        )
        if not token_match:
            print("ERROR: Could not find sform_token.")
            sys.exit(1)

        resp = session.post(
            urljoin(BASE_URL, "index.php"),
            data={
                "sform_token": token_match.group(1),
                "sform_step": "3",
                "mod": "000",
                "benutzernummer": LIBRARY_NUMBER,
                "datenschutzerklaerung_akzeptiert": "X",
                "captcha": captcha_text,
            },
            allow_redirects=True,
        )

        if "Erfolg" in resp.text:
            print("  Captcha solved successfully!")
            # Follow the "Weiter" link if present
            weiter = re.search(r'href="([^"]*)"[^>]*class="[^"]*ym-success[^"]*"', resp.text)
            if weiter:
                session.get(urljoin(BASE_URL, weiter.group(1)))
            return

        if "Schnellübersicht" in resp.text or "Neue Platzreservierung starten" in resp.text:
            print("  Captcha accepted (already registered).")
            return

        print("  Captcha rejected, retrying...")
        # Re-fetch for new captcha
        resp = session.get(BASE_URL)
        html = resp.text

    print("ERROR: Failed to solve captcha after max retries.")
    sys.exit(1)

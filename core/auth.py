"""SSO authentication and captcha form handling."""

import base64
import logging
import re
import time
from urllib.parse import urljoin

import requests

from config import BASE_URL, SSO_USERNAME, SSO_PASSWORD, LIBRARY_NUMBER, MAX_CAPTCHA_RETRIES
from core.captcha import solve_captcha
from core.exceptions import BookingError

log = logging.getLogger(__name__)


def login(session: requests.Session) -> str:
    """Log in via SSO. Returns the HTML of the reservation page after login."""
    log.info("[1/6] Logging in via SSO...")
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
        raise BookingError("Login failed. Check credentials.")
    log.info("  Logged in successfully.")
    return resp.text


def handle_captcha(session: requests.Session, html: str) -> None:
    """Solve the captcha and submit the confirmation form."""
    log.info("[2/6] Solving captcha...")

    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        # Extract captcha image (base64 inline JPEG)
        m = re.search(r"data:image/jpeg;base64,([^'\"]+)", html)
        if not m:
            # No captcha on page — already registered, just need to start a new reservation
            if "Neue Platzreservierung starten" in html:
                start_link = re.search(r'href="([^"]*)"[^>]*>Neue Platzreservierung starten', html)
                if start_link:
                    log.info("  Already registered, starting new reservation...")
                    new_resp = session.get(urljoin(BASE_URL, start_link.group(1)))
                    html = new_resp.text
                    continue  # new page likely has a captcha — solve it
                log.info("  Already registered, no start link found.")
                return
            if "Schnellübersicht" in html:
                log.info("  Already registered, no captcha needed.")
                return
            raise BookingError("Could not find captcha image.")

        image_bytes = base64.b64decode(m.group(1))
        captcha_text = solve_captcha(image_bytes)
        log.info("  Attempt %d: recognized '%s'", attempt, captcha_text)

        if not captcha_text or len(captcha_text) < 4:
            log.info("  OCR result too short, retrying...")
            # Re-fetch the page for a new captcha
            html = session.get(BASE_URL).text
            continue

        # Extract form token
        token_match = re.search(
            r'name="sform_token"\s+value="([^"]+)"', html
        )
        if not token_match:
            raise BookingError("Could not find sform_token.")

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
            log.info("  Captcha solved successfully!")
            # Follow the "Weiter" link if present
            weiter = re.search(r'href="([^"]*)"[^>]*class="[^"]*ym-success[^"]*"', resp.text)
            if weiter:
                session.get(urljoin(BASE_URL, weiter.group(1)))
            return

        if "Neue Platzreservierung starten" in resp.text:
            start_link = re.search(r'href="([^"]*)"[^>]*>Neue Platzreservierung starten', resp.text)
            if start_link:
                log.info("  Already registered, starting new reservation...")
                new_resp = session.get(urljoin(BASE_URL, start_link.group(1)))
                html = new_resp.text
                continue  # new page likely has a captcha — solve it
            log.info("  Already registered, no start link found.")
            return
        if "Schnellübersicht" in resp.text:
            log.info("  Already registered, no captcha needed.")
            return

        log.info("  Captcha rejected, retrying...")
        # Re-fetch for new captcha
        resp = session.get(BASE_URL)
        html = resp.text

    raise BookingError("Failed to solve captcha after max retries.")

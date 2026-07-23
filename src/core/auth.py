"""SSO authentication and captcha form handling."""

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from config import BASE_URL, SSO_USERNAME, SSO_PASSWORD, LIBRARY_NUMBER, MAX_CAPTCHA_RETRIES
from core.captcha import solve_captcha
from core.exceptions import BookingError

log = logging.getLogger(__name__)

# Cache the SSO session cookies between runs so back-to-back bookings (each a
# fresh process) reuse one login instead of re-authenticating every time. There
# is no token expiry to check — a stale session just fails the logged-in check
# below and we log in fresh.
SESSION_CACHE = Path(
    os.environ.get("ULB_SESSION_CACHE", str(Path.home() / ".cache" / "ulb-seat" / "cookies.json"))
)
_LOGGED_IN_MARKER = "Ihr Login:"


def _load_cookies(session: requests.Session) -> bool:
    """Restore cached cookies into ``session``. True if any were loaded."""
    try:
        data = json.loads(SESSION_CACHE.read_text())
    except (OSError, ValueError):
        return False
    if data.get("username") != SSO_USERNAME:
        return False
    cookies = data.get("cookies") or []
    for c in cookies:
        session.cookies.set_cookie(requests.cookies.create_cookie(
            name=c["name"], value=c["value"], domain=c.get("domain", ""),
            path=c.get("path", "/"), expires=c.get("expires"), secure=c.get("secure", False),
        ))
    return bool(cookies)


def _save_cookies(session: requests.Session) -> None:
    try:
        SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {"name": c.name, "value": c.value, "domain": c.domain,
             "path": c.path, "expires": c.expires, "secure": c.secure}
            for c in session.cookies
        ]
        tmp = SESSION_CACHE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"username": SSO_USERNAME, "cookies": data}))
        os.chmod(tmp, 0o600)  # session cookies are credentials
        os.replace(tmp, SESSION_CACHE)
    except OSError as exc:
        log.warning("Could not cache session: %s", exc)


def login(session: requests.Session) -> str:
    """Log in via SSO. Returns the HTML of the reservation page after login.

    Reuses cached cookies when the session is still valid, so repeated bookings
    do not re-authenticate; falls back to a fresh credential POST otherwise.
    """
    log.info("[1/6] Logging in via SSO...")

    # Try a cached session first — one GET tells us if it is still logged in.
    if _load_cookies(session):
        resp = session.get(BASE_URL)
        if _LOGGED_IN_MARKER in resp.text:
            log.info("  Reusing cached SSO session.")
            return resp.text
        log.info("  Cached session no longer valid, logging in fresh.")
        session.cookies.clear()
    else:
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
    if _LOGGED_IN_MARKER not in resp.text:
        raise BookingError("Login failed. Check credentials.")
    _save_cookies(session)
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

        if len(captcha_text) != 6:
            log.info("  OCR result has wrong length, retrying...")
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

        # Check for an explicit captcha rejection BEFORE the already-registered
        # checks: the rejection page also contains a "Neue Platzreservierung
        # starten" link, which previously masked wrong captchas as "already
        # registered" and looped without ever reporting the real problem.
        if "Captcha-Code ist falsch" in resp.text:
            log.info("  Captcha rejected, retrying...")
            # The rejection page already contains a fresh captcha + form
            html = resp.text
            continue

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

        log.info("  Unexpected response after captcha submit, retrying...")
        # Re-fetch for new captcha
        resp = session.get(BASE_URL)
        html = resp.text

    raise BookingError("Failed to solve captcha after max retries.")

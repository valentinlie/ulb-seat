"""SSO authentication."""

import json
import logging
import os
import time
from pathlib import Path

import requests

from config import BASE_URL
from core.exceptions import BookingError

log = logging.getLogger(__name__)

# Cache the SSO session cookies between runs so back-to-back bookings (each a
# fresh process) reuse one login instead of re-authenticating every time. There
# is no token expiry to check — a stale session just fails the logged-in check
# below and we log in fresh. One file per account: these are credentials, and
# accounts belong to different people.
SESSION_CACHE_DIR = Path(
    os.environ.get("ULB_SESSION_CACHE_DIR", str(Path.home() / ".cache" / "ulb-seat"))
)
_LOGGED_IN_MARKER = "Ihr Login:"


def _cache_path(account) -> Path:
    return SESSION_CACHE_DIR / f"cookies-{account.id}.json"


def _load_cookies(session: requests.Session, account) -> bool:
    """Restore cached cookies into ``session``. True if any were loaded."""
    try:
        data = json.loads(_cache_path(account).read_text())
    except (OSError, ValueError):
        return False
    if data.get("username") != account.sso_username:
        return False
    cookies = data.get("cookies") or []
    for c in cookies:
        session.cookies.set_cookie(requests.cookies.create_cookie(
            name=c["name"], value=c["value"], domain=c.get("domain", ""),
            path=c.get("path", "/"), expires=c.get("expires"), secure=c.get("secure", False),
        ))
    return bool(cookies)


def _save_cookies(session: requests.Session, account) -> None:
    path = _cache_path(account)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        data = [
            {"name": c.name, "value": c.value, "domain": c.domain,
             "path": c.path, "expires": c.expires, "secure": c.secure}
            for c in session.cookies
        ]
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"username": account.sso_username, "cookies": data}))
        os.chmod(tmp, 0o600)  # session cookies are credentials
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Could not cache session: %s", exc)


def forget_cached_session(account) -> None:
    """Drop an account's cached cookies (used when its credentials change)."""
    _cache_path(account).unlink(missing_ok=True)


def login(session: requests.Session, account) -> str:
    """Log in ``account`` via SSO. Returns the reservation page HTML after login.

    Reuses cached cookies when the session is still valid, so repeated bookings
    do not re-authenticate; falls back to a fresh credential POST otherwise.
    """
    log.info("[1/4] Logging in via SSO as %s...", account.sso_username)

    # Try a cached session first — one GET tells us if it is still logged in.
    if _load_cookies(session, account):
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
            "httpd_username": account.sso_username,
            "httpd_password": account.sso_password,
            "httpd_dummy": str(int(time.time() * 1000)),
        },
        allow_redirects=True,
    )
    if _LOGGED_IN_MARKER not in resp.text:
        raise BookingError(f"Login failed for {account.sso_username}. Check credentials.")
    _save_cookies(session, account)
    log.info("  Logged in successfully.")
    return resp.text

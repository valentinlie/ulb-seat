"""Passkey (WebAuthn) login for the web dashboard.

This is a single-owner dashboard, so there is no user table: every registered
passkey belongs to the one account described by ``USER_HANDLE``/``USER_NAME``.
A successful assertion flips a signed session cookie, and ``require_auth``
guards the routes from then on.

Registering a passkey requires either an already signed-in session or the
enrolment token from config.py (``/passkeys?token=...``). There is no
anonymous enrolment, not even for the first key — see ``registration_allowed``.

Setting ``AUTH_ENABLED = False`` in config.py switches the whole thing off and
leaves the dashboard open — meant for running it locally, never on a public
host.
"""

import hashlib
import json
import logging
import secrets
from typing import Any

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from config import AUTH_ENABLED, ORIGIN, REGISTRATION_TOKEN, RP_ID, RP_NAME
from core import db

log = logging.getLogger(__name__)

# Opaque, stable handle for the single dashboard owner. Never displayed.
USER_HANDLE = b"ulb-seat-owner"
USER_NAME = "vale"

_REG_CHALLENGE = "reg_challenge"
_AUTH_CHALLENGE = "auth_challenge"
_ENROL_OK = "enrol_ok"


class NotAuthenticated(Exception):
    """Raised by :func:`require_auth`; the app turns it into a redirect."""


def require_auth(request: Request) -> str:
    """Route dependency. Returns the user name, or bounces to the login page."""
    if not AUTH_ENABLED:
        return USER_NAME
    if not request.session.get("authenticated"):
        raise NotAuthenticated
    return USER_NAME


def not_authenticated_handler(request: Request, _exc: Exception) -> Response:
    """htmx swaps the response body, so it needs an explicit redirect header."""
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def is_authenticated(request: Request) -> bool:
    """Whether the visitor may see the dashboard — always, once auth is switched off."""
    return not AUTH_ENABLED or bool(request.session.get("authenticated"))


def _token_hash(token: str) -> str:
    """Only the hash is stored, so a spent token is never at rest in the DB."""
    return hashlib.sha256(token.encode()).hexdigest()


def unlock_enrolment(request: Request, token: str | None) -> bool:
    """Check ?token= against the configured one and remember it for this session.

    This is the only way to enrol without already being signed in. An unset
    REGISTRATION_TOKEN disables the route entirely rather than opening it.
    """
    if not AUTH_ENABLED or not REGISTRATION_TOKEN or not token:
        return False
    if not secrets.compare_digest(token, REGISTRATION_TOKEN):
        log.warning("Rejected a passkey enrolment attempt with a bad token")
        return False
    if db.token_consumed(_token_hash(token)):
        log.warning("Rejected a passkey enrolment attempt with an already-spent token")
        return False
    request.session[_ENROL_OK] = True
    return True


def registration_allowed(request: Request) -> bool:
    """Enrolling needs a signed-in session or a validated enrolment token.

    Deliberately reads the session rather than :func:`is_authenticated`: with
    auth disabled nobody is really signed in, and there is nothing to enrol.
    """
    if not AUTH_ENABLED:
        return False
    return bool(request.session.get("authenticated")) or bool(request.session.get(_ENROL_OK))


def complete_enrolment(request: Request) -> None:
    """Enrolling doubles as a login; burn the token so its link cannot be reused.

    Only a token-unlocked enrolment spends the token — an already signed-in
    owner adding a spare key must not invalidate it.
    """
    by_token = request.session.pop(_ENROL_OK, None)
    if by_token and REGISTRATION_TOKEN:
        db.consume_token(_token_hash(REGISTRATION_TOKEN))
        log.info("Enrolment token spent; the link no longer works")
    request.session["authenticated"] = True


def _descriptors() -> list[PublicKeyCredentialDescriptor]:
    return [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
        for c in db.get_credentials()
    ]


# ── Registration ─────────────────────────────────────────────────────────────

def registration_options(request: Request) -> dict[str, Any]:
    """Build creation options and stash the challenge in the session."""
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=USER_HANDLE,
        user_name=USER_NAME,
        user_display_name=USER_NAME,
        # Refuse to enrol a key that is already registered.
        exclude_credentials=_descriptors(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable, so the browser can offer the passkey without a username.
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    request.session[_REG_CHALLENGE] = bytes_to_base64url(options.challenge)
    return json.loads(options_to_json(options))


def verify_registration(request: Request, credential: dict[str, Any], label: str | None) -> None:
    """Verify the attestation and store the new passkey. Raises on any mismatch."""
    challenge = request.session.pop(_REG_CHALLENGE, None)
    if not challenge:
        raise ValueError("No registration in progress — reload the page and try again.")

    verified = verify_registration_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(challenge),
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
    )

    transports = credential.get("response", {}).get("transports") or []
    db.add_credential(
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=",".join(transports) or None,
        label=(label or "").strip() or "Passkey",
    )
    log.info("Registered new passkey (label=%s)", label)


# ── Authentication ───────────────────────────────────────────────────────────

def authentication_options(request: Request) -> dict[str, Any]:
    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=_descriptors(),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    request.session[_AUTH_CHALLENGE] = bytes_to_base64url(options.challenge)
    return json.loads(options_to_json(options))


def verify_authentication(request: Request, credential: dict[str, Any]) -> None:
    """Verify the assertion and, on success, mark the session as logged in."""
    challenge = request.session.pop(_AUTH_CHALLENGE, None)
    if not challenge:
        raise ValueError("No login in progress — reload the page and try again.")

    raw_id = credential.get("rawId") or credential.get("id")
    stored = db.get_credential(raw_id) if raw_id else None
    if stored is None:
        raise ValueError("Unknown passkey.")

    verified = verify_authentication_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(challenge),
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=bytes(stored.public_key),
        credential_current_sign_count=stored.sign_count,
    )

    db.touch_credential(stored.credential_id, verified.new_sign_count)
    # Drop any pre-login session state before granting access.
    request.session.clear()
    request.session["authenticated"] = True
    log.info("Passkey login succeeded (label=%s)", stored.label)


def logout(request: Request) -> None:
    request.session.clear()

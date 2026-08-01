"""WebAuthn ceremony helpers — a thin wrapper over ``py_webauthn``.

Passkeys are registered as *discoverable* credentials, so signing in needs no
username: the browser offers whichever passkeys it holds for this site and the
one it returns identifies the user.
"""

import json

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

try:
    from config import RP_ID, RP_NAME, ORIGIN
except ImportError as exc:  # pragma: no cover - configuration error path
    raise RuntimeError(
        "config.py is missing the passkey settings. Add:\n"
        '  RP_ID = "seats.example.com"   # bare domain, no scheme/port\n'
        '  RP_NAME = "ULB Seat Reservation"\n'
        '  ORIGIN = "https://seats.example.com"'
    ) from exc

_SELECTION = AuthenticatorSelectionCriteria(
    resident_key=ResidentKeyRequirement.REQUIRED,
    user_verification=UserVerificationRequirement.PREFERRED,
)


def registration_options(user_handle: bytes, display_name: str,
                         exclude: list[bytes] | None = None) -> tuple[str, bytes]:
    """Options for creating a passkey. Returns (JSON for the browser, challenge)."""
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_handle,
        user_name=display_name,
        user_display_name=display_name,
        authenticator_selection=_SELECTION,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in (exclude or [])],
    )
    return options_to_json(options), options.challenge


def verify_registration(credential: dict, challenge: bytes):
    """Verify a newly created passkey. Raises on any mismatch."""
    return verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
    )


def authentication_options() -> tuple[str, bytes]:
    """Options for a usernameless login. Returns (JSON for the browser, challenge)."""
    options = generate_authentication_options(
        rp_id=RP_ID,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(options), options.challenge


def verify_authentication(credential: dict, challenge: bytes,
                          public_key: bytes, sign_count: int):
    """Verify a login assertion against a stored passkey. Raises on any mismatch."""
    return verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
    )


def describe_authenticator(credential: dict) -> str:
    """A friendly default name for a passkey, from what the browser told us."""
    transports = credential.get("response", {}).get("transports") or []
    if "internal" in transports:
        return "This device"
    if "hybrid" in transports:
        return "Phone or tablet"
    if "usb" in transports or "nfc" in transports:
        return "Security key"
    return "Passkey"


def parse_credential(raw: str | dict) -> dict:
    """Accept the credential either as a JSON string or an already-parsed dict."""
    return json.loads(raw) if isinstance(raw, str) else raw

"""Symmetric encryption for the SSO passwords we keep in the database.

Multi-user means other people's university credentials live in Postgres rather
than in a git-ignored ``config.py``, so they are stored encrypted with
AES-256-GCM. The key sits in ``config.py`` next to the DB password — this
protects against a leaked database dump, not against someone who already owns
the machine.

Every ciphertext is bound to a **context** string (for account credentials, the
account's row id) passed as the AEAD's associated data. The context is not part
of the token: decryption only succeeds if the caller supplies the same one, so a
ciphertext lifted from another row fails to authenticate instead of quietly
decrypting into the wrong place.

Token layout, base64url-encoded::

    0x01 | nonce (12B) | AES-256-GCM ciphertext + tag (16B)

The leading byte marks the format for anyone eyeballing the column; decryption
skips over it and lets the GCM tag reject anything that is not one of ours.
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from config import CREDENTIAL_KEY
except ImportError as exc:  # pragma: no cover - configuration error path
    raise RuntimeError(
        "config.py is missing CREDENTIAL_KEY. Generate one with:\n"
        "  uv run python -c 'import base64, secrets; "
        "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'"
    ) from exc

VERSION = b"\x01"
NONCE_BYTES = 12


def _root_key() -> bytes:
    """The configured key as raw bytes — base64url if it decodes, else literal."""
    if isinstance(CREDENTIAL_KEY, bytes):
        return CREDENTIAL_KEY
    try:
        return base64.urlsafe_b64decode(CREDENTIAL_KEY)
    except Exception:
        return CREDENTIAL_KEY.encode()


# Derived rather than used raw, so this key material is bound to one purpose:
# any other use of CREDENTIAL_KEY derives an unrelated subkey.
_key = HKDF(
    algorithm=SHA256(), length=32, salt=None,
    info=b"ulb-seat/account-credentials/v1",
).derive(_root_key())

_aead = AESGCM(_key)


def encrypt(plaintext: str, context: str) -> str:
    """Encrypt a secret, tied to ``context`` — only that context can decrypt it."""
    nonce = os.urandom(NONCE_BYTES)
    blob = _aead.encrypt(nonce, plaintext.encode(), context.encode())
    return base64.urlsafe_b64encode(VERSION + nonce + blob).decode()


def decrypt(token: str, context: str) -> str:
    """Decrypt a secret. Raises ValueError if the key or the context is wrong."""
    raw = base64.urlsafe_b64decode(token)
    nonce, blob = raw[1:1 + NONCE_BYTES], raw[1 + NONCE_BYTES:]
    try:
        return _aead.decrypt(nonce, blob, context.encode()).decode()
    except InvalidTag as exc:
        raise ValueError(
            f"Could not decrypt stored credentials for {context!r} — either "
            "CREDENTIAL_KEY does not match the key they were encrypted with, or "
            "the stored value does not belong to this record."
        ) from exc

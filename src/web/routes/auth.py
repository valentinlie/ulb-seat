"""Passkey registration and login: /login, /register, /auth/*, /settings."""

import json
import logging
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from webauthn.helpers import base64url_to_bytes

from core import db
from web import ctx, templates
from web import passkeys
from web.auth import (
    COOKIE_NAME,
    Auth,
    clear_session_cookie,
    optional_auth,
    require_auth,
    set_session_cookie,
)

log = logging.getLogger(__name__)

router = APIRouter()


class CeremonyStart(BaseModel):
    token: str | None = None
    display_name: str | None = None


class CeremonyFinish(BaseModel):
    ceremony: int
    credential: dict
    name: str | None = None


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


# ── Login ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if optional_auth(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", ctx(request, auth=None))


@router.post("/auth/login/options")
def login_options():
    options, challenge = passkeys.authentication_options()
    ceremony = db.create_challenge(challenge, purpose="login")
    return JSONResponse({"ceremony": ceremony, "options": json.loads(options)})


@router.post("/auth/login/verify")
def login_verify(body: CeremonyFinish):
    row = db.pop_challenge(body.ceremony, "login")
    if not row:
        return _error("This login attempt expired. Please try again.")

    credential = passkeys.parse_credential(body.credential)
    raw_id = base64url_to_bytes(credential.get("rawId") or credential.get("id", ""))
    stored = db.get_credential(raw_id)
    if not stored:
        return _error("Unknown passkey. Ask an admin for an invitation link.", 403)

    try:
        result = passkeys.verify_authentication(
            credential=credential,
            challenge=bytes(row["challenge"]),
            public_key=bytes(stored.public_key),
            sign_count=stored.sign_count,
        )
    except Exception as exc:
        log.warning("Passkey login failed: %s", exc)
        return _error("Passkey verification failed.", 403)

    db.touch_credential(raw_id, result.new_sign_count)
    accounts = db.get_accounts_for_user(stored.user_id)
    token = db.create_session(stored.user_id, accounts[0].id if accounts else None)

    response = JSONResponse({"redirect": "/" if accounts else "/accounts"})
    set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.delete_session(token)
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


# ── Registration (invite only) ───────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, token: str = ""):
    invite = db.get_valid_invite(token) if token else None
    return templates.TemplateResponse("register.html", ctx(
        request, auth=None, token=token, invite=invite,
    ))


@router.post("/auth/register/options")
def register_options(body: CeremonyStart):
    invite = db.get_valid_invite(body.token or "")
    if not invite:
        return _error("This invitation is invalid, already used, or expired.", 403)

    display_name = (body.display_name or "").strip()
    if not 1 <= len(display_name) <= 100:
        return _error("Please enter a name.")

    # The WebAuthn user handle is random and independent of the database id, so
    # nothing about our users leaks into the authenticator.
    handle = secrets.token_bytes(32)
    options, challenge = passkeys.registration_options(handle, display_name)
    ceremony = db.create_challenge(
        challenge, purpose="register", user_handle=handle,
        display_name=display_name, invite_token=invite.token,
    )
    return JSONResponse({"ceremony": ceremony, "options": json.loads(options)})


@router.post("/auth/register/verify")
def register_verify(body: CeremonyFinish):
    row = db.pop_challenge(body.ceremony, "register")
    if not row:
        return _error("This registration attempt expired. Please try again.")

    invite = db.get_valid_invite(row["invite_token"] or "")
    if not invite:
        return _error("This invitation is invalid, already used, or expired.", 403)

    credential = passkeys.parse_credential(body.credential)
    try:
        result = passkeys.verify_registration(credential, bytes(row["challenge"]))
    except Exception as exc:
        log.warning("Passkey registration failed: %s", exc)
        return _error("Passkey verification failed.", 403)

    # The very first account on a fresh install is always an admin, so a new
    # deployment is never left with nobody who can hand out invitations.
    is_admin = invite.grants_admin or db.count_users() == 0
    user_id = db.create_user(row["display_name"], bytes(row["user_handle"]), is_admin=is_admin)

    if not db.consume_invite(invite.token, user_id):
        db.delete_user(user_id)  # someone redeemed it between our two checks
        return _error("This invitation was just used by someone else.", 403)

    db.add_credential(
        user_id=user_id,
        credential_id=result.credential_id,
        public_key=result.credential_public_key,
        sign_count=result.sign_count,
        name=body.name or passkeys.describe_authenticator(credential),
    )
    if invite.account_id:
        db.add_member(invite.account_id, user_id)

    accounts = db.get_accounts_for_user(user_id)
    token = db.create_session(user_id, accounts[0].id if accounts else None)
    response = JSONResponse({"redirect": "/" if accounts else "/accounts"})
    set_session_cookie(response, token)
    return response


# ── Managing your own passkeys ───────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request, auth: Auth = Depends(require_auth)):
    return templates.TemplateResponse("settings.html", ctx(
        request, auth=auth, credentials=db.get_credentials_for_user(auth.user.id),
    ))


@router.post("/settings/name")
def rename_self(display_name: str = Form(...), auth: Auth = Depends(require_auth)):
    name = display_name.strip()
    if 1 <= len(name) <= 100:
        db.rename_user(auth.user.id, name)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/auth/passkeys/options")
def add_passkey_options(auth: Auth = Depends(require_auth)):
    existing = [bytes(c.credential_id) for c in db.get_credentials_for_user(auth.user.id)]
    options, challenge = passkeys.registration_options(
        bytes(auth.user.handle), auth.user.display_name, exclude=existing,
    )
    ceremony = db.create_challenge(challenge, purpose="add", user_id=auth.user.id)
    return JSONResponse({"ceremony": ceremony, "options": json.loads(options)})


@router.post("/auth/passkeys/verify")
def add_passkey_verify(body: CeremonyFinish, auth: Auth = Depends(require_auth)):
    row = db.pop_challenge(body.ceremony, "add")
    if not row or row["user_id"] != auth.user.id:
        return _error("This request expired. Please try again.")

    credential = passkeys.parse_credential(body.credential)
    try:
        result = passkeys.verify_registration(credential, bytes(row["challenge"]))
    except Exception as exc:
        log.warning("Adding a passkey failed: %s", exc)
        return _error("Passkey verification failed.", 403)

    db.add_credential(
        user_id=auth.user.id,
        credential_id=result.credential_id,
        public_key=result.credential_public_key,
        sign_count=result.sign_count,
        name=body.name or passkeys.describe_authenticator(credential),
    )
    return JSONResponse({"redirect": "/settings"})


@router.post("/settings/passkeys/{cred_id}/delete")
def delete_passkey(cred_id: int, auth: Auth = Depends(require_auth)):
    # db refuses to delete the last one — losing it would lock the user out.
    db.delete_credential(cred_id, auth.user.id)
    return RedirectResponse(url="/settings", status_code=303)

"""Passkey login, logout and passkey management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config import AUTH_ENABLED
from core import db
from web import templates, ctx
from web.auth import (
    authentication_options,
    complete_enrolment,
    is_authenticated,
    logout,
    registration_allowed,
    registration_options,
    require_auth,
    unlock_enrolment,
    verify_authentication,
    verify_registration,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _auth_off() -> HTTPException:
    return HTTPException(status_code=404, detail="Authentication is disabled.")


def _fail(exc: Exception) -> JSONResponse:
    """WebAuthn failures are all "it didn't verify" as far as the browser cares."""
    log.warning("WebAuthn ceremony failed: %s", exc)
    return JSONResponse({"error": str(exc)}, status_code=400)


# ── Login ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", ctx(request, has_credentials=bool(db.get_credentials())))


@router.post("/auth/login/options")
def login_options(request: Request):
    if not AUTH_ENABLED:
        raise _auth_off()
    if not db.get_credentials():
        raise HTTPException(status_code=400, detail="No passkey registered yet.")
    return authentication_options(request)


@router.post("/auth/login/verify")
async def login_verify(request: Request):
    if not AUTH_ENABLED:
        raise _auth_off()
    try:
        verify_authentication(request, await request.json())
    except Exception as exc:
        return _fail(exc)
    return {"ok": True}


@router.post("/logout")
def logout_route(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    logout(request)
    return RedirectResponse("/login", status_code=303)


# ── Passkey management ───────────────────────────────────────────────────────

@router.get("/passkeys", response_class=HTMLResponse)
def passkeys_page(request: Request, token: str | None = None):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    if token is not None:
        # Bounce to a clean URL either way so the token leaves the address bar.
        unlock_enrolment(request, token)
        return RedirectResponse("/passkeys", status_code=303)
    if not registration_allowed(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("passkeys.html", ctx(
        request, credentials=db.get_credentials(), active_page="passkeys",
    ))


@router.post("/auth/register/options")
def register_options(request: Request):
    if not AUTH_ENABLED:
        raise _auth_off()
    if not registration_allowed(request):
        raise HTTPException(status_code=403, detail="A passkey is already registered.")
    return registration_options(request)


@router.post("/auth/register/verify")
async def register_verify(request: Request):
    if not AUTH_ENABLED:
        raise _auth_off()
    if not registration_allowed(request):
        raise HTTPException(status_code=403, detail="A passkey is already registered.")
    body = await request.json()
    try:
        verify_registration(request, body.get("credential", {}), body.get("label"))
    except Exception as exc:
        return _fail(exc)
    complete_enrolment(request)
    return {"ok": True}


@router.post("/passkeys/{credential_id}/delete")
def delete_passkey(request: Request, credential_id: str, _user: str = Depends(require_auth)):
    db.delete_credential(credential_id)
    return RedirectResponse("/passkeys", status_code=303)

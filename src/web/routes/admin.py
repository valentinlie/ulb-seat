"""Admin routes: /admin — invitations and users."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import ORIGIN
from core import db
from web import ctx, templates
from web.auth import Auth, require_admin

log = logging.getLogger(__name__)

router = APIRouter()


def _admin_page(request: Request, auth: Auth, new_token: str | None = None):
    users = db.get_all_users()
    return templates.TemplateResponse("admin.html", ctx(
        request, auth=auth,
        users=users,
        invites=db.get_all_invites(),
        accounts=db.get_all_accounts(),
        new_token=new_token,
        admin_count=sum(1 for u in users if u.is_admin),
        now=db.now(),
        # Built from the configured origin, not the request: behind a reverse
        # proxy request.base_url can come out as plain http.
        invite_base=ORIGIN.rstrip("/"),
    ))


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, auth: Auth = Depends(require_admin)):
    return _admin_page(request, auth)


@router.post("/admin/invites", response_class=HTMLResponse)
def create_invite(
    request: Request,
    note: str = Form(""),
    grants_admin: bool = Form(False),
    account_id: str = Form(""),
    ttl_days: int = Form(14),
    auth: Auth = Depends(require_admin),
):
    """Mint an invitation link. Optionally pre-shares one ULB account with them."""
    shared = int(account_id) if account_id else None
    token = db.create_invite(
        created_by=auth.user.id,
        note=note.strip() or None,
        grants_admin=grants_admin,
        account_id=shared,
        ttl=timedelta(days=max(1, min(ttl_days, 90))),
    )
    return _admin_page(request, auth, new_token=token)


@router.post("/admin/invites/{token}/revoke", response_class=HTMLResponse)
def revoke_invite(request: Request, token: str, auth: Auth = Depends(require_admin)):
    db.revoke_invite(token)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/users/{user_id}/admin", response_class=HTMLResponse)
def toggle_admin(request: Request, user_id: int, auth: Auth = Depends(require_admin)):
    user = db.get_user(user_id)
    if user and not (user.is_admin and db.count_admins() <= 1):
        db.set_user_admin(user_id, not user.is_admin)  # never demote the last admin
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
def delete_user(request: Request, user_id: int, auth: Auth = Depends(require_admin)):
    user = db.get_user(user_id)
    if user and user.id != auth.user.id and not (user.is_admin and db.count_admins() <= 1):
        # Their memberships cascade with them, so anything only they could see
        # would be stranded — take it over rather than lose track of it.
        taken = db.transfer_sole_owned_accounts(user_id, auth.user.id)
        if taken:
            log.info("Took over %d account(s) from deleted user %r.", taken, user.display_name)
        db.delete_sessions_for_user(user_id)
        db.delete_user(user_id)  # passkeys and memberships cascade
    return RedirectResponse(url="/admin", status_code=303)

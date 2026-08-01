"""ULB account routes: /accounts/* — credentials, members, switching."""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import db, systemd
from core.auth import forget_cached_session
from web import ctx, templates
from web.auth import Auth, require_auth

log = logging.getLogger(__name__)

router = APIRouter()


def _member_or_none(account_id: int, user_id: int) -> db.Account | None:
    """The account, but only if this user is a member of it."""
    if not db.is_member(user_id, account_id):
        return None
    return db.get_account(account_id)


@router.get("/accounts", response_class=HTMLResponse)
def account_list(request: Request, auth: Auth = Depends(require_auth)):
    return templates.TemplateResponse("accounts.html", ctx(request, auth=auth))


@router.post("/accounts/switch", response_class=HTMLResponse)
def account_switch(request: Request, account_id: int = Form(...),
                   auth: Auth = Depends(require_auth)):
    """Point the session at another of the user's accounts (the nav switcher)."""
    if db.is_member(auth.user.id, account_id):
        db.set_active_account(auth.token, account_id)
    return RedirectResponse(url=request.headers.get("referer") or "/", status_code=303)


@router.get("/accounts/new", response_class=HTMLResponse)
def account_new(request: Request, auth: Auth = Depends(require_auth)):
    return templates.TemplateResponse("account_form.html", ctx(
        request, auth=auth, account=None, members=[], candidates=[],
    ))


@router.post("/accounts", response_class=HTMLResponse)
def account_create(
    request: Request,
    label: str = Form(...),
    sso_username: str = Form(...),
    sso_password: str = Form(...),
    library_number: str = Form(...),
    auth: Auth = Depends(require_auth),
):
    account_id = db.create_account(
        label=label.strip(), sso_username=sso_username.strip(),
        sso_password=sso_password, library_number=library_number.strip(),
        owner_id=auth.user.id,
    )
    db.set_active_account(auth.token, account_id)  # land on the account you just made
    return RedirectResponse(url="/", status_code=303)


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def account_edit(request: Request, account_id: int, auth: Auth = Depends(require_auth)):
    account = _member_or_none(account_id, auth.user.id)
    if not account:
        return RedirectResponse(url="/accounts", status_code=303)
    members = db.get_members(account_id)
    member_ids = {u.id for u in members}
    return templates.TemplateResponse("account_form.html", ctx(
        request, auth=auth, account=account, members=members,
        candidates=[u for u in db.get_all_users() if u.id not in member_ids],
    ))


@router.post("/accounts/{account_id}", response_class=HTMLResponse)
def account_update(
    request: Request,
    account_id: int,
    label: str = Form(...),
    sso_username: str = Form(...),
    sso_password: str = Form(""),
    library_number: str = Form(...),
    auth: Auth = Depends(require_auth),
):
    account = _member_or_none(account_id, auth.user.id)
    if not account:
        return RedirectResponse(url="/accounts", status_code=303)
    db.update_account(
        account_id, label=label.strip(), sso_username=sso_username.strip(),
        library_number=library_number.strip(), sso_password=sso_password or None,
    )
    # Cached SSO cookies belong to the old credentials; drop them so the next
    # booking logs in fresh instead of failing on a stale session.
    forget_cached_session(account)
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/accounts/{account_id}/delete", response_class=HTMLResponse)
def account_delete(request: Request, account_id: int, auth: Auth = Depends(require_auth)):
    account = _member_or_none(account_id, auth.user.id)
    if not account:
        return RedirectResponse(url="/accounts", status_code=303)
    # Jobs and history cascade in the DB, but their timers live in systemd and
    # have to be torn down explicitly first.
    for job in db.get_all_jobs(account_id):
        systemd.remove_job_timer(job.id)
    forget_cached_session(account)
    db.delete_account(account_id)
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/accounts/{account_id}/members", response_class=HTMLResponse)
def account_add_member(request: Request, account_id: int, user_id: int = Form(...),
                       auth: Auth = Depends(require_auth)):
    """Share an account with another user — they can then manage its jobs."""
    if _member_or_none(account_id, auth.user.id) and db.get_user(user_id):
        db.add_member(account_id, user_id)
    return RedirectResponse(url=f"/accounts/{account_id}/edit", status_code=303)


@router.post("/accounts/{account_id}/members/{user_id}/delete", response_class=HTMLResponse)
def account_remove_member(request: Request, account_id: int, user_id: int,
                          auth: Auth = Depends(require_auth)):
    if _member_or_none(account_id, auth.user.id):
        db.remove_member(account_id, user_id)
    if user_id == auth.user.id:
        return RedirectResponse(url="/accounts", status_code=303)
    return RedirectResponse(url=f"/accounts/{account_id}/edit", status_code=303)

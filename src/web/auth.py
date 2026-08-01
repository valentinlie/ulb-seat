"""Session cookies and the auth dependencies for the dashboard.

Login is by passkey (see :mod:`web.passkeys` and ``web.routes.auth``); this
module only deals with what happens *after* a successful ceremony: an opaque
session token in an HttpOnly cookie, and the dependencies that turn it back
into a user, their accounts, and the one they are currently looking at.
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response

from core import db

try:
    from config import COOKIE_SECURE
except ImportError:
    COOKIE_SECURE = True  # passkeys need HTTPS anyway; only relax for local dev

COOKIE_NAME = "ulb_session"
_MAX_AGE = int(db.SESSION_TTL.total_seconds())

# Sessions are rows in the database rather than signed cookies, so there is no
# SESSION_SECRET to keep — the cookie is an opaque, revocable token.


@dataclass
class Auth:
    """The logged-in user, the accounts they may manage, and the active one."""

    user: db.User
    accounts: list[db.Account]
    account: db.Account | None
    token: str

    @property
    def is_admin(self) -> bool:
        return self.user.is_admin


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=_MAX_AGE, httponly=True,
        secure=COOKIE_SECURE, samesite="lax", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _redirect(to: str) -> HTTPException:
    """Redirect from inside a dependency, which cannot return a response.

    ``HX-Redirect`` makes HTMX navigate instead of swapping the login page into
    whatever fragment it was updating.
    """
    return HTTPException(status_code=303, detail="Not authenticated",
                         headers={"Location": to, "HX-Redirect": to})


def optional_auth(request: Request) -> Auth | None:
    """Resolve the session cookie, or None when not logged in."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    user = db.get_user(session["user_id"])
    if not user:
        return None

    accounts = db.get_accounts_for_user(user.id)
    active = next((a for a in accounts if a.id == session["active_account_id"]), None)
    if active is None and accounts:
        # Membership was revoked, or the session predates the account: pick one
        # and remember it so the switcher and the pages agree.
        active = accounts[0]
        db.set_active_account(token, active.id)
    return Auth(user=user, accounts=accounts, account=active, token=token)


def require_auth(request: Request) -> Auth:
    auth = optional_auth(request)
    if auth is None:
        raise _redirect("/login")
    return auth


def require_account(auth: Auth = Depends(require_auth)) -> Auth:
    """For pages that act on a ULB account — send the user to create one first."""
    if auth.account is None:
        raise _redirect("/accounts")
    return auth


def require_admin(auth: Auth = Depends(require_auth)) -> Auth:
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    return auth

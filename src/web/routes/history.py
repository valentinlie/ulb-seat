"""History route: GET /history"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core import db
from web import templates, ctx
from web.auth import Auth, require_account

router = APIRouter()


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, auth: Auth = Depends(require_account)):
    bookings = db.get_recent_bookings(auth.account.id, limit=20)
    return templates.TemplateResponse("history.html", ctx(request, auth=auth, bookings=bookings))

"""History route: GET /history"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core import db
from web import templates, ctx
from web.auth import require_auth

router = APIRouter()


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, _user: str = Depends(require_auth)):
    bookings = db.get_recent_bookings(limit=100)
    return templates.TemplateResponse("history.html", ctx(request, bookings=bookings))

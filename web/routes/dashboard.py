"""Dashboard route: GET /"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core import db
from web import templates, ctx
from web.auth import require_auth

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _user: str = Depends(require_auth)):
    jobs = db.get_all_jobs()
    bookings = db.get_recent_bookings(limit=20)
    upcoming = [j for j in jobs if j["enabled"]]
    return templates.TemplateResponse("dashboard.html", ctx(
        request, jobs=upcoming, bookings=bookings,
    ))

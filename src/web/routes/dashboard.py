"""Dashboard route: GET /"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core import db
from web import templates, ctx
from web.auth import Auth, require_account

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, auth: Auth = Depends(require_account)):
    jobs = db.get_all_jobs(auth.account.id)
    bookings = db.get_recent_bookings(auth.account.id, limit=5)
    upcoming = [j for j in jobs if j.enabled]
    return templates.TemplateResponse("dashboard.html", ctx(
        request, auth=auth, jobs=upcoming, bookings=bookings,
    ))

"""HTMX partial routes: GET /partials/*"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core import db
from web import templates, ctx
from web.auth import require_auth

router = APIRouter()


@router.get("/partials/job-list", response_class=HTMLResponse)
def partial_job_list(request: Request, _user: str = Depends(require_auth)):
    jobs = db.get_all_jobs()
    return templates.TemplateResponse("partials/job_list.html", ctx(request, jobs=jobs))


@router.get("/partials/history", response_class=HTMLResponse)
def partial_history(request: Request, _user: str = Depends(require_auth)):
    bookings = db.get_recent_bookings(limit=20)
    return templates.TemplateResponse("partials/history_list.html", ctx(request, bookings=bookings))

"""Jobs routes: /jobs/*"""

import logging
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import db
from core import systemd
from core.booking import execute_booking
from core.exceptions import BookingError
from web import templates, ctx
from web.auth import require_auth

log = logging.getLogger(__name__)

router = APIRouter()

_TZ = ZoneInfo("Europe/Berlin")


@router.get("/jobs", response_class=HTMLResponse)
def job_list(request: Request, _user: str = Depends(require_auth)):
    jobs = db.get_all_jobs()
    return templates.TemplateResponse("jobs.html", ctx(request, jobs=jobs))


@router.get("/jobs/new", response_class=HTMLResponse)
def job_new(request: Request, _user: str = Depends(require_auth)):
    return templates.TemplateResponse("job_form.html", ctx(request, job=None))


def _build_run_at(run_date: str, run_hour: int, run_minute: int) -> datetime | None:
    """Build an aware datetime from DD.MM.YYYY date string + hour/minute."""
    if not run_date:
        return None
    try:
        dt = datetime.strptime(run_date, "%d.%m.%Y")
    except ValueError:
        return None
    return dt.replace(hour=run_hour, minute=run_minute, tzinfo=_TZ)


def _parse_date(value: str) -> date | None:
    """Parse a DD.MM.YYYY form value into a date."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        return None


class JobForm:
    """Job form fields shared by the create and update routes."""

    def __init__(
        self,
        name: str = Form(...),
        library_id: int = Form(...),
        time_slot: str = Form(...),
        group_room: bool = Form(False),
        preferred_section: str = Form(""),
        job_type: str = Form(...),
        cron_days: str = Form(""),
        date_offset: int = Form(None),
        cron_hour: int = Form(0),
        cron_minute: int = Form(0),
        run_date: str = Form(""),
        run_hour: int = Form(0),
        run_minute: int = Form(0),
        target_date: str = Form(""),
    ):
        recurring = job_type == "recurring"
        self.data = {
            "name": name,
            "library_id": library_id,
            "time_slot": time_slot,
            "group_room": group_room,
            "preferred_section": preferred_section or None,
            "recurring": recurring,
            "cron_days": cron_days if recurring else None,
            "date_offset": date_offset if recurring else None,
            "cron_hour": cron_hour if recurring else None,
            "cron_minute": cron_minute if recurring else None,
            "run_at": _build_run_at(run_date, run_hour, run_minute) if not recurring else None,
            "target_date": _parse_date(target_date) if not recurring else None,
        }


@router.post("/jobs", response_class=HTMLResponse)
def job_create(request: Request, form: JobForm = Depends(),
               _user: str = Depends(require_auth)):
    job_id = db.create_job({**form.data, "enabled": True})
    systemd.sync_job_timer(db.get_job(job_id))
    return RedirectResponse(url="/jobs", status_code=303)


@router.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def job_edit(request: Request, job_id: int, _user: str = Depends(require_auth)):
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs", status_code=303)
    # Fill the form-only fields from run_at
    if job.run_at:
        job.run_date = job.run_at.strftime("%d.%m.%Y")
        job.run_hour = job.run_at.hour
        job.run_minute = job.run_at.minute
    return templates.TemplateResponse("job_form.html", ctx(request, job=job))


@router.post("/jobs/{job_id}", response_class=HTMLResponse)
def job_update(request: Request, job_id: int, form: JobForm = Depends(),
               _user: str = Depends(require_auth)):
    existing = db.get_job(job_id)
    if not existing:
        return RedirectResponse(url="/jobs", status_code=303)
    db.update_job(job_id, {**form.data, "enabled": existing.enabled})
    systemd.sync_job_timer(db.get_job(job_id))
    return RedirectResponse(url="/jobs", status_code=303)


@router.post("/jobs/{job_id}/delete", response_class=HTMLResponse)
def job_delete(request: Request, job_id: int, _user: str = Depends(require_auth)):
    systemd.remove_job_timer(job_id)
    db.delete_job(job_id)
    # Return empty string so HTMX removes the row
    return HTMLResponse("")


@router.post("/jobs/{job_id}/toggle", response_class=HTMLResponse)
def job_toggle(request: Request, job_id: int, _user: str = Depends(require_auth)):
    new_state = db.toggle_job(job_id)
    job = db.get_job(job_id)
    if job:
        if new_state:
            systemd.sync_job_timer(job)
        else:
            systemd.remove_job_timer(job_id)
    return templates.TemplateResponse("partials/job_row.html", ctx(request, job=job))


@router.post("/jobs/{job_id}/run", response_class=HTMLResponse)
def job_run_now(request: Request, job_id: int, _user: str = Depends(require_auth)):
    job = db.get_job(job_id)
    if not job:
        return HTMLResponse('<div role="alert">Job not found</div>')

    # Determine target date
    if job.recurring and job.date_offset is not None:
        target_date = date.today() + timedelta(days=job.date_offset)
    elif job.target_date:
        target_date = job.target_date
    else:
        return HTMLResponse('<div role="alert">No target date configured</div>')

    log_id = db.log_booking_start(
        job_id=job.id,
        job_name=job.name,
        library_id=job.library_id,
        target_date=target_date,
        time_slot=job.time_slot,
        group_room=job.group_room,
        manual=True,
    )

    try:
        result = execute_booking(
            library_id=job.library_id,
            date=target_date.strftime("%d.%m.%Y"),
            time_slot=job.time_slot,
            group_room=job.group_room,
            preferred_section=job.preferred_section or "",
        )
        db.log_booking_finish(log_id, "success", result.get("seat_desc"), result.get("message"))
        alert = f'<div role="alert" class="alert-success">Booked: {escape(result.get("seat_desc") or "OK")}</div>'
    except BookingError as e:
        db.log_booking_finish(log_id, "failed", message=str(e))
        alert = f'<div role="alert" class="alert-error">Failed: {escape(str(e))}</div>'
    except Exception as e:
        db.log_booking_finish(log_id, "error", message=str(e))
        alert = f'<div role="alert" class="alert-error">Error: {escape(str(e))}</div>'

    return HTMLResponse(alert)

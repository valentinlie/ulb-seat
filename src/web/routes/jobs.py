"""Jobs routes: /jobs/*"""

import logging
import re
from datetime import date, datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import db
from core import systemd
from core.booking import DAYS_AHEAD, execute_booking, opens_at
from core.exceptions import BookingError
from web import templates, ctx
from web.auth import Auth, require_account

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def job_list(request: Request, auth: Auth = Depends(require_account)):
    jobs = db.get_all_jobs(auth.account.id)
    return templates.TemplateResponse("jobs.html", ctx(request, auth=auth, jobs=jobs))


@router.get("/jobs/new", response_class=HTMLResponse)
def job_new(request: Request, auth: Auth = Depends(require_account)):
    return templates.TemplateResponse("job_form.html", ctx(request, auth=auth, job=None))


def _parse_date(value: str) -> date | None:
    """Parse a DD.MM.YYYY form value into a date."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        return None


def _seat_numbers(raw: str) -> list[int]:
    """Parse "600, 6001" into [600, 6001] — order matters, duplicates don't."""
    return list(dict.fromkeys(int(n) for n in re.findall(r"\d+", raw)))


class JobForm:
    """Job form fields shared by the create and update routes."""

    def __init__(
        self,
        name: str = Form(...),
        library_id: int = Form(...),
        time_slot: str = Form(...),
        category: int = Form(1),
        preferred_section: str = Form(""),
        preferred_seats: str = Form(""),
        job_type: str = Form(...),
        cron_days: str = Form(""),
        target_date: str = Form(""),
    ):
        recurring = job_type == "recurring"
        self.data = {
            "name": name,
            "library_id": library_id,
            "time_slot": time_slot,
            "category": category,
            "preferred_section": preferred_section or None,
            "preferred_seats": _seat_numbers(preferred_seats),
            "recurring": recurring,
            "cron_days": cron_days if recurring else None,
            "target_date": _parse_date(target_date) if not recurring else None,
        }

    def error(self) -> str | None:
        """Why this job could never fire, if it could not."""
        if self.data["recurring"]:
            return None
        target = self.data["target_date"]
        if target is None:
            return "Enter the target date as DD.MM.YYYY."
        if opens_at(target) <= db.now():
            return (f"Bookings for {target:%d.%m.%Y} are already open — "
                    "book it right away from the Dashboard instead.")
        return None


def _form_again(request: Request, auth: Auth, form: JobForm, job_id: int | None):
    """Re-show the submitted form with its error."""
    job = db.Job(id=job_id, account_id=auth.account.id, enabled=True,
                 created_at=db.now(), **form.data)
    return templates.TemplateResponse("job_form.html", ctx(
        request, auth=auth, job=job, error=form.error(),
    ), status_code=400)


@router.post("/jobs", response_class=HTMLResponse)
def job_create(request: Request, form: JobForm = Depends(),
               auth: Auth = Depends(require_account)):
    if form.error():
        return _form_again(request, auth, form, None)
    job_id = db.create_job(auth.account.id, {**form.data, "enabled": True})
    systemd.sync_job_timer(db.get_job(job_id))
    return RedirectResponse(url="/jobs", status_code=303)


@router.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def job_edit(request: Request, job_id: int, auth: Auth = Depends(require_account)):
    job = db.get_job(job_id, auth.account.id)
    if not job:
        return RedirectResponse(url="/jobs", status_code=303)
    return templates.TemplateResponse("job_form.html", ctx(request, auth=auth, job=job))


@router.post("/jobs/{job_id}", response_class=HTMLResponse)
def job_update(request: Request, job_id: int, form: JobForm = Depends(),
               auth: Auth = Depends(require_account)):
    existing = db.get_job(job_id, auth.account.id)
    if not existing:
        return RedirectResponse(url="/jobs", status_code=303)
    if form.error():
        return _form_again(request, auth, form, job_id)
    db.update_job(job_id, {**form.data, "enabled": existing.enabled})
    systemd.sync_job_timer(db.get_job(job_id))
    return RedirectResponse(url="/jobs", status_code=303)


@router.post("/jobs/{job_id}/delete", response_class=HTMLResponse)
def job_delete(request: Request, job_id: int, auth: Auth = Depends(require_account)):
    if not db.get_job(job_id, auth.account.id):
        return HTMLResponse("")
    systemd.remove_job_timer(job_id)
    db.delete_job(job_id)
    # Return empty string so HTMX removes the row
    return HTMLResponse("")


@router.post("/jobs/{job_id}/toggle", response_class=HTMLResponse)
def job_toggle(request: Request, job_id: int, auth: Auth = Depends(require_account)):
    if not db.get_job(job_id, auth.account.id):
        return HTMLResponse("")
    new_state = db.toggle_job(job_id)
    job = db.get_job(job_id)
    if job:
        if new_state:
            systemd.sync_job_timer(job)
        else:
            systemd.remove_job_timer(job_id)
    return templates.TemplateResponse("partials/job_row.html", ctx(request, auth=auth, job=job))


@router.post("/jobs/{job_id}/run", response_class=HTMLResponse)
def job_run_now(request: Request, job_id: int, auth: Auth = Depends(require_account)):
    job = db.get_job(job_id, auth.account.id)
    if not job:
        return HTMLResponse('<div role="alert">Job not found</div>')

    # Determine target date
    if job.recurring:
        target_date = date.today() + timedelta(days=DAYS_AHEAD)
    elif job.target_date:
        target_date = job.target_date
    else:
        return HTMLResponse('<div role="alert">No target date configured</div>')

    log_id = db.log_booking_start(
        account_id=auth.account.id,
        job_id=job.id,
        job_name=job.name,
        library_id=job.library_id,
        target_date=target_date,
        time_slot=job.time_slot,
        category=job.category,
        manual=True,
    )

    try:
        result = execute_booking(
            account=auth.account,
            library_id=job.library_id,
            date=target_date.strftime("%d.%m.%Y"),
            time_slot=job.time_slot,
            category=job.category,
            preferred_section=job.preferred_section or "",
            preferred_seats=job.preferred_seats,
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

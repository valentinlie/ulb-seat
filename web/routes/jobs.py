"""Jobs routes: /jobs/*"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import db
from core import scheduler as sched
from core.booking import execute_booking
from core.exceptions import BookingError
from web import templates, ctx
from web.auth import require_auth

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def job_list(request: Request, _user: str = Depends(require_auth)):
    jobs = db.get_all_jobs()
    return templates.TemplateResponse("jobs.html", ctx(request, jobs=jobs))


@router.get("/jobs/new", response_class=HTMLResponse)
def job_new(request: Request, _user: str = Depends(require_auth)):
    return templates.TemplateResponse("job_form.html", ctx(request, job=None))


def _build_run_at(run_date: str, run_hour: int, run_minute: int) -> str | None:
    """Build ISO datetime from DD.MM.YYYY date string + hour/minute."""
    if not run_date:
        return None
    try:
        dt = datetime.strptime(run_date, "%d.%m.%Y")
        dt = dt.replace(hour=run_hour, minute=run_minute)
        return dt.isoformat()
    except ValueError:
        return None


@router.post("/jobs", response_class=HTMLResponse)
def job_create(
    request: Request,
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
    _user: str = Depends(require_auth),
):
    recurring = job_type == "recurring"
    run_at = _build_run_at(run_date, run_hour, run_minute) if not recurring else None
    data = {
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
        "run_at": run_at,
        "target_date": target_date if not recurring else None,
        "enabled": True,
    }
    job_id = db.create_job(data)
    job = db.get_job(job_id)
    sched.schedule_job(job)
    return RedirectResponse(url="/jobs", status_code=303)


@router.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def job_edit(request: Request, job_id: int, _user: str = Depends(require_auth)):
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(url="/jobs", status_code=303)
    # Parse run_at ISO string into separate fields for the form
    job = dict(job)
    if job.get("run_at"):
        try:
            dt = datetime.fromisoformat(job["run_at"])
            job["run_date"] = dt.strftime("%d.%m.%Y")
            job["run_hour"] = dt.hour
            job["run_minute"] = dt.minute
        except ValueError:
            job["run_date"] = ""
            job["run_hour"] = 0
            job["run_minute"] = 0
    return templates.TemplateResponse("job_form.html", ctx(request, job=job))


@router.post("/jobs/{job_id}", response_class=HTMLResponse)
def job_update(
    request: Request,
    job_id: int,
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
    _user: str = Depends(require_auth),
):
    recurring = job_type == "recurring"
    run_at = _build_run_at(run_date, run_hour, run_minute) if not recurring else None
    data = {
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
        "run_at": run_at,
        "target_date": target_date if not recurring else None,
        "enabled": True,
    }
    db.update_job(job_id, data)
    job = db.get_job(job_id)
    sched.schedule_job(job)
    return RedirectResponse(url="/jobs", status_code=303)


@router.post("/jobs/{job_id}/delete", response_class=HTMLResponse)
def job_delete(request: Request, job_id: int, _user: str = Depends(require_auth)):
    sched.remove_job_from_scheduler(job_id)
    db.delete_job(job_id)
    # Return empty string so HTMX removes the row
    return HTMLResponse("")


@router.post("/jobs/{job_id}/toggle", response_class=HTMLResponse)
def job_toggle(request: Request, job_id: int, _user: str = Depends(require_auth)):
    new_state = db.toggle_job(job_id)
    job = db.get_job(job_id)
    if job:
        if new_state:
            sched.schedule_job(job)
        else:
            sched.remove_job_from_scheduler(job_id)
    return templates.TemplateResponse("partials/job_row.html", ctx(request, job=job))


@router.post("/jobs/{job_id}/run", response_class=HTMLResponse)
def job_run_now(request: Request, job_id: int, _user: str = Depends(require_auth)):
    job = db.get_job(job_id)
    if not job:
        return HTMLResponse('<div role="alert">Job not found</div>')

    # Determine target date
    if job["recurring"] and job.get("date_offset") is not None:
        target_date = (datetime.now() + timedelta(days=job["date_offset"])).strftime("%d.%m.%Y")
    elif job.get("target_date"):
        target_date = job["target_date"]
    else:
        return HTMLResponse('<div role="alert">No target date configured</div>')

    log_id = db.log_booking_start(
        job_id=job["id"],
        job_name=job["name"],
        library_id=job["library_id"],
        target_date=target_date,
        time_slot=job["time_slot"],
        group_room=bool(job["group_room"]),
        manual=True,
    )

    try:
        result = execute_booking(
            library_id=job["library_id"],
            date=target_date,
            time_slot=job["time_slot"],
            group_room=bool(job["group_room"]),
            preferred_section=job.get("preferred_section") or "",
        )
        db.log_booking_finish(log_id, "success", result.get("seat_desc"), result.get("message"))
        alert = f'<div role="alert" class="alert-success">Booked: {result.get("seat_desc", "OK")}</div>'
    except BookingError as e:
        db.log_booking_finish(log_id, "failed", message=str(e))
        alert = f'<div role="alert" class="alert-error">Failed: {e}</div>'
    except Exception as e:
        db.log_booking_finish(log_id, "error", message=str(e))
        alert = f'<div role="alert" class="alert-error">Error: {e}</div>'

    return HTMLResponse(alert)

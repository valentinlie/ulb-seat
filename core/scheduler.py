"""APScheduler integration for scheduled bookings."""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from core import db
from core.booking import execute_booking
from core.exceptions import BookingError

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Europe/Berlin")

# Day name → weekday index (mon=0 … sun=6)
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES)}


def _target_days_to_trigger_days(target_days: str, date_offset: int) -> str:
    """Convert target days (days user wants a seat) to trigger days
    (days the scheduler should fire) by subtracting date_offset.

    Example: target_days="mon", offset=3 → trigger on "fri"
             (fire Friday, book 3 days ahead = Monday)
    """
    trigger = []
    for day in target_days.split(","):
        day = day.strip().lower()
        if day not in DAY_INDEX:
            continue
        trigger_idx = (DAY_INDEX[day] - date_offset) % 7
        trigger.append(DAY_NAMES[trigger_idx])
    return ",".join(trigger) if trigger else "mon,tue,wed,thu,fri"


def run_booking_job(job_id: int) -> None:
    """Execute a booking for the given job. Called by APScheduler."""
    job = db.get_job(job_id)
    if not job:
        log.error("Job %d not found, skipping.", job_id)
        return
    if not job["enabled"]:
        log.info("Job %d is disabled, skipping.", job_id)
        return

    # Determine target date
    if job["recurring"] and job["date_offset"] is not None:
        target_date = (datetime.now() + timedelta(days=job["date_offset"])).strftime("%d.%m.%Y")
    elif job["target_date"]:
        target_date = job["target_date"]
    else:
        log.error("Job %d has no target date configured.", job_id)
        return

    log_id = db.log_booking_start(
        job_id=job_id,
        job_name=job["name"],
        library_id=job["library_id"],
        target_date=target_date,
        time_slot=job["time_slot"],
        group_room=bool(job["group_room"]),
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
        log.info("Job %d (%s) succeeded: %s", job_id, job["name"], result.get("message"))
    except BookingError as e:
        db.log_booking_finish(log_id, "failed", message=str(e))
        log.warning("Job %d (%s) failed: %s", job_id, job["name"], e)
    except Exception as e:
        db.log_booking_finish(log_id, "error", message=str(e))
        log.exception("Job %d (%s) error:", job_id, job["name"])

    # Auto-disable one-shot jobs after execution
    if not job["recurring"]:
        db.disable_job(job_id)
        remove_job_from_scheduler(job_id)


def schedule_job(job: dict) -> None:
    """Register a job with APScheduler."""
    job_apid = f"job_{job['id']}"

    # Remove existing schedule if any
    if scheduler.get_job(job_apid):
        scheduler.remove_job(job_apid)

    if not job["enabled"]:
        return

    if job["recurring"]:
        target_days = job.get("cron_days", "mon,tue,wed,thu,fri")
        offset = job.get("date_offset", 2) or 0
        trigger_days = _target_days_to_trigger_days(target_days, offset)
        log.info("Job %d: target days=%s, offset=%d → trigger days=%s",
                 job["id"], target_days, offset, trigger_days)
        trigger = CronTrigger(
            day_of_week=trigger_days,
            hour=job.get("cron_hour", 0),
            minute=job.get("cron_minute", 0),
            timezone="Europe/Berlin",
        )
    else:
        if not job.get("run_at"):
            log.warning("One-shot job %d has no run_at, skipping.", job["id"])
            return
        trigger = DateTrigger(
            run_date=datetime.fromisoformat(job["run_at"]),
            timezone="Europe/Berlin",
        )

    scheduler.add_job(
        run_booking_job,
        trigger=trigger,
        args=[job["id"]],
        id=job_apid,
        name=job["name"],
        replace_existing=True,
    )
    log.info("Scheduled job %d (%s): %s", job["id"], job["name"], trigger)


def remove_job_from_scheduler(job_id: int) -> None:
    job_apid = f"job_{job_id}"
    if scheduler.get_job(job_apid):
        scheduler.remove_job(job_apid)


def load_all_jobs() -> None:
    """Load all enabled jobs from DB into the scheduler."""
    for job in db.get_enabled_jobs():
        schedule_job(job)
    log.info("Loaded %d enabled jobs into scheduler.", len(db.get_enabled_jobs()))


def start() -> None:
    db.init_db()
    load_all_jobs()
    scheduler.start()
    log.info("Scheduler started.")


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    log.info("Scheduler shut down.")

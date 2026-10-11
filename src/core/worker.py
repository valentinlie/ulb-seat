"""The booking worker: execute one scheduled job, then exit.

A systemd timer (``ulb-book@<id>.timer``) fires the template service
``ulb-book@<id>.service``, which runs ``cli.py run-job <id>``, which calls
:func:`run_job`. Nothing stays resident between bookings.
"""

import logging
import time
from datetime import date, timedelta

from core import db
from core.booking import DAYS_AHEAD, execute_booking
from core.exceptions import BookingError, NotOpenYet

log = logging.getLogger(__name__)

RETRIES_UNTIL_OPEN = 30
RETRY_DELAY = 2  # seconds


def run_job(job_id: int) -> None:
    """Execute the booking for a single job. One-shot jobs disable themselves."""
    job = db.get_job(job_id)
    if not job:
        log.error("Job %d not found, skipping.", job_id)
        return
    if not job.enabled:
        log.info("Job %d is disabled, skipping.", job_id)
        return

    account = db.get_account(job.account_id) if job.account_id else None
    if not account:
        log.error("Job %d has no ULB account attached, skipping.", job_id)
        return

    if job.recurring:
        target_date = date.today() + timedelta(days=DAYS_AHEAD)
    elif job.target_date:
        target_date = job.target_date
    else:
        log.error("Job %d has no target date configured.", job_id)
        return

    log_id = db.log_booking_start(
        account_id=job.account_id,
        job_id=job_id,
        job_name=job.name,
        library_id=job.library_id,
        target_date=target_date,
        time_slot=job.time_slot,
        category=job.category,
    )

    try:
        # The timer fires right at opening time; if our clock is a little
        # ahead of the ULB's, the date is not open yet — keep trying a minute.
        for attempt in range(RETRIES_UNTIL_OPEN + 1):
            try:
                result = execute_booking(
                    account=account,
                    library_id=job.library_id,
                    date=target_date.strftime("%d.%m.%Y"),
                    time_slot=job.time_slot,
                    category=job.category,
                    preferred_section=job.preferred_section or "",
                    preferred_seats=job.preferred_seats,
                )
                break
            except NotOpenYet:
                if attempt == RETRIES_UNTIL_OPEN:
                    raise
                log.info("Date not open yet, retrying in %ds...", RETRY_DELAY)
                time.sleep(RETRY_DELAY)
        db.log_booking_finish(log_id, "success", result.get("seat_desc"), result.get("message"))
        log.info("Job %d (%s) succeeded: %s", job_id, job.name, result.get("message"))
    except BookingError as e:
        db.log_booking_finish(log_id, "failed", message=str(e))
        log.warning("Job %d (%s) failed: %s", job_id, job.name, e)
    except Exception as e:
        db.log_booking_finish(log_id, "error", message=str(e))
        log.exception("Job %d (%s) error:", job_id, job.name)

    # One-shot jobs run exactly once: disable and tear their timer down.
    if not job.recurring:
        db.disable_job(job_id)
        from core import systemd
        systemd.remove_job_timer(job_id)

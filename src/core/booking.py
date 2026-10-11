"""Shared booking orchestrator — used by the CLI, the worker and the web UI."""

import logging
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from core.auth import login
from core.reservation import CAT_SEAT, find_timeslot, reserve_seat, select_seat

log = logging.getLogger(__name__)

# When the ULB opens a date for booking — the same for every job.
DAYS_AHEAD = 3
OPENS_AT = time(9, 0)
TZ = ZoneInfo("Europe/Berlin")
POINTS_PER_HOUR = 60
POINTS_PER_WEEK = 3600  # a full points bar
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def opens_at(target: date) -> datetime:
    """The moment bookings for ``target`` open (09:00, three days before)."""
    return datetime.combine(target - timedelta(days=DAYS_AHEAD), OPENS_AT, tzinfo=TZ)


def planned_points(jobs, monday: date, now: datetime) -> int:
    """Points ``jobs`` would still spend in the week starting ``monday``.

    Only dates that have not opened yet count: an opened date was already
    booked (and is in the site's balance) or failed.
    """
    total = 0
    for job in jobs:
        start, end = (int(t.split(":")[0]) for t in job.time_slot.split("-"))
        # same fallback as the timers (core.systemd.trigger_days): no valid day means Mon-Fri
        days = {d.strip().lower() for d in (job.cron_days or "").split(",")} & set(WEEKDAYS) \
            or set(WEEKDAYS[:5])
        for day in (monday + timedelta(days=i) for i in range(7)):
            hit = WEEKDAYS[day.weekday()] in days if job.recurring else day == job.target_date
            if hit and opens_at(day) > now:
                total += (end - start) * POINTS_PER_HOUR
    return total


def ulb_session(account) -> requests.Session:
    """A requests session logged into the booking site as ``account``."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    login(session, account)
    return session


def execute_booking(account, library_id: int, date: str, time_slot: str,
                    category: int = CAT_SEAT, preferred_section: str = "",
                    preferred_seats: Sequence[int] = ()) -> dict:
    """Run a full booking flow for one ULB account (a ``db.Account``).

    ``preferred_seats`` (the job's seat or room numbers) are tried first, in
    order; with none of them free, a random free one is taken.

    Returns {"success": True, "seat_desc": "...", "message": "..."} on success.
    Raises BookingError on failure.
    """
    session = ulb_session(account)
    timeslot_href = find_timeslot(session, library_id, date, time_slot,
                                  category=category, preferred_section=preferred_section)
    seat_href, seat_desc = select_seat(session, timeslot_href,
                                       preferred=preferred_seats)
    details = reserve_seat(session, seat_href)
    return {
        "success": True,
        "seat_desc": seat_desc,
        "message": details,
    }

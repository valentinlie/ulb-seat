"""Shared booking orchestrator — used by both CLI and web."""

import logging
import time

import requests

from core.auth import login, handle_captcha
from core.reservation import find_timeslot, select_seat, reserve_seat

log = logging.getLogger(__name__)

RETRIES = 3
RETRY_DELAY = 5.0  # seconds between attempts


def execute_booking(library_id: int, date: str, time_slot: str,
                    group_room: bool = False, preferred_section: str = "") -> dict:
    """Run a full booking flow.

    Logs in once, then retries the booking up to RETRIES times with RETRY_DELAY
    seconds between attempts (e.g. if slots aren't open yet).

    Returns {"success": True, "seat_desc": "...", "message": "..."} on success.
    Raises BookingError on failure.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    # Login and captcha once — reused across all retry attempts
    html = login(session)
    handle_captcha(session, html)

    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            timeslot_href = find_timeslot(session, library_id, date, time_slot,
                                          group_room=group_room, preferred_section=preferred_section)
            seat_href, seat_desc = select_seat(session, timeslot_href, group_room=group_room)
            details = reserve_seat(session, seat_href)
            return {
                "success": True,
                "seat_desc": seat_desc,
                "message": details,
            }
        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                log.info("Attempt %d/%d failed: %s — retrying in %.0fs...", attempt, RETRIES, e, RETRY_DELAY)
                time.sleep(RETRY_DELAY)
            else:
                log.warning("All %d attempts failed: %s", RETRIES, e)

    raise last_error

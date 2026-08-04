"""Shared booking orchestrator — used by both CLI and web."""

import logging

import requests

from core.auth import login, handle_captcha
from core.reservation import find_timeslot, select_seat, reserve_seat

log = logging.getLogger(__name__)


def execute_booking(account, library_id: int, date: str, time_slot: str,
                    group_room: bool = False, preferred_section: str = "") -> dict:
    """Run a full booking flow for one ULB account (a ``db.Account``).

    Which seats or rooms are tried first comes from the account's own
    preferences, edited on the dashboard's settings page.

    Returns {"success": True, "seat_desc": "...", "message": "..."} on success.
    Raises BookingError on failure.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    html = login(session, account)
    handle_captcha(session, html, account.library_number)

    timeslot_href = find_timeslot(session, library_id, date, time_slot,
                                  group_room=group_room, preferred_section=preferred_section)
    preferred = account.preferred_group_rooms if group_room else account.preferred_seats
    seat_href, seat_desc = select_seat(session, timeslot_href, group_room=group_room,
                                       preferred=preferred or [])
    details = reserve_seat(session, seat_href)
    return {
        "success": True,
        "seat_desc": seat_desc,
        "message": details,
    }

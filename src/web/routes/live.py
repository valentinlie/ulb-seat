"""Live view: the ULB booking site, mirrored — book, cancel and swap right away.

Every page is fetched live from the booking site as the active account. The
site's links carry an ``onetime_token`` that dies with the next page load, so
our forms only carry plain ids (slot, seat, reservation); on submit we reload
the page that holds the link and follow its fresh copy.
"""

import logging
import re
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse

from config import BASE_URL, LIBRARIES
from core import db
from core.booking import DAYS_AHEAD, POINTS_PER_WEEK, planned_points, ulb_session
from core.exceptions import BookingError
from core.reservation import (
    CATEGORIES, booked_details, day_info, find_link, link_id, page_error, parse_account,
    parse_day, parse_overview, parse_seats, pick_seat, seat_number, success,
)
from web import ctx, templates
from web.auth import Auth, require_account

log = logging.getLogger(__name__)

router = APIRouter()

TIMEOUT = 20  # seconds per request to the booking site


def _get(session: requests.Session, query: str) -> str:
    return session.get(urljoin(BASE_URL, query), timeout=TIMEOUT).text


def _follow(session: requests.Session, page: str, mod: str, gone: str, **ids: int) -> str:
    """Load ``page``, then follow its ``?mod=<mod>`` link with these ids."""
    href = find_link(_get(session, page), mod, **ids)
    if href is None:
        raise BookingError(gone)
    return _get(session, href)


def _overview(request: Request, auth: Auth, notice: str = "", error: str = ""):
    account = days = None
    try:
        session = ulb_session(auth.account)
        account = parse_account(_get(session, "?mod=300"))
        days = parse_overview(_get(session, "?mod=005"))
    except (BookingError, requests.RequestException) as e:
        error = error or f"Could not reach the booking site: {e}"
    jobs = [j for j in db.get_all_jobs(auth.account.id) if j.enabled]
    return templates.TemplateResponse("dashboard.html", ctx(
        request, auth=auth, account=account, days=days, jobs=jobs, notice=notice, error=error,
        weeks=_weeks(account["weeks"] if account else [], jobs),
    ))


def _weeks(rows: list[list[str]], jobs: list[db.Job]) -> list[dict]:
    """The site's weekly balance, plus what the enabled jobs would still spend."""
    def pct(points: int) -> float:
        return max(0, min(points, POINTS_PER_WEEK)) * 100 / POINTS_PER_WEEK

    weeks = []
    for label, count, points in rows:
        m = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", label)  # the week's Monday
        left = int(re.sub(r"\D", "", points) or 0)  # "1.800" -> 1800
        planned = planned_points(jobs, date(int(m[3]), int(m[2]), int(m[1])), db.now()) if m else 0
        weeks.append({"label": label, "count": count, "left": left, "planned": planned,
                      "after": left - planned, "left_pct": pct(left), "after_pct": pct(left - planned)})
    return weeks


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, auth: Auth = Depends(require_account)):
    return _overview(request, auth)


@router.get("/book", response_class=HTMLResponse)
def book_day(request: Request, date_: str = Query("", alias="date"), cat: int = 1,
             start: str = "", end: str = "", auth: Auth = Depends(require_account)):
    today = date.today()
    days = [today + timedelta(days=i) for i in range(DAYS_AHEAD + 1)]
    day = next((d for d in days if d.isoformat() == date_), today)
    cat = cat if cat in CATEGORIES else 1

    sections, info, error = [], "", ""
    try:
        html = _get(ulb_session(auth.account), f"?mod=010&cat_id={cat}&date={day.isoformat()}")
        sections, info = parse_day(html), day_info(html)
    except (BookingError, requests.RequestException) as e:
        error = str(e)

    hidden = set(auth.user.hidden_libraries)
    hidden_count = len({s["library"] for s in sections if s["library"] in hidden})
    sections = [s for s in sections if s["library"] not in hidden]

    starts = sorted({s for sec in sections for s in sec["slots"]})
    ends = sorted({e for sec in sections for ends_ in sec["slots"].values() for e, _ in ends_})
    by_library: dict[str, list] = {}
    for sec in sections:
        href = next((h for e, h in sec["slots"].get(start, []) if e == end), "")
        sec["chosen"] = link_id(href, "locslot_id") if href else None
        sec["range"] = (min(sec["slots"]), max(e for ends_ in sec["slots"].values() for e, _ in ends_)) \
            if sec["slots"] else None
        by_library.setdefault(sec["library"], []).append(sec)

    return templates.TemplateResponse("book_day.html", ctx(
        request, auth=auth, days=days, day=day, cat=cat, start=start, end=end,
        starts=starts, ends=ends, by_library=by_library, info=info, error=error,
        hidden_count=hidden_count,
    ))


def _seat_page(request: Request, auth: Auth, html: str, action: str, locslot_id: int = 0):
    details, seats = parse_seats(html)
    category = next((cid for cid, name in CATEGORIES.items()
                     if details.get("Platzkategorie", [""])[0].startswith(name)), 1)
    # Seat preferences live on jobs; offer those of this account's jobs here.
    preferred = list(dict.fromkeys(n for j in db.get_all_jobs(auth.account.id)
                                   if j.category == category for n in j.preferred_seats))
    seats = [(link_id(href, "seat_id"), name, note) for href, name, note in seats]
    by_number = {seat_number(s[1]): s for s in seats}
    when = re.findall(r"\d\d:\d\d", details.get("Zeitslot", [""])[0])
    day = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", details.get("Datum", [""])[0])
    place = details.get("Bibliothek / Platzgruppe", [""])[0]
    return templates.TemplateResponse("book_seats.html", ctx(
        request, auth=auth, details=details, seats=seats, action=action, locslot_id=locslot_id,
        best=pick_seat(seats, preferred) if seats else None,
        preferred=[by_number[n] for n in preferred if n in by_number],
        # what the booking log needs, carried through the form
        library_id=next((i for i, n in LIBRARIES.items() if n == place.split(" / ")[0]), 0),
        target_date=f"{day.group(3)}-{day.group(2)}-{day.group(1)}" if day else "",
        time_slot="-".join(when[:2]), category=category,
    ))


@router.get("/book/slot/{locslot_id}", response_class=HTMLResponse)
def book_slot(request: Request, locslot_id: int, auth: Auth = Depends(require_account)):
    try:
        html = _get(ulb_session(auth.account), f"?mod=020&locslot_id={locslot_id}")
    except (BookingError, requests.RequestException) as e:
        return _overview(request, auth, error=str(e))
    return _seat_page(request, auth, html, action="/book/seat", locslot_id=locslot_id)


@router.post("/book/seat", response_class=HTMLResponse)
def book_seat(request: Request, locslot_id: int = Form(...), seat_id: int = Form(...),
              library_id: int = Form(0), target_date: str = Form(""), time_slot: str = Form(""),
              category: int = Form(1), auth: Auth = Depends(require_account)):
    try:
        day = date.fromisoformat(target_date)
    except ValueError:
        day = date.today()
    log_id = db.log_booking_start(
        account_id=auth.account.id, job_id=None, job_name=None, library_id=library_id,
        target_date=day, time_slot=time_slot, category=category, manual=True,
    )
    try:
        html = _follow(ulb_session(auth.account), f"?mod=020&locslot_id={locslot_id}", "030",
                       "That place was just taken — pick another one.", seat_id=seat_id)
    except (BookingError, requests.RequestException) as e:
        db.log_booking_finish(log_id, "failed", message=str(e))
        return _overview(request, auth, error=str(e))
    if not success(html):
        error = page_error(html) or "Booking failed."
        db.log_booking_finish(log_id, "failed", message=error)
        return _overview(request, auth, error=error)
    details = booked_details(html)
    parts = details.split("; ")  # when; library / section / seat; ...
    seat = parts[1].rsplit(" / ", 1)[-1] if len(parts) > 1 else None
    db.log_booking_finish(log_id, "success", seat, details)
    return _overview(request, auth, notice=f"Booked: {details}")


@router.post("/reservations/{res_id}/cancel", response_class=HTMLResponse)
def cancel(request: Request, res_id: int, auth: Auth = Depends(require_account)):
    return _act(request, auth, "Reservation cancelled.", lambda s: _follow(
        s, "?mod=300", "100", "That reservation can no longer be cancelled.", res_id=res_id))


@router.get("/reservations/{res_id}/swap", response_class=HTMLResponse)
def swap_choose(request: Request, res_id: int, auth: Auth = Depends(require_account)):
    try:
        html = _follow(ulb_session(auth.account), "?mod=300", "200",
                       "That reservation can no longer be swapped.", res_id=res_id)
    except (BookingError, requests.RequestException) as e:
        return _overview(request, auth, error=str(e))
    return _seat_page(request, auth, html, action=f"/reservations/{res_id}/swap")


@router.post("/reservations/{res_id}/swap", response_class=HTMLResponse)
def swap(request: Request, res_id: int, seat_id: int = Form(...),
         auth: Auth = Depends(require_account)):
    def go(session):
        swap_page = _follow(session, "?mod=300", "200",
                            "That reservation can no longer be swapped.", res_id=res_id)
        href = find_link(swap_page, "210", seat_id=seat_id)
        if href is None:
            raise BookingError("That place was just taken — pick another one.")
        return _get(session, href)
    return _act(request, auth, "Seat swapped.", go)


def _act(request: Request, auth: Auth, done: str, action):
    """Run a cancel/swap against a fresh session, then show the overview."""
    try:
        html = action(ulb_session(auth.account))
    except (BookingError, requests.RequestException) as e:
        return _overview(request, auth, error=str(e))
    if success(html):
        return _overview(request, auth, notice=f"{done} {booked_details(html)}".strip())
    return _overview(request, auth, error=page_error(html) or "The booking site refused.")

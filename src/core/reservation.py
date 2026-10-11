"""The ULB booking pages: parsers, and the steps of one booking.

The booking site (reworked by the ULB in October 2026):

- ``?mod=005`` overview: per day, the categories with their occupancy.
- ``?mod=010&cat_id=<1|3|4>&date=YYYY-MM-DD`` lists, per section (an ``<h2>``
  "Library / seat group"), a table of rows "von <start> bis" with one link per
  possible end time, pointing at ``?mod=020&locslot_id=N``.
- ``?mod=020&locslot_id=N`` details of that slot plus the free seats as
  ``?mod=030&...&seat_id=`` links; following one books it straight away.
- ``?mod=300`` account: weekly points, reservations with cancel (``mod=100``)
  and swap (``mod=200`` -> seat links ``mod=210``) links.
"""

import logging
import random
import re
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString

from config import BASE_URL, LIBRARIES
from core.exceptions import BookingError, NotOpenYet

log = logging.getLogger(__name__)

# cat_id on the booking site -> what it is called there
CATEGORIES = {
    1: "Einzelarbeitsplätze",
    3: "Gruppenarbeitstische",
    4: "Gruppenarbeitsräume",
}
CAT_SEAT = 1


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("main") or soup


def page_error(page: BeautifulSoup | str) -> str | None:
    """The site's own "Fehler: ..." message, if the page shows one."""
    node = (_soup(page) if isinstance(page, str) else page).find(string=re.compile("Fehler:"))
    if node is None:
        return None
    return (node.find_parent("p") or node.parent).get_text(" ", strip=True)


def _hhmm(value: str) -> str:
    """Normalise "8:00" to "08:00"."""
    return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")


def _lines(tag) -> list[str]:
    """A cell's text, one entry per visual line (the site separates them with <br>)."""
    for br in tag.find_all("br"):
        br.replace_with("\n")
    text = tag.get_text("").replace("\xa0", " ")
    return [line.strip() for line in text.splitlines() if line.strip()]


# ── Parsers ──────────────────────────────────────────────────────────────────

def parse_overview(html: str) -> list[dict]:
    """``?mod=005``: per day its categories, or the notice when it is not open yet."""
    days = []
    for h2 in _soup(html).find_all("h2"):
        day = {"title": h2.get_text(strip=True), "categories": [], "notice": ""}
        name = None
        for el in h2.find_next_siblings():
            if el.name == "h2":
                break
            if el.name != "p":
                continue
            text = el.get_text(" ", strip=True)
            if el.strong and text == el.strong.get_text(" ", strip=True):
                name = text  # category heading, its details follow in the next <p>
                continue
            if name is None:
                day["notice"] = f"{day['notice']} {text}".strip()
                continue
            link = el.find("a", href=re.compile(r"mod=010"))
            query = parse_qs(urlparse(link["href"]).query) if link else {}
            load = re.search(r"\w+ Auslastung \([\d.,]+ ?%\)", text)
            day["categories"].append({
                "name": name,
                "cat": int(query["cat_id"][0]) if link else None,
                "date": query["date"][0] if link else None,
                "load": load.group(0) if load else "",
                "last_minute": "Last-Minute" in text,
            })
            name = None
        days.append(day)
    return days


def parse_day(html: str) -> list[dict]:
    """``?mod=010``: the sections of one day and category.

    Each section is ``{"name", "library", "status", "slots"}`` where ``slots``
    maps a start time to ``[(end, href), ...]`` and ``status`` is the site's
    note (e.g. "Ausgebucht") for a section with nothing bookable.
    """
    main = _soup(html)
    if error := page_error(main):
        raise NotOpenYet(error) if "unzulässig" in error else BookingError(error)
    sections = []
    for h2 in main.find_all("h2"):
        name = h2.get_text(strip=True)
        section = {"name": name, "library": name.split(" / ")[0], "status": "", "slots": {}}
        body = h2.find_next_sibling(["table", "p", "h2"])
        if body is not None and body.name == "table":
            for row in body.find_all("tr"):
                start = row.find("strong")
                if start:
                    section["slots"][start.get_text(strip=True)] = [
                        (a.get_text(strip=True), a["href"]) for a in row.find_all("a", href=True)
                    ]
        elif body is not None and body.name == "p":
            section["status"] = body.get_text(" ", strip=True)
        sections.append(section)
    return sections


def day_info(html: str) -> str:
    """What a day page says about you: reservations you already have that day,
    and, when it offers no section at all, the reason why."""
    main = _soup(html)
    texts = [p.get_text(" ", strip=True) for p in main.find_all("p", class_="box")]
    if not main.find("h2"):
        texts += [p.get_text(" ", strip=True) for p in main.find_all("p", class_=False)]
    return " ".join(texts)


def parse_seats(html: str) -> tuple[dict[str, list[str]], list[tuple[str, str, str]]]:
    """``?mod=020`` (booking) or ``?mod=200`` (swap) -> (details, seats).

    ``details`` maps each row label to its lines of text (external links such
    as the seat map are kept as ``("Sitzplan", url)`` under ``"links"``).
    ``seats`` holds ``(href, name, note)``; group rooms come with a note like
    "Arbeitskabine für max. 8 Personen".
    """
    main = _soup(html)
    seat_link = re.compile(r"^\?mod=(030|210)&")
    seats = []
    for a in main.find_all("a", href=seat_link):
        note = a.next_sibling if type(a.next_sibling) is NavigableString else ""  # not a Comment
        seats.append((a["href"], a.get_text(strip=True), note.strip().strip("()")))
    details = {"links": [(a.get_text(strip=True), a["href"])
                         for a in main.find_all("a", href=re.compile(r"^https?://"), target=True)]}
    for row in main.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) == 2 and not cells[1].find("a", href=seat_link):
            details[cells[0].get_text(strip=True)] = [
                line for line in _lines(cells[1]) if not line.startswith("➜")]
    if not seats and (error := page_error(main)):
        details["Fehler"] = [error]
    return details, seats


def parse_account(html: str) -> dict:
    """``?mod=300``: weekly balance and the reservations, per section."""
    main = _soup(html)
    account = {"weeks": [], "sections": []}
    for h2 in main.find_all("h2"):
        title = h2.get_text(strip=True)
        body = h2.find_next_sibling(["table", "p", "h2"])
        rows = body.find("tbody").find_all("tr") if body is not None and body.name == "table" else []
        if title == "Guthaben":
            for row in rows:
                cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                if len(cells) == 3:
                    account["weeks"].append(cells)
            continue
        reservations = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            links = {a.get_text(strip=True): a["href"] for a in cells[1].find_all("a", href=True)}
            strong = cells[1].find("strong")
            reservations.append({
                "id": next((link_id(h, "res_id") for h in links.values() if "res_id=" in h), None),
                "when": strong.get_text("", strip=True) if strong else "",
                "lines": [line for line in _lines(cells[1])[1:] if line not in links],
                "cancel": next((h for h in links.values() if h.startswith("?mod=100&")), None),
                "swap": next((h for h in links.values() if h.startswith("?mod=200&")), None),
            })
        account["sections"].append({"title": title, "reservations": reservations})
    return account


def link_id(href: str, key: str) -> int | None:
    """An id from a booking-site link, e.g. ``seat_id`` or ``res_id``."""
    values = parse_qs(urlparse(href).query).get(key)
    return int(values[0]) if values and values[0].isdigit() else None


def find_link(html: str, mod: str, **ids: int) -> str | None:
    """The ``?mod=<mod>`` link on this page carrying these ids.

    The site's ``onetime_token`` only survives until the next page load, so a
    link must be followed straight from a freshly fetched page — callers keep
    ids, not links.
    """
    for a in _soup(html).find_all("a", href=re.compile(rf"^\?mod={mod}&")):
        if all(link_id(a["href"], key) == value for key, value in ids.items()):
            return a["href"]
    return None


def success(html: str) -> bool:
    """Booking, cancelling and swapping all answer with an "Erfolg:" message."""
    return "Erfolg:" in html


def booked_details(html: str) -> str:
    """The highlighted (just changed) reservation on a success page, as one line."""
    row = _soup(html).find("tr", style=lambda s: s and "yellow" in s)
    cells = row.find_all("td") if row else []
    lines = _lines(cells[1]) if len(cells) > 1 else []
    return "; ".join(
        line for line in lines
        if not line.startswith(("Reservierung stornieren", "Platz umtauschen", "Anzahl freier"))
    )


# ── One booking, step by step ────────────────────────────────────────────────

def find_timeslot(
    session: requests.Session, library_id: int, target_date: str, target_time: str,
    category: int = CAT_SEAT, preferred_section: str = "",
) -> str:
    """Find the matching time slot. Returns its relative URL (``?mod=020&...``).

    ``target_date`` is DD.MM.YYYY, ``target_time`` "HH:MM-HH:MM" (whole hours).
    When preferred_section is set (e.g. "Hauptlesesaal"), sections whose
    heading contains that keyword are tried first.
    """
    kind = CATEGORIES[category]
    library = LIBRARIES[library_id]
    log.info("[2/4] Looking for %s slot: %s %s at %s...", kind, target_date, target_time, library)

    iso_date = datetime.strptime(target_date, "%d.%m.%Y").date().isoformat()
    start, end = (_hhmm(t) for t in target_time.split("-"))
    html = session.get(f"{BASE_URL}?mod=010&cat_id={category}&date={iso_date}").text
    sections = [s for s in parse_day(html) if s["library"] == library]

    matching = [(s["name"], href) for s in sections
                for e, href in s["slots"].get(start, []) if e == end]
    if matching:
        if preferred_section:
            preferred = [m for m in matching if preferred_section.lower() in m[0].lower()]
            if preferred:
                log.info("  Found slot in preferred section '%s'.", preferred[0][0])
                return preferred[0][1]
            log.info("  Preferred section '%s' not found, falling back.", preferred_section)
        log.info("  Found slot in '%s'.", matching[0][0])
        return matching[0][1]

    if sections:
        seen = [
            f"{s['name']}: free between {min(s['slots'])} and "
            f"{max(e for ends in s['slots'].values() for e, _ in ends)}"
            if s["slots"] else f"{s['name']}: {s['status'] or '-'}"
            for s in sections
        ]
        raise BookingError(f"No {kind} slot {start}-{end} at {library} on {target_date}. "
                           + "; ".join(seen))
    raise BookingError(f"No bookable {kind} at {library} on {target_date}. {day_info(html)}".strip())


def select_seat(session: requests.Session, timeslot_href: str,
                preferred: Sequence[int] = ()) -> tuple[str, str]:
    """Select a seat/room. Returns (seat_href, seat_description).

    ``preferred`` holds seat/room numbers to try in order (the job's seat
    preferences), matched against the number a name ends with: 302 for
    "Platz 302", 2 for "Kabine G2", 18 for "Raum 018". The first available one
    wins, otherwise a random free seat does.
    """
    log.info("[3/4] Fetching available seats...")
    details, seats = parse_seats(session.get(urljoin(BASE_URL, timeslot_href)).text)
    if not seats:
        raise BookingError(" ".join(details.get("Fehler", ["No free seats left in this slot."])))
    log.info("  %d available.", len(seats))

    href, name, _ = pick_seat(seats, preferred)
    log.info("  Selected: %s", name)
    return href, name


def seat_number(name: str) -> int | None:
    """The number a seat name ends with: 302 for "Platz 302", 2 for "Kabine G2"."""
    m = re.search(r"(\d+)\s*$", name)
    return int(m.group(1)) if m else None


def pick_seat(seats: list[tuple[str, str, str]], preferred: Sequence[int]) -> tuple[str, str, str]:
    """The first preferred seat that is free, else a random free one."""
    by_number = {}
    for seat in seats:
        by_number.setdefault(seat_number(seat[1]), seat)
    return next((by_number[n] for n in preferred if n in by_number), None) or random.choice(seats)


def reserve_seat(session: requests.Session, seat_href: str) -> str:
    """Reserve the selected seat. Returns reservation details on success."""
    log.info("[4/4] Reserving seat...")
    html = session.get(urljoin(BASE_URL, seat_href)).text
    if not success(html):
        raise BookingError(page_error(html) or "Reservation failed.")
    details = booked_details(html)
    log.info("  Reservation successful: %s", details)
    return details

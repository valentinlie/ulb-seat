"""Reservation logic: find timeslots, select seats, reserve."""

import logging
import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import BASE_URL, GROUP_ROOM_SECTION_KEYWORD, PREFERRED_GROUP_ROOMS, PREFERRED_SEATS
from core.exceptions import BookingError

log = logging.getLogger(__name__)


def find_timeslot(
    session: requests.Session, library_id: int, target_date: str, target_time: str,
    group_room: bool = False, preferred_section: str = "",
) -> str:
    """Find the matching time slot. Returns the relative URL (with onetime_token).

    When preferred_section is set (e.g. "Hauptlesesaal"), slots whose <h2>
    section heading contains that keyword are tried first.
    """
    slot_type = "group room" if group_room else "seat"
    log.info("[3/6] Looking for %s time slot: %s %s at library %d...", slot_type, target_date, target_time, library_id)

    url = f"{BASE_URL}?mod=190&library_id={library_id}"
    resp = session.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    date_ddmmyyyy = target_date
    log.info("  Looking for date %s and time %s...", date_ddmmyyyy, target_time)
    start_time, end_time = target_time.split("-")
    target_pattern = f"{start_time}\u2013{end_time}"  # en-dash

    # Collect all slots for the target date
    available_slots = []  # (slot_time, slot_id, section, href) — all date matches
    matching_slots = []   # same tuple — date AND time matches
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "reservationtimeslot_id=" not in href:
            continue

        row = link.find_parent("tr")
        if not row:
            continue

        row_text = row.get_text()

        if date_ddmmyyyy not in row_text:
            continue

        time_match = re.search(r"(\d{2}:\d{2})\u2013(\d{2}:\d{2})", row_text)
        qs = parse_qs(urlparse(href).query)
        slot_id = int(qs["reservationtimeslot_id"][0])

        # Filter by section type (group room vs individual)
        section_h2 = row.find_previous("h2")
        section = section_h2.get_text().strip() if section_h2 else ""
        is_group_section = GROUP_ROOM_SECTION_KEYWORD.lower() in section.lower()
        if group_room != is_group_section:
            continue

        if time_match:
            slot_time = f"{time_match.group(1)}-{time_match.group(2)}"
            available_slots.append((slot_time, slot_id, section, href))

        if target_pattern in row_text:
            free_match = re.search(r"(\d+)", row.find_all("td")[1].get_text()) if len(row.find_all("td")) > 1 else None
            free_count = free_match.group(1) if free_match else "?"
            matching_slots.append((slot_id, section, href, free_count))

    if matching_slots:
        # Prefer section matching preferred_section keyword
        if preferred_section:
            pref_lower = preferred_section.lower()
            preferred = [s for s in matching_slots if pref_lower in s[1].lower()]
            if preferred:
                slot_id, section, href, free_count = preferred[0]
                log.info("  Found preferred section '%s' (ID=%d), %s free places.", section, slot_id, free_count)
                return href
            log.info("  Preferred section '%s' not found, falling back.", preferred_section)

        # Fall back to first match
        slot_id, section, href, free_count = matching_slots[0]
        log.info("  Found time slot in '%s' (ID=%d), %s free places.", section, slot_id, free_count)
        return href

    if available_slots:
        msg = f"No slot matching time '{target_time}' found for {date_ddmmyyyy}."
        slot_info = "; ".join(f"{t} (ID={sid}) {sec}" for t, sid, sec, _ in available_slots)
        raise BookingError(f"{msg} Available: {slot_info}")
    else:
        raise BookingError(f"No slots found for date {date_ddmmyyyy}. Check if reservations are available.")


def select_seat(session: requests.Session, timeslot_href: str, group_room: bool = False) -> tuple[str, str]:
    """Select the first available seat/room. Returns (seat_href, seat_description)."""
    slot_type = "group rooms" if group_room else "seats"
    log.info("[4/6] Fetching available %s...", slot_type)

    url = urljoin(BASE_URL, timeslot_href)
    resp = session.get(url)

    if "Reservierung möglich?" in resp.text and "Nein" in resp.text:
        raise BookingError("Reservation not possible for this time slot.")

    soup = BeautifulSoup(resp.text, "html.parser")
    seat_links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "seat_id=" in href:
            qs = parse_qs(urlparse(href).query)
            seat_id = int(qs["seat_id"][0])
            desc = link.get_text().strip()
            # Get extra info from the text after the link
            next_text = link.next_sibling
            if next_text and isinstance(next_text, str):
                desc += " " + next_text.strip().strip("()")
            seat_links.append((href, seat_id, desc))

    if not seat_links:
        raise BookingError(f"No available {slot_type} found.")

    log.info("  %d %s available.", len(seat_links), slot_type)

    # Try preferred seats/rooms first (match by number in description)
    preferred_list = PREFERRED_GROUP_ROOMS if group_room else PREFERRED_SEATS
    seat_by_number = {}
    for href, sid, desc in seat_links:
        m = re.search(r"(?:Platz|Kabine|Raum)\s+(\d+)", desc)
        if m:
            seat_by_number[int(m.group(1))] = (href, sid, desc)

    for preferred in preferred_list:
        if preferred in seat_by_number:
            seat_href, seat_id, desc = seat_by_number[preferred]
            log.info("  Selected preferred %s: %s (ID=%d)", slot_type.rstrip('s'), desc, seat_id)
            return seat_href, desc

    # Fallback to first available
    seat_href, seat_id, desc = seat_links[0]
    if preferred_list:
        log.info("  Preferred %s not available, falling back to: %s (ID=%d)", preferred_list, desc, seat_id)
    else:
        log.info("  Selected: %s (ID=%d)", desc, seat_id)
    return seat_href, desc


def reserve_seat(session: requests.Session, seat_href: str) -> str:
    """Reserve the selected seat. Returns reservation details on success."""
    log.info("[5/6] Reserving seat...")

    url = urljoin(BASE_URL, seat_href)
    resp = session.get(url)

    if "Erfolg" in resp.text:
        log.info("  Reservation successful!")
        soup = BeautifulSoup(resp.text, "html.parser")
        details = []
        for tr in soup.find_all("tr", style=lambda s: s and "yellow" in s):
            td = tr.find_all("td")
            if len(td) >= 2:
                lines = [l.strip() for l in td[1].get_text(separator="\n").split("\n") if l.strip()]
                for line in lines:
                    if line in ("Platz-Umtausch versuchen", "Reservierung jetzt stornieren"):
                        continue
                    if "Platz-Umtausch möglich" in line or "Stornierung möglich" in line:
                        continue
                    details.append(line)
            break
        detail_str = "; ".join(details)
        log.info("[6/6] Reservation details: %s", detail_str)
        return detail_str

    msg = "Reservation failed."
    if "bereits" in resp.text.lower():
        msg += " You may already have a reservation for this time."
    raise BookingError(msg)

"""Reservation logic: find timeslots, select seats, reserve."""

import re
import sys
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import BASE_URL, GROUP_ROOM_SECTION_KEYWORD, PREFERRED_GROUP_ROOMS, PREFERRED_SEATS


def find_timeslot(
    session: requests.Session, library_id: int, target_date: str, target_time: str,
    group_room: bool = False,
) -> str:
    """Find the matching time slot. Returns the relative URL (with onetime_token)."""
    slot_type = "group room" if group_room else "seat"
    print(f"[3/6] Looking for {slot_type} time slot: {target_date} {target_time} at library {library_id}...")

    url = f"{BASE_URL}?mod=190&library_id={library_id}"
    resp = session.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    # date format dd.mm.yyyy
    date_ddmmyyyy = target_date
    print(f"  Looking for date {date_ddmmyyyy} and time {target_time}...")
    start_time, end_time = target_time.split("-")
    target_pattern = f"{start_time}\u2013{end_time}"  # en-dash

    # The page uses tables with rows containing "Mo, 09.02.2026, 08:00–12:00 Uhr"
    available_slots = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "reservationtimeslot_id=" not in href:
            continue

        # Get the table row containing this link
        row = link.find_parent("tr")
        if not row:
            continue

        row_text = row.get_text()

        # Check date match (DD.MM.YYYY)
        if date_ddmmyyyy not in row_text:
            continue

        # Collect all matching-date slots for display
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
            available_slots.append((slot_time, slot_id, section))

        # Check time match
        if target_pattern in row_text:
            free_match = re.search(r"(\d+)", row.find_all("td")[1].get_text()) if len(row.find_all("td")) > 1 else None
            free_count = free_match.group(1) if free_match else "?"
            print(f"  Found time slot (ID={slot_id}), {free_count} free places.")
            return href

    if available_slots:
        print(f"\n  No slot matching time '{target_time}' found for {date_ddmmyyyy}.")
        print("  Available slots for this date:")
        for slot_time, slot_id, section in available_slots:
            print(f"    {slot_time}  (ID={slot_id})  {section}")
    else:
        print(f"\n  No slots found for date {date_ddmmyyyy}.")
        print("  Check if reservations are available for this date.")
    sys.exit(1)


def select_seat(session: requests.Session, timeslot_href: str, group_room: bool = False) -> tuple[str, str]:
    """Select the first available seat/room. Returns (seat_href, seat_description)."""
    slot_type = "group rooms" if group_room else "seats"
    print(f"[4/6] Fetching available {slot_type}...")

    url = urljoin(BASE_URL, timeslot_href)
    resp = session.get(url)

    if "Reservierung möglich?" in resp.text and "Nein" in resp.text:
        print("ERROR: Reservation not possible for this time slot.")
        sys.exit(1)

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
        print(f"ERROR: No available {slot_type} found.")
        sys.exit(1)

    print(f"  {len(seat_links)} {slot_type} available.")

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
            print(f"  Selected preferred {slot_type.rstrip('s')}: {desc} (ID={seat_id})")
            return seat_href, desc

    # Fallback to first available
    seat_href, seat_id, desc = seat_links[0]
    if preferred_list:
        print(f"  Preferred {preferred_list} not available, falling back to: {desc} (ID={seat_id})")
    else:
        print(f"  Selected: {desc} (ID={seat_id})")
    return seat_href, desc


def reserve_seat(session: requests.Session, seat_href: str) -> bool:
    """Reserve the selected seat. Returns True on success."""
    print(f"[5/6] Reserving seat...")

    url = urljoin(BASE_URL, seat_href)
    resp = session.get(url)

    if "Erfolg" in resp.text:
        print("  Reservation successful!")
        soup = BeautifulSoup(resp.text, "html.parser")
        for tr in soup.find_all("tr", style=lambda s: s and "yellow" in s):
            td = tr.find_all("td")
            if len(td) >= 2:
                lines = [l.strip() for l in td[1].get_text(separator="\n").split("\n") if l.strip()]
                print("\n[6/6] Reservation details:")
                for line in lines:
                    if line in ("Platz-Umtausch versuchen", "Reservierung jetzt stornieren"):
                        continue
                    if "Platz-Umtausch möglich" in line or "Stornierung möglich" in line:
                        continue
                    print(f"  {line}")
            break
        return True

    print("ERROR: Reservation failed.")
    if "bereits" in resp.text.lower():
        print("  You may already have a reservation for this time.")
    print("  Response snippet:", resp.text[:500])
    return False

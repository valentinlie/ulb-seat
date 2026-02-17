#!/usr/bin/env python3
"""ULB Münster library seat reservation bot."""

import argparse
import sys
import time
from datetime import datetime, timedelta

import requests

from config import LIBRARIES
from auth import login, handle_captcha
from reservation import find_timeslot, select_seat, reserve_seat

def main():
    parser = argparse.ArgumentParser(
        description="ULB Münster library seat reservation bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            f"  {kid:>3}: {name}" for kid, name in sorted(LIBRARIES.items())
        ),
    )
    parser.add_argument(
        "--library",
        type=int,
        default=1,
        help="Library ID (default: 1 = Zentralbibliothek). See list below.",
    )
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        "--date",
        help="Reservation date (DD.MM.YYYY)",
    )
    date_group.add_argument(
        "--date-offset",
        type=int,
        help="Book for N days from today (e.g. 2 = day after tomorrow)",
    )
    parser.add_argument(
        "--time",
        required=True,
        help='Time slot (HH:MM-HH:MM, e.g. "08:00-12:00")',
    )
    parser.add_argument(
        "--group-room",
        action="store_true",
        help="Book a group room (Arbeitskabine) instead of an individual seat",
    )
    args = parser.parse_args()

    if args.date_offset is not None:
        args.date = (datetime.now() + timedelta(days=args.date_offset)).strftime("%d.%m.%Y")

    if args.library not in LIBRARIES:
        print(f"ERROR: Unknown library ID {args.library}.")
        print("Available libraries:")
        for kid, name in sorted(LIBRARIES.items()):
            print(f"  {kid}: {name}")
        sys.exit(1)

    booking_type = "group room" if args.group_room else "seat"
    print(f"Target: {LIBRARIES[args.library]} (ID={args.library}), type: {booking_type}")
    print(f"Date: {args.date}, Time: {args.time}")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    html = login(session)
    handle_captcha(session, html)
    timeslot_href = find_timeslot(session, args.library, args.date, args.time, group_room=args.group_room)
    seat_href, _ = select_seat(session, timeslot_href, group_room=args.group_room)
    success = reserve_seat(session, seat_href)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

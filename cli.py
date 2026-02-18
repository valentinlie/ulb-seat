#!/usr/bin/env python3
"""ULB Münster library seat reservation bot."""

import argparse
import logging
import sys
from datetime import datetime, timedelta

from config import LIBRARIES
from core.exceptions import BookingError
from core.booking import execute_booking


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

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
    parser.add_argument(
        "--section",
        default="",
        help='Preferred section keyword (e.g. "Hauptlesesaal"). Falls back to first available.',
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

    try:
        result = execute_booking(args.library, args.date, args.time,
                                 group_room=args.group_room, preferred_section=args.section)
        print(f"\nBooking successful: {result['message']}")
        sys.exit(0)
    except BookingError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

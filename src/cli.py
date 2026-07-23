#!/usr/bin/env python3
"""Unified CLI for the ULB seat bot.

Command groups:

- **booking**  book (ad-hoc one-off booking), run-job (fire a saved job — this
               is what the per-job systemd timer calls), jobs (list saved jobs)
- **worker**   web (serve the dashboard, optionally on a systemd socket)
- **service**  install / sync / enable / disable / status / logs — the
               systemd --user timers + socket-activated web UI
"""

import argparse
import logging
import subprocess
import sys
from datetime import date, timedelta

WEB_SOCKET = "ulb-web.socket"
WEB_SERVICE = "ulb-web.service"


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args]).returncode


# ── booking ───────────────────────────────────────────────────────────────────

def cmd_book(args: argparse.Namespace) -> int:
    """Book a seat right now, straight from the terminal (no saved job)."""
    from config import LIBRARIES
    from core.booking import execute_booking
    from core.exceptions import BookingError

    if args.date_offset is not None:
        args.date = (date.today() + timedelta(days=args.date_offset)).strftime("%d.%m.%Y")

    if args.library not in LIBRARIES:
        print(f"ERROR: Unknown library ID {args.library}.", file=sys.stderr)
        print("Available libraries:", file=sys.stderr)
        for kid, name in sorted(LIBRARIES.items()):
            print(f"  {kid}: {name}", file=sys.stderr)
        return 1

    booking_type = "group room" if args.group_room else "seat"
    print(f"Target: {LIBRARIES[args.library]} (ID={args.library}), type: {booking_type}")
    print(f"Date: {args.date}, Time: {args.time}")

    try:
        result = execute_booking(args.library, args.date, args.time,
                                 group_room=args.group_room, preferred_section=args.section)
        print(f"\nBooking successful: {result['message']}")
        return 0
    except BookingError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


def cmd_run_job(args: argparse.Namespace) -> int:
    """Execute a saved job by id. Invoked by ulb-book@<id>.service."""
    from core import db
    from core.worker import run_job

    db.init_db()
    try:
        run_job(args.id)
    finally:
        db.close_pool()
    return 0


def cmd_jobs(_: argparse.Namespace) -> int:
    from config import LIBRARIES
    from core import db

    db.init_db()
    jobs = db.get_all_jobs()
    if not jobs:
        print("No jobs.")
        return 0

    print(f"{'ID':>4}  {'ON':<3} {'TYPE':<9} {'LIBRARY':<10} {'TIME':<12} NAME")
    for job in jobs:
        on = "yes" if job.enabled else "-"
        kind = "recurring" if job.recurring else "one-shot"
        lib = str(job.library_id)
        print(f"{job.id:>4}  {on:<3} {kind:<9} {lib:<10} {job.time_slot:<12} {job.name}")
    return 0


# ── worker ────────────────────────────────────────────────────────────────────

def cmd_web(args: argparse.Namespace) -> int:
    """Serve the dashboard. With --fd it accepts a socket passed by systemd."""
    import os

    if args.fd is None:
        # Manual run: keep the server up (the socket-activated unit passes --fd).
        os.environ.setdefault("ULB_WEB_IDLE_TIMEOUT", "0")

    import uvicorn

    if args.fd is not None:
        uvicorn.run("web.app:app", fd=args.fd)
    else:
        from config import HOST, PORT
        uvicorn.run("web.app:app", host=HOST, port=PORT)
    return 0


# ── service ───────────────────────────────────────────────────────────────────

def cmd_install(_: argparse.Namespace) -> int:
    from core import db, systemd

    db.init_db()
    systemd.install()
    systemd.sync_all_jobs()
    print("Installed systemd --user units and synced job timers.")
    print("Enable the web UI with:  ulb enable")
    return 0


def cmd_sync(_: argparse.Namespace) -> int:
    from core import db, systemd

    db.init_db()
    systemd.install()
    systemd.sync_all_jobs()
    print("Re-synced units and job timers with the database.")
    return 0


def cmd_enable(_: argparse.Namespace) -> int:
    return _systemctl("enable", "--now", WEB_SOCKET)


def cmd_disable(_: argparse.Namespace) -> int:
    return _systemctl("disable", "--now", WEB_SOCKET)


def cmd_status(_: argparse.Namespace) -> int:
    _systemctl("list-timers", "ulb-book@*", "--all")
    return _systemctl("status", "--no-pager", WEB_SOCKET, WEB_SERVICE)


def cmd_logs(args: argparse.Namespace) -> int:
    cmd = ["journalctl", "--user", "-n", str(args.lines),
           "-u", WEB_SERVICE, "-u", "ulb-book@*"]
    if args.follow:
        cmd.append("-f")
    return subprocess.run(cmd).returncode


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(prog="ulb", description="ULB Münster seat reservation bot")
    sub = parser.add_subparsers(dest="command", required=True)

    # book: ad-hoc one-off booking
    from config import LIBRARIES
    p_book = sub.add_parser(
        "book", help="Book a seat now from the terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Libraries:\n" + "\n".join(
            f"  {kid:>3}: {name}" for kid, name in sorted(LIBRARIES.items())
        ),
    )
    p_book.add_argument("--library", type=int, default=1,
                        help="Library ID (default: 1 = Zentralbibliothek)")
    g = p_book.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="Reservation date (DD.MM.YYYY)")
    g.add_argument("--date-offset", type=int, help="Book for N days from today")
    p_book.add_argument("--time", required=True, help='Time slot (e.g. "08:00-12:00")')
    p_book.add_argument("--group-room", action="store_true",
                        help="Book a group room instead of a seat")
    p_book.add_argument("--section", default="",
                        help='Preferred section keyword (falls back to any available)')
    p_book.set_defaults(func=cmd_book)

    # run-job: fire a saved job (used by the timer)
    p_run = sub.add_parser("run-job", help="Execute a saved job by id")
    p_run.add_argument("id", type=int, help="Job ID (see `ulb jobs`)")
    p_run.set_defaults(func=cmd_run_job)

    sub.add_parser("jobs", help="List saved jobs").set_defaults(func=cmd_jobs)

    # web
    p_web = sub.add_parser("web", help="Serve the dashboard")
    p_web.add_argument("--fd", type=int, default=None,
                       help="Serve on a socket passed by systemd (fd 3)")
    p_web.set_defaults(func=cmd_web)

    # service management
    sub.add_parser("install", help="Write the systemd --user units and job timers").set_defaults(func=cmd_install)
    sub.add_parser("sync", help="Re-sync units and job timers with the DB").set_defaults(func=cmd_sync)
    sub.add_parser("enable", help="Enable + start the web socket").set_defaults(func=cmd_enable)
    sub.add_parser("disable", help="Disable + stop the web socket").set_defaults(func=cmd_disable)
    sub.add_parser("status", help="Show job timers and web-UI status").set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="Show service logs")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="Lines to show")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow the log")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

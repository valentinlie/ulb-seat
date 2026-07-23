"""Manage the systemd --user units: one booking timer per job + the web UI.

Scale-to-zero, same shape as the Zara checker:

- Each enabled job gets a ``ulb-book@<id>.timer`` whose ``OnCalendar`` is
  computed from the job's schedule. It fires the template service
  ``ulb-book@<id>.service`` (a ``oneshot`` running ``cli.py run-job <id>``),
  so nothing of ours runs until a booking is actually due.
- ``ulb-web.socket`` socket-activates ``ulb-web.service`` on the first HTTP
  hit; the web app shuts itself down again once idle (see ``web/app.py``).

The web routes and the CLI both call :func:`sync_job_timer` /
:func:`remove_job_timer` whenever a job changes, so the DB stays the single
source of truth and systemd is just a projection of it.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from config import PORT
from core import db

log = logging.getLogger(__name__)

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
PROJECT_DIR = Path(__file__).resolve().parents[2]  # src/core/systemd.py -> repo root
UV = shutil.which("uv") or "uv"

BOOK_TEMPLATE = "ulb-book@.service"
WEB_SOCKET = "ulb-web.socket"
WEB_SERVICE = "ulb-web.service"

# weekday name -> index (mon=0 … sun=6), and -> systemd's OnCalendar spelling
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES)}
SYSTEMD_DAY = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
               "fri": "Fri", "sat": "Sat", "sun": "Sun"}


def _systemctl(*args: str) -> int:
    try:
        return subprocess.run(["systemctl", "--user", *args]).returncode
    except FileNotFoundError:
        log.warning("systemctl not found; skipping: %s", " ".join(args))
        return 1


def _write(name: str, content: str) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIT_DIR / name).write_text(content)


def _timer_name(job_id: int) -> str:
    return f"ulb-book@{job_id}.timer"


def _target_days_to_trigger_days(target_days: str, date_offset: int) -> list[str]:
    """Days the user wants a seat → days the timer should fire (subtract offset).

    Example: target "mon", offset 3 → fire on "fri" (book 3 days ahead).
    """
    trigger = []
    for day in target_days.split(","):
        day = day.strip().lower()
        if day not in DAY_INDEX:
            continue
        trigger.append(DAY_NAMES[(DAY_INDEX[day] - date_offset) % 7])
    return trigger or ["mon", "tue", "wed", "thu", "fri"]


def _on_calendar(job: db.Job) -> str | None:
    """Build the systemd OnCalendar= value for a job, or None if unschedulable."""
    if job.recurring:
        days = _target_days_to_trigger_days(
            job.cron_days or "mon,tue,wed,thu,fri", job.date_offset or 0
        )
        day_spec = ",".join(SYSTEMD_DAY[d] for d in days)
        hour = job.cron_hour or 0
        minute = job.cron_minute or 0
        return f"{day_spec} *-*-* {hour:02d}:{minute:02d}:00"
    if job.run_at:
        return job.run_at.strftime("%Y-%m-%d %H:%M:00")
    return None


def sync_job_timer(job: db.Job | None) -> None:
    """Create/update the timer for a job to match the DB, or remove it."""
    if job is None:
        return
    if not job.enabled:
        remove_job_timer(job.id)
        return

    on_calendar = _on_calendar(job)
    if not on_calendar:
        log.warning("Job %d has no schedule; removing any timer.", job.id)
        remove_job_timer(job.id)
        return

    # Recurring timers catch up if the machine was asleep at fire time; a
    # one-shot booking for a fixed date should not fire late.
    persistent = "true" if job.recurring else "false"
    _write(_timer_name(job.id), f"""[Unit]
Description=ULB booking job {job.id} ({job.name})

[Timer]
OnCalendar={on_calendar}
AccuracySec=1s
Persistent={persistent}
Unit=ulb-book@{job.id}.service

[Install]
WantedBy=timers.target
""")
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", _timer_name(job.id))
    log.info("Synced timer for job %d: OnCalendar=%s", job.id, on_calendar)


def remove_job_timer(job_id: int) -> None:
    name = _timer_name(job_id)
    _systemctl("disable", "--now", name)
    path = UNIT_DIR / name
    if path.exists():
        path.unlink()
    _systemctl("daemon-reload")


def sync_all_jobs() -> None:
    """Reconcile every job's timer with the DB (used by `install` / `sync`)."""
    for job in db.get_all_jobs():
        sync_job_timer(job)


def install() -> None:
    """Write the shared units (booking template + web UI) and reload systemd."""
    _write(BOOK_TEMPLATE, f"""[Unit]
Description=ULB booking worker for job %i

[Service]
Type=oneshot
WorkingDirectory={PROJECT_DIR}
ExecStart={UV} run python src/cli.py run-job %i
""")
    _write(WEB_SOCKET, f"""[Unit]
Description=ULB seat web UI socket (on-demand)

[Socket]
ListenStream={PORT}

[Install]
WantedBy=sockets.target
""")
    _write(WEB_SERVICE, f"""[Unit]
Description=ULB seat web UI
Requires={WEB_SOCKET}
After={WEB_SOCKET}

[Service]
WorkingDirectory={PROJECT_DIR}
ExecStart={UV} run python src/cli.py web --fd 3
# The idle watchdog stops the UI with SIGTERM (exit 143) — treat that as clean.
SuccessExitStatus=143
""")
    _systemctl("daemon-reload")

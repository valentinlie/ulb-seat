# ULB Seat Reservation Bot

Automated library seat reservation for [ULB Münster](https://www.ulb.uni-muenster.de/).
Schedule recurring or one-shot bookings through a web dashboard, or run a quick booking from the command line.

## Features

- **Web dashboard** -- manage jobs, view booking history, trigger manual runs
- **Recurring jobs** -- book seats automatically on specific days of the week
- **One-shot jobs** -- schedule a single booking for a specific date
- **CLI** -- book a seat directly from the terminal
- **Captcha solving** -- OCR-based (Tesseract) automatic captcha handling
- **Seat preferences** -- tries your preferred seats first, falls back to any available

## Requirements

- Python 3.13+
- PostgreSQL
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (system package)
- [uv](https://docs.astral.sh/uv/) (package manager)

Install Tesseract on your system:

```bash
# Debian / Ubuntu
sudo apt install tesseract-ocr

# Arch
sudo pacman -S tesseract

# macOS
brew install tesseract
```

## Setup

### 1. Install Python dependencies

```bash
uv sync
```

### 2. Create the PostgreSQL database

```bash
createdb ulb_seat
```

Tables are created automatically on first startup.

### 3. Create `config.py`

Copy the example below and fill in your credentials. This file is git-ignored.

```python
"""Configuration for ULB seat reservation."""

# ── SSO credentials (Uni Münster) ────────────────────────────────────────────
SSO_USERNAME = "your_sso_username"
SSO_PASSWORD = "your_sso_password"
LIBRARY_NUMBER = "A12345678/X"  # your library card number

MAX_CAPTCHA_RETRIES = 5

BASE_URL = "https://sso.uni-muenster.de/ULB/sso/wwu/platzreservierung/"

# ── Libraries ────────────────────────────────────────────────────────────────
LIBRARIES = {
    1:   "Zentralbibliothek",
    18:  "Rechtswissenschaftliches Seminar I (RWS I)",
    19:  "RWS II (Kriminalwissenschaften)",
    22:  "Wirtschaftswissenschaften (Forum Oeconomicum)",
    37:  "Medizin-Bibliothek",
    38:  "Erziehungswissenschaft & Kommunikationswissenschaft",
    41:  "Zweigbibliothek Sozialwissenschaften",
    42:  "Psychologie",
    45:  "Bibliotheken im Fürstenberghaus",
    104: "Bibliotheken im Philosophikum",
    105: "Vom-Stein-Haus (Germanistik)",
}

# ── Seat preferences (tried in order, fallback to any available) ─────────────
PREFERRED_SEATS = []         # e.g. [600, 6001]
PREFERRED_GROUP_ROOMS = []   # e.g. [3, 4]

# ── PostgreSQL ───────────────────────────────────────────────────────────────
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "ulb_seat"
DB_USER = "your_db_user"
DB_PASS = "your_db_password"

# ── Dashboard credentials (HTTP Basic Auth) ──────────────────────────────────
DASHBOARD_USER = "admin"
DASHBOARD_PASS = "change_me"

# ── Web server ───────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8000
```

## Usage

### Web dashboard

Start the server:

```bash
uv run src/main.py
# or, directly:
uv run uvicorn web.app:app --app-dir src
```

Open `http://localhost:8000` and log in with your `DASHBOARD_USER` / `DASHBOARD_PASS` credentials.

From the dashboard you can:

- **Create jobs** -- go to Jobs > New Job
- **Toggle jobs** on/off
- **Run a job immediately** with the "Run Now" button
- **View booking history** with status and error messages

#### Job types

**Recurring** -- runs on a cron schedule. You specify:

- Which days you want a seat (e.g. Mon-Fri)
- How many days in advance to book (offset)
- What time to trigger the booking

Example: to always have a seat on weekday mornings, set days to `mon,tue,wed,thu,fri`, offset to `3`, and trigger time to `00:01`. The scheduler calculates the correct trigger day automatically.

**One-shot** -- runs once at a specific date and time, then disables itself. You specify:

- The target date (when you want the seat)
- The trigger date and time (when to attempt the booking)

### CLI

The `ulb` CLI (`uv run python src/cli.py <command>`) has three command groups:
booking, the web UI, and service management. For quick one-off bookings without
the web interface, use `book`:

```bash
# Book a seat 3 days from now, morning slot
uv run python src/cli.py book --date-offset 3 --time "08:00-12:00"

# Book at a specific library on a specific date
uv run python src/cli.py book --library 22 --date "20.03.2026" --time "12:00-16:00"

# Book a group room
uv run python src/cli.py book --date-offset 2 --time "08:00-12:00" --group-room

# Prefer a specific section
uv run python src/cli.py book --date-offset 3 --time "08:00-12:00" --section "Hauptlesesaal"
```

Other commands: `jobs` (list saved jobs), `run-job <id>` (fire a saved job — the
timer uses this), and the service commands below. Available libraries are listed
with `uv run python src/cli.py book --help`.

## Deployment (systemd, scale-to-zero)

Scheduling and serving are handled by **systemd --user units**, so nothing of
ours stays resident when idle:

- each enabled job becomes a `ulb-book@<id>.timer` that fires a short-lived
  worker at booking time (`OnCalendar` computed from the job's schedule);
- the dashboard is **socket-activated** — `ulb-web.socket` starts the web app on
  the first request, and the app shuts itself down again after 10 min idle
  (`ULB_WEB_IDLE_TIMEOUT` seconds).

```bash
uv run python src/cli.py install   # write the units + a timer per saved job
uv run python src/cli.py enable     # enable + start the web socket
uv run python src/cli.py status     # list job timers + web-UI status
uv run python src/cli.py logs -f    # tail worker + web logs
```

Creating, editing, toggling, or deleting a job through the dashboard (or a
`run-job` on a one-shot) re-syncs its timer automatically. Run `sync` after
pulling code changes to rewrite the units. The units run `uv run` from the repo
directory, so keep the checkout in place.

> **Enable lingering** so timers fire even when you are not logged in — bookings
> at 00:01 depend on it:
>
> ```bash
> sudo loginctl enable-linger "$USER"
> ```

## Project structure

```
ulb-seat/
├── config.py                   # Credentials and settings (git-ignored)
├── pyproject.toml
│
└── src/
    ├── main.py                 # Web entry point (uvicorn)
    ├── cli.py                  # CLI entry point
    │
    ├── core/
    │   ├── auth.py             # SSO login and captcha flow
    │   ├── booking.py          # Booking orchestration
    │   ├── captcha.py          # Tesseract OCR captcha solver
    │   ├── db.py               # PostgreSQL access (jobs + booking log)
    │   ├── exceptions.py       # BookingError
    │   ├── reservation.py      # Timeslot search, seat selection, reservation
    │   ├── worker.py           # Execute one scheduled job, then exit
    │   └── systemd.py          # Generate per-job timers + web units
    │
    └── web/
        ├── app.py              # FastAPI app (socket-activated, idle-shutdown)
        ├── auth.py             # HTTP Basic Auth
        ├── routes/
        │   ├── dashboard.py    # GET /
        │   ├── jobs.py         # Job CRUD + manual run
        │   ├── history.py      # GET /history
        │   └── partials.py     # HTMX partial updates
        └── templates/          # Jinja2 templates (Pico CSS + HTMX)
```

`config.py` stays in the project root (outside the `src/` import root); `main.py` and
`cli.py` put the root on `sys.path` so `from config import ...` resolves.

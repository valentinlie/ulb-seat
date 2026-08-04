# ULB Seat Reservation Bot

Automated library seat reservation for [ULB Münster](https://www.ulb.uni-muenster.de/).
Schedule recurring or one-shot bookings through a web dashboard, or run a quick booking from the command line.

## Features

- **Web dashboard** -- manage jobs, view booking history, trigger manual runs
- **Recurring jobs** -- book seats automatically on specific days of the week
- **One-shot jobs** -- schedule a single booking for a specific date
- **Passkey login** -- no passwords; invitation-only, several people per instance
- **Multiple ULB accounts** -- one login can manage and switch between accounts, and an account can be shared
- **CLI** -- book a seat directly from the terminal
- **Captcha solving** -- OCR-based (Tesseract) automatic captcha handling
- **Seat preferences** -- per account, set in the dashboard: tries your preferred seats first, falls back to any available

### Who owns what

- A **user** is a person, identified by one or more **passkeys** (phone, laptop, security key).
- An **account** is a ULB login: SSO credentials plus a library card number. Jobs and booking history belong to an account.
- **Membership** links them many-to-many, so one passkey login can manage several accounts, and one account can be shared with several people.
- Signing up is **invitation only**. Admins mint single-use invitation links; the first user is created from the command line and is automatically an admin.

## Requirements

- Python 3.13+
- PostgreSQL
- A domain with HTTPS -- passkeys only work in a secure context (`localhost` is fine for development)
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

ULB credentials are **not** configured here -- they are entered in the dashboard,
stored encrypted, and there can be several sets of them (one per account).
Neither are seat preferences: they belong to an account and are edited under
Settings.

```python
"""Configuration for ULB seat reservation."""

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

# ── PostgreSQL ───────────────────────────────────────────────────────────────
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "ulb_seat"
DB_USER = "your_db_user"
DB_PASS = "your_db_password"

# ── Encryption of stored ULB passwords ───────────────────────────────────────
# Generate with:
#   uv run python -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
# Changing this makes every stored ULB password unreadable.
CREDENTIAL_KEY = "your_generated_key"

# ── Passkeys (WebAuthn) ──────────────────────────────────────────────────────
RP_ID = "seats.example.com"          # bare hostname, no scheme or port
RP_NAME = "ULB Seat Reservation"     # shown in the passkey prompt
ORIGIN = "https://seats.example.com" # scheme + host (+ port), no path

SESSION_MAX_AGE = 30 * 24 * 3600     # how long a login lasts

# Optional: only for local development over plain http.
# COOKIE_SECURE = False

# ── Web server ───────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8000
```

`RP_ID` must be the exact hostname the dashboard is served from (or a parent of
it) -- `seats.example.com`, not `https://seats.example.com` and not with a port.
`ORIGIN` must match what the browser shows. A passkey is cryptographically bound
to `RP_ID`, so moving the dashboard to another hostname means registering new
ones -- pick the final domain before inviting anyone.

Sessions are rows in the database behind an opaque cookie rather than signed
cookies, so there is no secret to configure; enrolment goes through invitations.
There is no switch to turn authentication off -- with several users there is no
sensible identity to fall back to.

### 4. Create the first user

Passkeys can only be registered through an invitation, and the first one comes
from the command line:

```bash
uv run python src/cli.py invite --note "me"
```

That prints a single-use link. Open it in a browser, pick a name, and create a
passkey -- the first user is automatically an admin. From then on, invitations
are minted in the dashboard under **Admin**.

## Usage

### Web dashboard

Start the server:

```bash
uv run src/main.py
# or, directly:
uv run uvicorn web.app:app --app-dir src
```

Open the dashboard and sign in with your passkey.

From the dashboard you can:

- **Add a ULB account** -- Accounts > Add ULB account (SSO login + library card)
- **Switch accounts** with the selector in the nav bar; jobs and history always
  belong to the account currently selected
- **Share an account** with another user from its edit page -- they can then
  manage its jobs
- **Create jobs** -- go to Jobs > New Job
- **Toggle jobs** on/off
- **Run a job immediately** with the "Run Now" button
- **View booking history** with status and error messages
- **Set seat preferences** under your name in the nav bar > Settings: seat and
  group room numbers to try first, in order, for the account currently selected
- **Add more passkeys** under your name in the nav bar, so you can still sign in
  if you lose a device
- **Invite people** under Admin (admins only): a single-use link that optionally
  pre-shares one of your ULB accounts and can grant admin rights

Removing a user deletes their passkeys and access. Accounts they shared stay put
unless they were the only member.

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

The `ulb` CLI (`uv run python src/cli.py <command>`) has four command groups:
booking, users, the web UI, and service management. For quick one-off bookings
without the web interface, use `book`:

```bash
# Book a seat 3 days from now, morning slot
uv run python src/cli.py book --date-offset 3 --time "08:00-12:00"

# Book at a specific library on a specific date
uv run python src/cli.py book --library 22 --date "20.03.2026" --time "12:00-16:00"

# Book a group room
uv run python src/cli.py book --date-offset 2 --time "08:00-12:00" --group-room

# Prefer a specific section
uv run python src/cli.py book --date-offset 3 --time "08:00-12:00" --section "Hauptlesesaal"

# With several ULB accounts, say which one to book with (id or label)
uv run python src/cli.py book --date-offset 3 --time "08:00-12:00" --account "My login"
```

User management from the terminal:

```bash
uv run python src/cli.py invite --note "for Bob" --days 7   # single-use link
uv run python src/cli.py invite --admin --note "co-admin"   # can invite others
uv run python src/cli.py users                              # who has access
uv run python src/cli.py accounts                           # ULB accounts + members
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

Put a TLS-terminating reverse proxy in front of `ulb-web.socket` and point
`ORIGIN` at it. Passkeys are refused on plain HTTP, and session cookies
are sent with `Secure` unless you set `COOKIE_SECURE = False`.

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
    │   ├── auth.py             # SSO login and captcha flow (per ULB account)
    │   ├── booking.py          # Booking orchestration
    │   ├── captcha.py          # Tesseract OCR captcha solver
    │   ├── crypto.py           # AES-256-GCM encryption of stored ULB passwords
    │   ├── db.py               # PostgreSQL access (users, accounts, jobs, log)
    │   ├── exceptions.py       # BookingError
    │   ├── reservation.py      # Timeslot search, seat selection, reservation
    │   ├── worker.py           # Execute one scheduled job, then exit
    │   └── systemd.py          # Generate per-job timers + web units
    │
    └── web/
        ├── app.py              # FastAPI app (socket-activated, idle-shutdown)
        ├── auth.py             # Session cookies + auth dependencies
        ├── passkeys.py         # WebAuthn ceremonies
        ├── routes/
        │   ├── auth.py         # Passkey login, invitations, /settings
        │   ├── accounts.py     # ULB accounts, members, switching
        │   ├── admin.py        # Invitations and users
        │   ├── dashboard.py    # GET /
        │   ├── jobs.py         # Job CRUD + manual run
        │   └── history.py      # GET /history
        └── templates/          # Jinja2 templates (Pico CSS + HTMX)
```

`config.py` stays in the project root (outside the `src/` import root); `main.py` and
`cli.py` put the root on `sys.path` so `from config import ...` resolves.

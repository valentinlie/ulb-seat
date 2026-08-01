"""PostgreSQL database: users, passkeys, ULB accounts, jobs and booking history.

The ownership model is two-level, because a person and a library identity are
different things:

- a **user** is a person, identified by one or more **passkeys** (phone, laptop);
- an **account** is a ULB/SSO identity (username, password, library card) that
  jobs and bookings belong to;
- **membership** links the two many-to-many, so one passkey login can manage
  several accounts and one account can be shared with several people.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.rows import class_row, dict_row
from psycopg_pool import ConnectionPool

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from core import crypto

log = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/Berlin")

try:
    from config import SESSION_MAX_AGE
except ImportError:
    SESSION_MAX_AGE = 30 * 24 * 3600

SESSION_TTL = timedelta(seconds=SESSION_MAX_AGE)
CHALLENGE_TTL = timedelta(minutes=5)
INVITE_TTL = timedelta(days=14)

_pool = ConnectionPool(
    conninfo=f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}",
    min_size=1,
    max_size=4,
    kwargs={"row_factory": dict_row, "options": "-c TimeZone=Europe/Berlin"},
    open=True,
)


@dataclass
class User:
    id: int
    display_name: str
    handle: bytes
    is_admin: bool
    created_at: datetime


@dataclass
class Credential:
    id: int
    user_id: int
    credential_id: bytes
    public_key: bytes
    sign_count: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


@dataclass
class Account:
    id: int
    label: str
    sso_username: str
    sso_password_enc: str
    library_number: str
    created_at: datetime

    @property
    def sso_password(self) -> str:
        """The decrypted SSO password. Raises ValueError on a key mismatch."""
        return crypto.decrypt(self.sso_password_enc, _account_context(self.id))


@dataclass
class Invite:
    token: str
    note: str | None
    grants_admin: bool
    account_id: int | None
    created_by: int | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_by: int | None


@dataclass
class Job:
    id: int
    account_id: int | None
    name: str
    library_id: int
    time_slot: str
    group_room: bool
    preferred_section: str | None
    recurring: bool
    cron_days: str | None
    date_offset: int | None
    cron_hour: int | None
    cron_minute: int | None
    run_at: datetime | None
    target_date: date | None
    enabled: bool
    created_at: datetime
    # form-only fields, filled by the edit route from run_at (not DB columns)
    run_date: str = ""
    run_hour: int = 0
    run_minute: int = 0


@dataclass
class Booking:
    id: int
    account_id: int | None
    job_id: int | None
    job_name: str | None
    library_id: int
    target_date: date
    time_slot: str
    group_room: bool
    status: str
    seat_desc: str | None
    message: str | None
    started_at: datetime
    finished_at: datetime | None
    manual: bool


def _now() -> datetime:
    return datetime.now(_TZ)


def now() -> datetime:
    """Current time in the app's timezone, for callers outside this module."""
    return _now()


def close_pool() -> None:
    _pool.close()


# ── Schema ───────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_users (
                id              SERIAL PRIMARY KEY,
                display_name    VARCHAR(100) NOT NULL,
                handle          BYTEA NOT NULL UNIQUE,
                is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_credentials (
                id              SERIAL PRIMARY KEY,
                user_id         INT NOT NULL REFERENCES ulb_users(id) ON DELETE CASCADE,
                credential_id   BYTEA NOT NULL UNIQUE,
                public_key      BYTEA NOT NULL,
                sign_count      BIGINT NOT NULL DEFAULT 0,
                name            VARCHAR(100) NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL,
                last_used_at    TIMESTAMPTZ
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_accounts (
                id                SERIAL PRIMARY KEY,
                label             VARCHAR(100) NOT NULL,
                sso_username      VARCHAR(255) NOT NULL,
                sso_password_enc  TEXT NOT NULL,
                library_number    VARCHAR(100) NOT NULL,
                created_at        TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_account_members (
                account_id      INT NOT NULL REFERENCES ulb_accounts(id) ON DELETE CASCADE,
                user_id         INT NOT NULL REFERENCES ulb_users(id) ON DELETE CASCADE,
                created_at      TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (account_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_sessions (
                token_hash        CHAR(64) PRIMARY KEY,
                user_id           INT NOT NULL REFERENCES ulb_users(id) ON DELETE CASCADE,
                active_account_id INT REFERENCES ulb_accounts(id) ON DELETE SET NULL,
                created_at        TIMESTAMPTZ NOT NULL,
                expires_at        TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_challenges (
                id              SERIAL PRIMARY KEY,
                challenge       BYTEA NOT NULL,
                purpose         VARCHAR(20) NOT NULL,
                user_id         INT REFERENCES ulb_users(id) ON DELETE CASCADE,
                user_handle     BYTEA,
                display_name    VARCHAR(100),
                invite_token    VARCHAR(64),
                created_at      TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_invites (
                token           VARCHAR(64) PRIMARY KEY,
                note            VARCHAR(255),
                grants_admin    BOOLEAN NOT NULL DEFAULT FALSE,
                account_id      INT REFERENCES ulb_accounts(id) ON DELETE SET NULL,
                created_by      INT REFERENCES ulb_users(id) ON DELETE SET NULL,
                created_at      TIMESTAMPTZ NOT NULL,
                expires_at      TIMESTAMPTZ NOT NULL,
                used_at         TIMESTAMPTZ,
                used_by         INT REFERENCES ulb_users(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_jobs (
                id              SERIAL PRIMARY KEY,
                account_id      INT REFERENCES ulb_accounts(id) ON DELETE CASCADE,
                name            VARCHAR(255) NOT NULL,
                library_id      INT NOT NULL,
                time_slot       VARCHAR(50) NOT NULL,
                group_room      BOOLEAN NOT NULL DEFAULT FALSE,
                preferred_section VARCHAR(255),
                recurring       BOOLEAN NOT NULL DEFAULT FALSE,
                cron_days       VARCHAR(100),
                date_offset     INT,
                cron_hour       INT,
                cron_minute     INT,
                run_at          TIMESTAMPTZ,
                target_date     DATE,
                enabled         BOOLEAN NOT NULL DEFAULT TRUE,
                created_at      TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_booking_log (
                id              SERIAL PRIMARY KEY,
                account_id      INT REFERENCES ulb_accounts(id) ON DELETE CASCADE,
                job_id          INT REFERENCES ulb_jobs(id) ON DELETE SET NULL,
                job_name        VARCHAR(255),
                library_id      INT NOT NULL,
                target_date     DATE NOT NULL,
                time_slot       VARCHAR(50) NOT NULL,
                group_room      BOOLEAN NOT NULL DEFAULT FALSE,
                status          VARCHAR(20) NOT NULL,
                seat_desc       VARCHAR(255),
                message         TEXT,
                started_at      TIMESTAMPTZ NOT NULL,
                finished_at     TIMESTAMPTZ,
                manual          BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)


# ── Users ────────────────────────────────────────────────────────────────────

def create_user(display_name: str, handle: bytes, is_admin: bool = False) -> int:
    with _pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO ulb_users (display_name, handle, is_admin, created_at)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (display_name, handle, is_admin, _now()),
        ).fetchone()
    return row["id"]


def get_user(user_id: int) -> User | None:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(User)) as cur:
            cur.execute("SELECT * FROM ulb_users WHERE id = %s", (user_id,))
            return cur.fetchone()


def get_all_users() -> list[User]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(User)) as cur:
            cur.execute("SELECT * FROM ulb_users ORDER BY created_at")
            return cur.fetchall()


def count_users() -> int:
    with _pool.connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM ulb_users").fetchone()["n"]


def rename_user(user_id: int, display_name: str) -> None:
    with _pool.connection() as conn:
        conn.execute("UPDATE ulb_users SET display_name = %s WHERE id = %s",
                     (display_name, user_id))


def set_user_admin(user_id: int, is_admin: bool) -> None:
    with _pool.connection() as conn:
        conn.execute("UPDATE ulb_users SET is_admin = %s WHERE id = %s", (is_admin, user_id))


def count_admins() -> int:
    with _pool.connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM ulb_users WHERE is_admin").fetchone()["n"]


def delete_user(user_id: int) -> None:
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_users WHERE id = %s", (user_id,))


# ── Passkeys ─────────────────────────────────────────────────────────────────

def add_credential(user_id: int, credential_id: bytes, public_key: bytes,
                   sign_count: int, name: str) -> int:
    with _pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO ulb_credentials
               (user_id, credential_id, public_key, sign_count, name, created_at)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, credential_id, public_key, sign_count, name, _now()),
        ).fetchone()
    return row["id"]


def get_credential(credential_id: bytes) -> Credential | None:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Credential)) as cur:
            cur.execute("SELECT * FROM ulb_credentials WHERE credential_id = %s",
                        (credential_id,))
            return cur.fetchone()


def get_credentials_for_user(user_id: int) -> list[Credential]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Credential)) as cur:
            cur.execute("SELECT * FROM ulb_credentials WHERE user_id = %s ORDER BY created_at",
                        (user_id,))
            return cur.fetchall()


def touch_credential(credential_id: bytes, sign_count: int) -> None:
    with _pool.connection() as conn:
        conn.execute(
            "UPDATE ulb_credentials SET sign_count = %s, last_used_at = %s WHERE credential_id = %s",
            (sign_count, _now(), credential_id),
        )


def delete_credential(cred_row_id: int, user_id: int) -> None:
    """Delete one of the user's own passkeys (never the last one)."""
    with _pool.connection() as conn:
        remaining = conn.execute(
            "SELECT count(*) AS n FROM ulb_credentials WHERE user_id = %s", (user_id,)
        ).fetchone()["n"]
        if remaining <= 1:
            return
        conn.execute("DELETE FROM ulb_credentials WHERE id = %s AND user_id = %s",
                     (cred_row_id, user_id))


# ── Accounts ─────────────────────────────────────────────────────────────────

def _account_context(account_id: int) -> str:
    """What an account's stored password is encrypted against (see core.crypto)."""
    return f"ulb-account:{account_id}"


def create_account(label: str, sso_username: str, sso_password: str,
                   library_number: str, owner_id: int | None = None) -> int:
    with _pool.connection() as conn:
        # The ciphertext is bound to the row id, so take the id from the sequence
        # before inserting rather than letting the INSERT hand it back.
        account_id = conn.execute(
            "SELECT nextval(pg_get_serial_sequence('ulb_accounts', 'id')) AS id"
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO ulb_accounts
               (id, label, sso_username, sso_password_enc, library_number, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (account_id, label, sso_username,
             crypto.encrypt(sso_password, _account_context(account_id)),
             library_number, _now()),
        )
        if owner_id is not None:
            conn.execute(
                """INSERT INTO ulb_account_members (account_id, user_id, created_at)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (account_id, owner_id, _now()),
            )
    return account_id


def get_account(account_id: int) -> Account | None:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Account)) as cur:
            cur.execute("SELECT * FROM ulb_accounts WHERE id = %s", (account_id,))
            return cur.fetchone()


def get_accounts_for_user(user_id: int) -> list[Account]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Account)) as cur:
            cur.execute(
                """SELECT a.* FROM ulb_accounts a
                   JOIN ulb_account_members m ON m.account_id = a.id
                   WHERE m.user_id = %s ORDER BY a.label""",
                (user_id,),
            )
            return cur.fetchall()


def get_all_accounts() -> list[Account]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Account)) as cur:
            cur.execute("SELECT * FROM ulb_accounts ORDER BY label")
            return cur.fetchall()


def is_member(user_id: int, account_id: int) -> bool:
    with _pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM ulb_account_members WHERE user_id = %s AND account_id = %s",
            (user_id, account_id),
        ).fetchone()
    return row is not None


def update_account(account_id: int, label: str, sso_username: str,
                   library_number: str, sso_password: str | None = None) -> None:
    """Update an account. A blank password leaves the stored one untouched."""
    with _pool.connection() as conn:
        if sso_password:
            conn.execute(
                """UPDATE ulb_accounts SET label=%s, sso_username=%s,
                   sso_password_enc=%s, library_number=%s WHERE id=%s""",
                (label, sso_username,
                 crypto.encrypt(sso_password, _account_context(account_id)),
                 library_number, account_id),
            )
        else:
            conn.execute(
                "UPDATE ulb_accounts SET label=%s, sso_username=%s, library_number=%s WHERE id=%s",
                (label, sso_username, library_number, account_id),
            )


def delete_account(account_id: int) -> None:
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_accounts WHERE id = %s", (account_id,))


def get_members(account_id: int) -> list[User]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(User)) as cur:
            cur.execute(
                """SELECT u.* FROM ulb_users u
                   JOIN ulb_account_members m ON m.user_id = u.id
                   WHERE m.account_id = %s ORDER BY u.display_name""",
                (account_id,),
            )
            return cur.fetchall()


def add_member(account_id: int, user_id: int) -> None:
    with _pool.connection() as conn:
        conn.execute(
            """INSERT INTO ulb_account_members (account_id, user_id, created_at)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (account_id, user_id, _now()),
        )


def remove_member(account_id: int, user_id: int) -> bool:
    """Remove a member. Refuses to remove the last one (that would orphan the account)."""
    with _pool.connection() as conn:
        remaining = conn.execute(
            "SELECT count(*) AS n FROM ulb_account_members WHERE account_id = %s", (account_id,)
        ).fetchone()["n"]
        if remaining <= 1:
            return False
        conn.execute(
            "DELETE FROM ulb_account_members WHERE account_id = %s AND user_id = %s",
            (account_id, user_id),
        )
    return True


def transfer_sole_owned_accounts(user_id: int, to_user_id: int) -> int:
    """Hand over the accounts ``user_id`` is the only member of. Returns how many.

    Call this before deleting a user: their memberships cascade away with them,
    and an account with no members is invisible to everyone — while its timers
    keep booking. Accounts they share with someone else are left alone.
    """
    with _pool.connection() as conn:
        cur = conn.execute(
            """INSERT INTO ulb_account_members (account_id, user_id, created_at)
               SELECT a.id, %s, %s FROM ulb_accounts a
               WHERE EXISTS (
                       SELECT 1 FROM ulb_account_members m
                       WHERE m.account_id = a.id AND m.user_id = %s
                   )
                 AND (SELECT count(*) FROM ulb_account_members m
                      WHERE m.account_id = a.id) = 1
               ON CONFLICT DO NOTHING""",
            (to_user_id, _now(), user_id),
        )
    return cur.rowcount


# ── Sessions ─────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, active_account_id: int | None) -> str:
    """Create a login session. Returns the raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    now = _now()
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_sessions WHERE expires_at < %s", (now,))
        conn.execute(
            """INSERT INTO ulb_sessions
               (token_hash, user_id, active_account_id, created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (_hash_token(token), user_id, active_account_id, now, now + SESSION_TTL),
        )
    return token


def get_session(token: str) -> dict | None:
    """Look up a live session by its raw token."""
    with _pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM ulb_sessions WHERE token_hash = %s AND expires_at > %s",
            (_hash_token(token), _now()),
        ).fetchone()


def set_active_account(token: str, account_id: int | None) -> None:
    with _pool.connection() as conn:
        conn.execute("UPDATE ulb_sessions SET active_account_id = %s WHERE token_hash = %s",
                     (account_id, _hash_token(token)))


def delete_session(token: str) -> None:
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_sessions WHERE token_hash = %s", (_hash_token(token),))


def delete_sessions_for_user(user_id: int) -> None:
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_sessions WHERE user_id = %s", (user_id,))


# ── WebAuthn challenges ──────────────────────────────────────────────────────

def create_challenge(challenge: bytes, purpose: str, user_id: int | None = None,
                     user_handle: bytes | None = None, display_name: str | None = None,
                     invite_token: str | None = None) -> int:
    now = _now()
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_challenges WHERE created_at < %s", (now - CHALLENGE_TTL,))
        row = conn.execute(
            """INSERT INTO ulb_challenges
               (challenge, purpose, user_id, user_handle, display_name, invite_token, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (challenge, purpose, user_id, user_handle, display_name, invite_token, now),
        ).fetchone()
    return row["id"]


def pop_challenge(challenge_row_id: int, purpose: str) -> dict | None:
    """Fetch and delete a challenge — single use, so a replay finds nothing."""
    with _pool.connection() as conn:
        return conn.execute(
            """DELETE FROM ulb_challenges
               WHERE id = %s AND purpose = %s AND created_at > %s
               RETURNING *""",
            (challenge_row_id, purpose, _now() - CHALLENGE_TTL),
        ).fetchone()


# ── Invites ──────────────────────────────────────────────────────────────────

def create_invite(created_by: int | None, note: str | None = None,
                  grants_admin: bool = False, account_id: int | None = None,
                  ttl: timedelta = INVITE_TTL) -> str:
    token = secrets.token_urlsafe(24)
    now = _now()
    with _pool.connection() as conn:
        conn.execute(
            """INSERT INTO ulb_invites
               (token, note, grants_admin, account_id, created_by, created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (token, note, grants_admin, account_id, created_by, now, now + ttl),
        )
    return token


def get_valid_invite(token: str) -> Invite | None:
    """An invite that is neither used nor expired, or None."""
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Invite)) as cur:
            cur.execute(
                """SELECT * FROM ulb_invites
                   WHERE token = %s AND used_at IS NULL AND expires_at > %s""",
                (token, _now()),
            )
            return cur.fetchone()


def consume_invite(token: str, user_id: int) -> bool:
    """Mark an invite used. False if someone got there first (race-safe)."""
    with _pool.connection() as conn:
        row = conn.execute(
            """UPDATE ulb_invites SET used_at = %s, used_by = %s
               WHERE token = %s AND used_at IS NULL AND expires_at > %s
               RETURNING token""",
            (_now(), user_id, token, _now()),
        ).fetchone()
    return row is not None


def revoke_invite(token: str) -> None:
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_invites WHERE token = %s AND used_at IS NULL", (token,))


def get_all_invites() -> list[Invite]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Invite)) as cur:
            cur.execute("SELECT * FROM ulb_invites ORDER BY created_at DESC")
            return cur.fetchall()


# ── Jobs CRUD ────────────────────────────────────────────────────────────────

def create_job(account_id: int, data: dict) -> int:
    with _pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO ulb_jobs
               (account_id, name, library_id, time_slot, group_room, preferred_section,
                recurring, cron_days, date_offset, cron_hour, cron_minute,
                run_at, target_date, enabled, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                account_id,
                data["name"],
                data["library_id"],
                data["time_slot"],
                bool(data.get("group_room", False)),
                data.get("preferred_section"),
                bool(data.get("recurring", False)),
                data.get("cron_days"),
                data.get("date_offset"),
                data.get("cron_hour"),
                data.get("cron_minute"),
                data.get("run_at"),
                data.get("target_date"),
                bool(data.get("enabled", True)),
                _now(),
            ),
        ).fetchone()
    return row["id"]


def get_job(job_id: int, account_id: int | None = None) -> Job | None:
    """Fetch a job. Pass ``account_id`` to scope it to one account's jobs."""
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Job)) as cur:
            if account_id is None:
                cur.execute("SELECT * FROM ulb_jobs WHERE id = %s", (job_id,))
            else:
                cur.execute("SELECT * FROM ulb_jobs WHERE id = %s AND account_id = %s",
                            (job_id, account_id))
            return cur.fetchone()


def get_all_jobs(account_id: int | None = None) -> list[Job]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Job)) as cur:
            if account_id is None:
                cur.execute("SELECT * FROM ulb_jobs ORDER BY created_at DESC")
            else:
                cur.execute("SELECT * FROM ulb_jobs WHERE account_id = %s ORDER BY created_at DESC",
                            (account_id,))
            return cur.fetchall()


def get_enabled_jobs(account_id: int | None = None) -> list[Job]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Job)) as cur:
            if account_id is None:
                cur.execute("SELECT * FROM ulb_jobs WHERE enabled ORDER BY id")
            else:
                cur.execute("SELECT * FROM ulb_jobs WHERE enabled AND account_id = %s ORDER BY id",
                            (account_id,))
            return cur.fetchall()


def update_job(job_id: int, data: dict) -> None:
    with _pool.connection() as conn:
        conn.execute(
            """UPDATE ulb_jobs SET
               name=%s, library_id=%s, time_slot=%s, group_room=%s,
               preferred_section=%s, recurring=%s,
               cron_days=%s, date_offset=%s, cron_hour=%s, cron_minute=%s,
               run_at=%s, target_date=%s, enabled=%s
               WHERE id=%s""",
            (
                data["name"],
                data["library_id"],
                data["time_slot"],
                bool(data.get("group_room", False)),
                data.get("preferred_section"),
                bool(data.get("recurring", False)),
                data.get("cron_days"),
                data.get("date_offset"),
                data.get("cron_hour"),
                data.get("cron_minute"),
                data.get("run_at"),
                data.get("target_date"),
                bool(data.get("enabled", True)),
                job_id,
            ),
        )


def delete_job(job_id: int) -> None:
    with _pool.connection() as conn:
        conn.execute("DELETE FROM ulb_jobs WHERE id = %s", (job_id,))


def toggle_job(job_id: int) -> bool:
    """Toggle enabled state. Returns new state."""
    with _pool.connection() as conn:
        row = conn.execute(
            "UPDATE ulb_jobs SET enabled = NOT enabled WHERE id = %s RETURNING enabled",
            (job_id,),
        ).fetchone()
    return row["enabled"] if row else False


def disable_job(job_id: int) -> None:
    with _pool.connection() as conn:
        conn.execute("UPDATE ulb_jobs SET enabled = FALSE WHERE id = %s", (job_id,))


# ── Booking log ──────────────────────────────────────────────────────────────

def log_booking_start(account_id: int | None, job_id: int | None, job_name: str,
                      library_id: int, target_date: date, time_slot: str,
                      group_room: bool, manual: bool = False) -> int:
    with _pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO ulb_booking_log
               (account_id, job_id, job_name, library_id, target_date, time_slot, group_room,
                status, started_at, manual)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s)
               RETURNING id""",
            (account_id, job_id, job_name, library_id, target_date, time_slot,
             group_room, _now(), manual),
        ).fetchone()
    return row["id"]


def log_booking_finish(log_id: int, status: str, seat_desc: str = None,
                       message: str = None) -> None:
    with _pool.connection() as conn:
        conn.execute(
            """UPDATE ulb_booking_log SET status=%s, seat_desc=%s, message=%s, finished_at=%s
               WHERE id=%s""",
            (status, seat_desc, message, _now(), log_id),
        )


def get_recent_bookings(account_id: int | None = None, limit: int = 50) -> list[Booking]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Booking)) as cur:
            if account_id is None:
                cur.execute("SELECT * FROM ulb_booking_log ORDER BY started_at DESC LIMIT %s",
                            (limit,))
            else:
                cur.execute(
                    """SELECT * FROM ulb_booking_log WHERE account_id = %s
                       ORDER BY started_at DESC LIMIT %s""",
                    (account_id, limit),
                )
            return cur.fetchall()

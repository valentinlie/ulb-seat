"""PostgreSQL database for job definitions and booking history."""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from psycopg.rows import class_row, dict_row
from psycopg_pool import ConnectionPool

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

_TZ = ZoneInfo("Europe/Berlin")

_pool = ConnectionPool(
    conninfo=f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}",
    min_size=1,
    max_size=4,
    kwargs={"row_factory": dict_row, "options": "-c TimeZone=Europe/Berlin"},
    open=True,
)


@dataclass
class Job:
    id: int
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


def close_pool() -> None:
    _pool.close()


def _column_type(conn, table: str, column: str) -> str | None:
    row = conn.execute(
        """SELECT data_type FROM information_schema.columns
           WHERE table_name = %s AND column_name = %s""",
        (table, column),
    ).fetchone()
    return row["data_type"] if row else None


def _migrate_legacy_types(conn) -> None:
    """Upgrade pre-existing tables from SMALLINT/VARCHAR to BOOLEAN/DATE/TIMESTAMPTZ."""
    if _column_type(conn, "ulb_jobs", "enabled") == "smallint":
        conn.execute("""
            ALTER TABLE ulb_jobs
                ALTER COLUMN group_room DROP DEFAULT,
                ALTER COLUMN group_room TYPE BOOLEAN USING group_room::int::boolean,
                ALTER COLUMN group_room SET DEFAULT FALSE,
                ALTER COLUMN recurring DROP DEFAULT,
                ALTER COLUMN recurring TYPE BOOLEAN USING recurring::int::boolean,
                ALTER COLUMN recurring SET DEFAULT FALSE,
                ALTER COLUMN enabled DROP DEFAULT,
                ALTER COLUMN enabled TYPE BOOLEAN USING enabled::int::boolean,
                ALTER COLUMN enabled SET DEFAULT TRUE,
                ALTER COLUMN run_at TYPE TIMESTAMPTZ USING NULLIF(run_at, '')::timestamptz,
                ALTER COLUMN target_date TYPE DATE USING to_date(NULLIF(target_date, ''), 'DD.MM.YYYY'),
                ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at::timestamptz
        """)
    if _column_type(conn, "ulb_booking_log", "manual") == "smallint":
        conn.execute("""
            ALTER TABLE ulb_booking_log
                ALTER COLUMN group_room DROP DEFAULT,
                ALTER COLUMN group_room TYPE BOOLEAN USING group_room::int::boolean,
                ALTER COLUMN group_room SET DEFAULT FALSE,
                ALTER COLUMN manual DROP DEFAULT,
                ALTER COLUMN manual TYPE BOOLEAN USING manual::int::boolean,
                ALTER COLUMN manual SET DEFAULT FALSE,
                ALTER COLUMN target_date TYPE DATE USING to_date(NULLIF(target_date, ''), 'DD.MM.YYYY'),
                ALTER COLUMN started_at TYPE TIMESTAMPTZ USING started_at::timestamptz,
                ALTER COLUMN finished_at TYPE TIMESTAMPTZ USING NULLIF(finished_at, '')::timestamptz
        """)


def init_db() -> None:
    with _pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ulb_jobs (
                id              SERIAL PRIMARY KEY,
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
        _migrate_legacy_types(conn)


# ── Jobs CRUD ────────────────────────────────────────────────────────────────

def create_job(data: dict) -> int:
    with _pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO ulb_jobs
               (name, library_id, time_slot, group_room, preferred_section,
                recurring, cron_days, date_offset, cron_hour, cron_minute,
                run_at, target_date, enabled, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
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
                _now(),
            ),
        ).fetchone()
    return row["id"]


def get_job(job_id: int) -> Job | None:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Job)) as cur:
            cur.execute("SELECT * FROM ulb_jobs WHERE id = %s", (job_id,))
            return cur.fetchone()


def get_all_jobs() -> list[Job]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Job)) as cur:
            cur.execute("SELECT * FROM ulb_jobs ORDER BY created_at DESC")
            return cur.fetchall()


def get_enabled_jobs() -> list[Job]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Job)) as cur:
            cur.execute("SELECT * FROM ulb_jobs WHERE enabled ORDER BY id")
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

def log_booking_start(job_id: int | None, job_name: str, library_id: int,
                      target_date: date, time_slot: str, group_room: bool,
                      manual: bool = False) -> int:
    with _pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO ulb_booking_log
               (job_id, job_name, library_id, target_date, time_slot, group_room,
                status, started_at, manual)
               VALUES (%s, %s, %s, %s, %s, %s, 'running', %s, %s)
               RETURNING id""",
            (job_id, job_name, library_id, target_date, time_slot,
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


def get_recent_bookings(limit: int = 50) -> list[Booking]:
    with _pool.connection() as conn:
        with conn.cursor(row_factory=class_row(Booking)) as cur:
            cur.execute("SELECT * FROM ulb_booking_log ORDER BY started_at DESC LIMIT %s", (limit,))
            return cur.fetchall()

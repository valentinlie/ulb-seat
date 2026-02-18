"""MariaDB database for job definitions and booking history."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pymysql
import pymysql.cursors

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

_TZ = ZoneInfo("Europe/Berlin")


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


def get_connection() -> pymysql.Connection:
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def init_db() -> None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                name            VARCHAR(255) NOT NULL,
                library_id      INT NOT NULL,
                time_slot       VARCHAR(50) NOT NULL,
                group_room      TINYINT NOT NULL DEFAULT 0,
                preferred_section VARCHAR(255),
                recurring       TINYINT NOT NULL DEFAULT 0,
                cron_days       VARCHAR(100),
                date_offset     INT,
                cron_hour       INT,
                cron_minute     INT,
                run_at          VARCHAR(50),
                target_date     VARCHAR(20),
                enabled         TINYINT NOT NULL DEFAULT 1,
                created_at      VARCHAR(50) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS booking_log (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                job_id          INT,
                job_name        VARCHAR(255),
                library_id      INT NOT NULL,
                target_date     VARCHAR(20) NOT NULL,
                time_slot       VARCHAR(50) NOT NULL,
                group_room      TINYINT NOT NULL DEFAULT 0,
                status          VARCHAR(20) NOT NULL,
                seat_desc       VARCHAR(255),
                message         TEXT,
                started_at      VARCHAR(50) NOT NULL,
                finished_at     VARCHAR(50),
                manual          TINYINT NOT NULL DEFAULT 0,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()
    conn.close()


# ── Jobs CRUD ────────────────────────────────────────────────────────────────

def create_job(data: dict) -> int:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO jobs
               (name, library_id, time_slot, group_room, preferred_section,
                recurring, cron_days, date_offset, cron_hour, cron_minute,
                run_at, target_date, enabled, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                data["name"],
                data["library_id"],
                data["time_slot"],
                int(data.get("group_room", False)),
                data.get("preferred_section"),
                int(data.get("recurring", False)),
                data.get("cron_days"),
                data.get("date_offset"),
                data.get("cron_hour"),
                data.get("cron_minute"),
                data.get("run_at"),
                data.get("target_date"),
                int(data.get("enabled", True)),
                _now_iso(),
            ),
        )
        conn.commit()
        job_id = cur.lastrowid
    conn.close()
    return job_id


def get_job(job_id: int) -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    conn.close()
    return row


def get_all_jobs() -> list[dict]:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cur.fetchall()
    conn.close()
    return rows


def get_enabled_jobs() -> list[dict]:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE enabled = 1 ORDER BY id")
        rows = cur.fetchall()
    conn.close()
    return rows


def update_job(job_id: int, data: dict) -> None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs SET
               name=%s, library_id=%s, time_slot=%s, group_room=%s,
               preferred_section=%s, recurring=%s,
               cron_days=%s, date_offset=%s, cron_hour=%s, cron_minute=%s,
               run_at=%s, target_date=%s, enabled=%s
               WHERE id=%s""",
            (
                data["name"],
                data["library_id"],
                data["time_slot"],
                int(data.get("group_room", False)),
                data.get("preferred_section"),
                int(data.get("recurring", False)),
                data.get("cron_days"),
                data.get("date_offset"),
                data.get("cron_hour"),
                data.get("cron_minute"),
                data.get("run_at"),
                data.get("target_date"),
                int(data.get("enabled", True)),
                job_id,
            ),
        )
    conn.commit()
    conn.close()


def delete_job(job_id: int) -> None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
    conn.commit()
    conn.close()


def toggle_job(job_id: int) -> bool:
    """Toggle enabled state. Returns new state."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET enabled = 1 - enabled WHERE id = %s", (job_id,))
        conn.commit()
        cur.execute("SELECT enabled FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    conn.close()
    return bool(row["enabled"]) if row else False


def disable_job(job_id: int) -> None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET enabled = 0 WHERE id = %s", (job_id,))
    conn.commit()
    conn.close()


# ── Booking log ──────────────────────────────────────────────────────────────

def log_booking_start(job_id: int | None, job_name: str, library_id: int,
                      target_date: str, time_slot: str, group_room: bool,
                      manual: bool = False) -> int:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO booking_log
               (job_id, job_name, library_id, target_date, time_slot, group_room,
                status, started_at, manual)
               VALUES (%s, %s, %s, %s, %s, %s, 'running', %s, %s)""",
            (job_id, job_name, library_id, target_date, time_slot,
             int(group_room), _now_iso(), int(manual)),
        )
        conn.commit()
        log_id = cur.lastrowid
    conn.close()
    return log_id


def log_booking_finish(log_id: int, status: str, seat_desc: str = None,
                       message: str = None) -> None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE booking_log SET status=%s, seat_desc=%s, message=%s, finished_at=%s
               WHERE id=%s""",
            (status, seat_desc, message, _now_iso(), log_id),
        )
    conn.commit()
    conn.close()


def get_recent_bookings(limit: int = 50) -> list[dict]:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM booking_log ORDER BY started_at DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    conn.close()
    return rows

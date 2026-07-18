import sqlite3
from contextlib import contextmanager

DB_PATH = "scout.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_encrypted TEXT
);

CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    road_name TEXT NOT NULL,
    gob_name TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 30,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_result TEXT,
    is_running INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_roads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    road_name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info'
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # migration: add created_by to jobs if this db predates it
        job_cols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)")]
        if "created_by" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN created_by INTEGER REFERENCES users(id)")
        # migration: add password_encrypted to accounts if this db predates it
        acct_cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)")]
        if "password_encrypted" not in acct_cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN password_encrypted TEXT")
        # migration: seed job_roads from any pre-existing single-road jobs
        jobs_without_roads = conn.execute(
            "SELECT jobs.id, jobs.road_name FROM jobs "
            "LEFT JOIN job_roads ON job_roads.job_id = jobs.id "
            "WHERE job_roads.id IS NULL"
        ).fetchall()
        for j in jobs_without_roads:
            if j["road_name"]:
                conn.execute(
                    "INSERT INTO job_roads (job_id, road_name, position) VALUES (?, ?, 0)",
                    (j["id"], j["road_name"]),
                )


def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def list_accounts():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM accounts ORDER BY label").fetchall()


def get_account(account_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def add_account(label, username, password_encrypted=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (label, username, password_encrypted) VALUES (?, ?, ?)",
            (label, username, password_encrypted),
        )


def update_account_basic(account_id, label, username):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET label = ?, username = ? WHERE id = ?",
            (label, username, account_id),
        )


def set_account_password(account_id, password_encrypted):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET password_encrypted = ? WHERE id = ?",
            (password_encrypted, account_id),
        )


def clear_account_password(account_id):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET password_encrypted = NULL WHERE id = ?", (account_id,))


def delete_account(account_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def list_characters(account_id=None):
    with get_conn() as conn:
        if account_id:
            return conn.execute(
                "SELECT * FROM characters WHERE account_id = ? ORDER BY name", (account_id,)
            ).fetchall()
        return conn.execute(
            "SELECT characters.*, accounts.label AS account_label, accounts.username AS account_username "
            "FROM characters JOIN accounts ON accounts.id = characters.account_id "
            "ORDER BY accounts.label, characters.name"
        ).fetchall()


def get_character(character_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT characters.*, accounts.label AS account_label, accounts.username AS account_username "
            "FROM characters JOIN accounts ON accounts.id = characters.account_id "
            "WHERE characters.id = ?",
            (character_id,),
        ).fetchone()


def add_character(account_id, name):
    with get_conn() as conn:
        conn.execute("INSERT INTO characters (account_id, name) VALUES (?, ?)", (account_id, name))


def delete_character(character_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))


def list_jobs():
    with get_conn() as conn:
        return conn.execute(
            "SELECT jobs.*, characters.name AS character_name, "
            "accounts.label AS account_label, accounts.username AS account_username, "
            "users.email AS created_by_email, "
            "(SELECT GROUP_CONCAT(road_name, ', ') FROM "
            " (SELECT road_name FROM job_roads WHERE job_roads.job_id = jobs.id ORDER BY position) "
            ") AS roads_display "
            "FROM jobs "
            "JOIN characters ON characters.id = jobs.character_id "
            "JOIN accounts ON accounts.id = characters.account_id "
            "LEFT JOIN users ON users.id = jobs.created_by "
            "ORDER BY accounts.label, characters.name"
        ).fetchall()


def get_job(job_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT jobs.*, characters.name AS character_name, characters.account_id AS account_id, "
            "accounts.label AS account_label, accounts.username AS account_username, "
            "users.email AS created_by_email, "
            "(SELECT GROUP_CONCAT(road_name, ', ') FROM "
            " (SELECT road_name FROM job_roads WHERE job_roads.job_id = jobs.id ORDER BY position) "
            ") AS roads_display "
            "FROM jobs "
            "JOIN characters ON characters.id = jobs.character_id "
            "JOIN accounts ON accounts.id = characters.account_id "
            "LEFT JOIN users ON users.id = jobs.created_by "
            "WHERE jobs.id = ?",
            (job_id,),
        ).fetchone()


def get_job_roads(job_id):
    """Returns an ordered list of road name strings for this job."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT road_name FROM job_roads WHERE job_id = ? ORDER BY position", (job_id,)
        ).fetchall()
        return [r["road_name"] for r in rows]


def set_job_roads(job_id, road_names):
    """Replaces the full ordered list of roads for a job."""
    with get_conn() as conn:
        conn.execute("DELETE FROM job_roads WHERE job_id = ?", (job_id,))
        for i, name in enumerate(road_names):
            if name.strip():
                conn.execute(
                    "INSERT INTO job_roads (job_id, road_name, position) VALUES (?, ?, ?)",
                    (job_id, name.strip(), i),
                )


def add_job(character_id, road_names, gob_name, interval_minutes, created_by=None):
    """road_names is a list of one or more road name strings, checked in order."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (character_id, road_name, gob_name, interval_minutes, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (character_id, road_names[0], gob_name, interval_minutes, created_by),
        )
        job_id = cur.lastrowid
        for i, name in enumerate(road_names):
            if name.strip():
                conn.execute(
                    "INSERT INTO job_roads (job_id, road_name, position) VALUES (?, ?, ?)",
                    (job_id, name.strip(), i),
                )
        return job_id


def update_job(job_id, character_id, road_names, gob_name, interval_minutes):
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET character_id = ?, road_name = ?, gob_name = ?, interval_minutes = ? "
            "WHERE id = ?",
            (character_id, road_names[0], gob_name, interval_minutes, job_id),
        )
        conn.execute("DELETE FROM job_roads WHERE job_id = ?", (job_id,))
        for i, name in enumerate(road_names):
            if name.strip():
                conn.execute(
                    "INSERT INTO job_roads (job_id, road_name, position) VALUES (?, ?, ?)",
                    (job_id, name.strip(), i),
                )


def delete_job(job_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def set_job_enabled(job_id, enabled):
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET enabled = ? WHERE id = ?", (1 if enabled else 0, job_id))


def set_job_running(job_id, running):
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET is_running = ? WHERE id = ?", (1 if running else 0, job_id))


def record_job_result(job_id, when_iso, result):
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET last_run_at = ?, last_result = ?, is_running = 0 WHERE id = ?",
            (when_iso, result, job_id),
        )


def due_jobs(now_iso_minus_intervals):
    """Return enabled jobs whose interval has elapsed. Filtering by time is
    done in Python (scheduler.py) since SQLite date math across intervals
    per-row is awkward; this just returns all enabled, not-running jobs."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT jobs.*, characters.name AS character_name, characters.account_id AS account_id, "
            "accounts.label AS account_label, accounts.username AS account_username "
            "FROM jobs "
            "JOIN characters ON characters.id = jobs.character_id "
            "JOIN accounts ON accounts.id = characters.account_id "
            "WHERE jobs.enabled = 1 AND jobs.is_running = 0"
        ).fetchall()


# --- users ---

def create_user(email, password_hash, is_admin=0, is_approved=0):
    from datetime import datetime, timezone
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, is_admin, is_approved, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email.lower().strip(), password_hash, int(is_admin), int(is_approved),
             datetime.now(timezone.utc).isoformat()),
        )


def get_user_by_email(email):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()


def get_user_by_id(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()


def list_pending_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE is_approved = 0 ORDER BY created_at"
        ).fetchall()


def set_user_approved(user_id, approved):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_approved = ? WHERE id = ?", (1 if approved else 0, user_id))


def set_user_admin(user_id, is_admin):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id))


def delete_user(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def any_admin_exists():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_admin = 1").fetchone()
        return row["c"] > 0


# --- activity log ---

def log_event(actor, action, message, level="info"):
    from datetime import datetime, timezone
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (created_at, actor, action, message, level) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), actor, action, message, level),
        )


def list_log(search=None, limit=500):
    with get_conn() as conn:
        if search:
            like = f"%{search}%"
            return conn.execute(
                "SELECT * FROM activity_log WHERE actor LIKE ? OR action LIKE ? OR message LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

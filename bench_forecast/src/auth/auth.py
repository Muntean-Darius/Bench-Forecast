import sqlite3
import hashlib
import jwt
import uuid
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("./data/auth.sqlite")
SECRET_KEY = 'bench-forecast-secret-key-2026'
ALGORITHM = 'HS256'


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def hash_password(password: str) -> str:
    return hashlib.sha256(('bench_forecast_' + password).encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


def init_auth_db() -> None:
    """Create users table, seed manager, and auto-create accounts for all employees."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('manager','employee')),
        full_name TEXT NOT NULL,
        employee_id TEXT,
        cv_uri TEXT,
        created_at TEXT NOT NULL
    )''')
    conn.commit()

    # Seed manager if not exists
    cur.execute("SELECT 1 FROM users WHERE username='manager'")
    if not cur.fetchone():
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), 'manager', hash_password('manager'),
                     'manager', 'Darius Muntean', None, None, now))
        conn.commit()
        logger.info("Auth: seeded manager account")

    # Sync employee accounts from employee database
    try:
        from src.agents.nodes import _get_db
        db = _get_db()
        employees = db.get_bench_forecast(horizon_days=9999)
        now = datetime.now(timezone.utc).isoformat()
        added = 0
        for emp in employees:
            parts = emp.name.strip().lower().split()
            if len(parts) >= 2:
                username = parts[0] + '_' + parts[-1]
            else:
                username = parts[0]
            # Skip if already exists
            cur.execute("SELECT 1 FROM users WHERE employee_id=?", (emp.id,))
            if cur.fetchone():
                continue
            # Check username collision
            cur.execute("SELECT 1 FROM users WHERE username=?", (username,))
            if cur.fetchone():
                username = username + '_' + emp.id[-4:]
            cur.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), username, hash_password('employee'),
                         'employee', emp.name, emp.id, None, now))
            added += 1
        conn.commit()
        if added:
            logger.info(f"Auth: synced {added} employee accounts from DB")
    except Exception as e:
        logger.warning(f"Auth: employee sync failed (non-fatal): {e}")

    conn.close()


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(hours=24)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def authenticate_user(username: str, password: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row and verify_password(password, row['password_hash']):
        u = dict(row)
        del u['password_hash']
        return u
    return None


def get_user_by_id(user_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    u = dict(row)
    del u['password_hash']
    return u


def change_password(user_id: str, old_password: str, new_password: str) -> bool:
    conn = _conn()
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not verify_password(old_password, row['password_hash']):
        conn.close()
        return False
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    return True


def update_cv_uri(user_id: str, cv_uri: str) -> bool:
    conn = _conn()
    conn.execute("UPDATE users SET cv_uri=? WHERE id=?", (cv_uri, user_id))
    conn.commit()
    conn.close()
    return True

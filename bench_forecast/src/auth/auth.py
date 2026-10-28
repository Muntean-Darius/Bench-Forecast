import hashlib
import jwt
import os
import uuid
import logging
from datetime import datetime, timedelta, timezone

from src.database.database import get_sync_db
from src.database.models import User, Employee

logger = logging.getLogger(__name__)

# Read the signing key from the environment. The development fallback is long
# enough for HS256 (>= 32 bytes) so PyJWT does not emit an insecure-key warning,
# but it is still a known value: production deployments MUST set JWT_SECRET_KEY.
SECRET_KEY = os.getenv("JWT_SECRET_KEY") or "bench-forecast-dev-only-secret-key-change-me-2026"
if not os.getenv("JWT_SECRET_KEY"):
    logger.warning(
        "JWT_SECRET_KEY is not set; using an insecure development default. "
        "Set JWT_SECRET_KEY (>= 32 bytes) before deploying."
    )
elif len(SECRET_KEY.encode("utf-8")) < 32:
    logger.warning(
        "JWT_SECRET_KEY is only %d bytes; HS256 recommends at least 32 bytes.",
        len(SECRET_KEY.encode("utf-8")),
    )
ALGORITHM = 'HS256'

def hash_password(password: str) -> str:
    return hashlib.sha256(('bench_forecast_' + password).encode()).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash

def _unique_username(base: str, used: set[str], employee_id) -> str:
    candidate = base or f"emp_{str(employee_id)[:6]}"
    if candidate in used:
        candidate = f"{base}_{str(employee_id)[:4]}"
    if candidate in used:
        candidate = f"{base}_{str(employee_id)[:8]}"
    return candidate


def init_auth_db() -> None:
    """Seed admin/manager accounts and sync one login per employee.

    Role strategy:
      * ``admin``    — global executive account (no employee link required)
      * ``manager``  — one per employee who has direct reports, linked to the
                       employee record so team scoping works
      * ``employee`` — one per remaining employee
    """
    with get_sync_db() as db:
        try:
            # ---- global admin ------------------------------------------------
            admin = db.query(User).filter(User.username == 'admin').first()
            if not admin:
                admin = User(
                    username='admin',
                    password_hash=hash_password('admin'),
                    role='admin',
                    full_name='Global Administrator',
                )
                db.add(admin)
                db.flush()
                logger.info("Auth: seeded admin account")

            employees = db.query(Employee).all()
            employee_by_id = {emp.id: emp for emp in employees}
            direct_reports = {}
            for emp in employees:
                if emp.manager_id:
                    direct_reports.setdefault(emp.manager_id, []).append(emp)

            lead_ids = set(direct_reports.keys())
            manager_employee = None
            if direct_reports:
                manager_employee = max(
                    (employee_by_id[eid] for eid in lead_ids if eid in employee_by_id),
                    key=lambda e: len(direct_reports.get(e.id, [])),
                    default=None,
                )

            # ---- canonical 'manager' demo account ---------------------------
            manager = db.query(User).filter(User.username == 'manager').first()
            if not manager:
                manager_name = (
                    f"{manager_employee.first_name} {manager_employee.last_name}".strip()
                    if manager_employee else "People Manager"
                )
                manager = User(
                    username='manager',
                    password_hash=hash_password('manager'),
                    role='manager',
                    full_name=manager_name,
                    employee_id=manager_employee.id if manager_employee else None,
                )
                db.add(manager)
                db.flush()
                logger.info("Auth: seeded manager account")
            elif manager_employee:
                if manager.employee_id is None:
                    manager.employee_id = manager_employee.id
                if manager.full_name in ("People Manager", "Darius Muntean", ""):
                    manager.full_name = (
                        f"{manager_employee.first_name} {manager_employee.last_name}".strip()
                    )
                manager.role = 'manager'
                db.flush()

            existing_emp_ids = {
                u[0]
                for u in db.query(User.employee_id).filter(User.employee_id.isnot(None)).all()
            }
            used_usernames = {u[0] for u in db.query(User.username).all()}

            # ---- one manager login per team lead ---------------------------
            manager_accounts = 0
            for emp in employees:
                if emp.id not in lead_ids:
                    continue
                if emp.id in existing_emp_ids:
                    # Upgrade an existing employee login to manager scope.
                    user = db.query(User).filter(User.employee_id == emp.id).first()
                    if user and user.role == 'employee':
                        user.role = 'manager'
                    continue

                first = (emp.first_name or "").strip().lower()
                last = (emp.last_name or "").strip().lower()
                base = f"{first}_{last}" if first and last else (first or last)
                username = _unique_username(base, used_usernames, emp.id)
                used_usernames.add(username)
                existing_emp_ids.add(emp.id)
                db.add(User(
                    username=username,
                    password_hash=hash_password('manager'),
                    role='manager',
                    full_name=f"{emp.first_name or ''} {emp.last_name or ''}".strip(),
                    employee_id=emp.id,
                ))
                manager_accounts += 1

            # ---- remaining employees ---------------------------------------
            employee_accounts = 0
            for emp in employees:
                if emp.id in existing_emp_ids:
                    continue

                first = (emp.first_name or "").strip().lower()
                last = (emp.last_name or "").strip().lower()
                base = f"{first}_{last}" if first and last else (first or last)
                username = _unique_username(base, used_usernames, emp.id)
                used_usernames.add(username)
                existing_emp_ids.add(emp.id)

                full_name = (
                    f"{emp.first_name or ''} {emp.last_name or ''}".strip()
                    or username
                )
                db.add(User(
                    username=username,
                    password_hash=hash_password('employee'),
                    role='employee',
                    full_name=full_name,
                    employee_id=emp.id,
                ))
                employee_accounts += 1

            if manager_accounts or employee_accounts:
                db.flush()
                logger.info(
                    "Auth: synced %d manager and %d employee accounts from DB",
                    manager_accounts,
                    employee_accounts,
                )
        except Exception as e:
            db.rollback()
            logger.warning(f"Auth: employee sync failed (non-fatal): {e}")

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
    with get_sync_db() as db:
        user = db.query(User).filter(User.username == username).first()
        if user and verify_password(password, user.password_hash):
            return {
                "id": str(user.id),
                "username": user.username,
                "role": user.role,
                "full_name": user.full_name,
                "employee_id": str(user.employee_id) if user.employee_id else None,
                "cv_uri": user.cv_uri
            }
    return None

def get_user_by_id(user_id: str) -> dict | None:
    with get_sync_db() as db:
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        if user:
            return {
                "id": str(user.id),
                "username": user.username,
                "role": user.role,
                "full_name": user.full_name,
                "employee_id": str(user.employee_id) if user.employee_id else None,
                "cv_uri": user.cv_uri
            }
    return None

def change_password(user_id: str, old_password: str, new_password: str) -> bool:
    with get_sync_db() as db:
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        if not user or not verify_password(old_password, user.password_hash):
            return False
        user.password_hash = hash_password(new_password)
        db.commit()
        return True

def update_cv_uri(user_id: str, cv_uri: str) -> bool:
    with get_sync_db() as db:
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        if not user:
            return False
        user.cv_uri = cv_uri
        db.commit()
        return True

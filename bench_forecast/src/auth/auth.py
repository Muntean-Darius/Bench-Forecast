import hashlib
import jwt
import uuid
import logging
from datetime import datetime, timedelta, timezone

from src.database.database import get_sync_db
from src.database.models import User, Employee

logger = logging.getLogger(__name__)

SECRET_KEY = 'bench-forecast-secret-key-2026'
ALGORITHM = 'HS256'

def hash_password(password: str) -> str:
    return hashlib.sha256(('bench_forecast_' + password).encode()).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash

def init_auth_db() -> None:
    """Seed manager, and auto-create accounts for all employees in the main DB."""
    with get_sync_db() as db:
        # Seed manager if not exists
        manager = db.query(User).filter(User.username == 'manager').first()
        if not manager:
            manager = User(
                username='manager',
                password_hash=hash_password('manager'),
                role='manager',
                full_name='Darius Muntean'
            )
            db.add(manager)
            db.commit()
            logger.info("Auth: seeded manager account")
            
        # Sync employee accounts from employee database
        try:
            employees = db.query(Employee).all()
            added = 0
            for emp in employees:
                # Check if user already exists for this employee
                existing_user = db.query(User).filter(User.employee_id == emp.id).first()
                if existing_user:
                    continue
                
                parts = emp.name.strip().lower().split()
                if len(parts) >= 2:
                    username = parts[0] + '_' + parts[-1]
                else:
                    username = parts[0]
                
                # Check username collision
                collision = db.query(User).filter(User.username == username).first()
                if collision:
                    username = username + '_' + str(emp.id)[-4:]
                    
                new_user = User(
                    username=username,
                    password_hash=hash_password('employee'),
                    role='employee',
                    full_name=emp.name,
                    employee_id=emp.id
                )
                db.add(new_user)
                added += 1
                
            if added > 0:
                db.commit()
                logger.info(f"Auth: synced {added} employee accounts from DB")
        except Exception as e:
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

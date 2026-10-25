import uuid
from typing import Dict, Any, Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.auth.auth import verify_token, get_user_by_id

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
) -> Optional[Dict[str, Any]]:
    """Return the current user when a bearer token is supplied, else None."""
    if credentials is None:
        return None
    payload = verify_token(credentials.credentials)
    if not payload:
        return None
    return get_user_by_id(payload["sub"])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """Dependency to get the current authenticated user via JWT."""
    token = credentials.credentials
    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def has_role(user: Dict[str, Any], *roles: str) -> bool:
    return str(user.get("role") or "").lower() in {r.lower() for r in roles}


def is_admin(user: Dict[str, Any]) -> bool:
    return has_role(user, "admin")


def is_manager(user: Dict[str, Any]) -> bool:
    return has_role(user, "manager", "admin")


def manager_employee_id(user: Dict[str, Any]) -> str | None:
    value = user.get("employee_id")
    return str(value) if value else None


def require_roles(*roles: str):
    """FastAPI dependency factory enforcing the 3-tier role strategy."""

    async def _dependency(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        if not has_role(user, *roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.get('role')}' is not permitted; "
                    f"requires one of {sorted(roles)}."
                ),
            )
        return user

    return _dependency


def assert_can_access_employee(user: Dict[str, Any], employee_id: uuid.UUID) -> None:
    """Employees may access themselves; managers their direct reports; admins all."""
    if is_admin(user):
        return
    own = manager_employee_id(user)
    if user.get("role") == "employee" and own and str(employee_id) == own:
        return
    if user.get("role") == "manager" and own and str(employee_id) == own:
        return
    # Direct-report checks that need the DB are enforced by the routers/
    # services; this helper only covers the self case.
    if own and str(employee_id) == own:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this employee.",
    )


async def assert_can_manage_employee(
    session,
    user: Dict[str, Any],
    employee_id: uuid.UUID,
) -> None:
    """DB-backed scope check for managers and employees."""
    if is_admin(user):
        return
    own = manager_employee_id(user)
    if user.get("role") == "employee":
        if own and str(employee_id) == own:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employees may only access their own record.",
        )

    from src.database.models import Employee  # local import to avoid cycles

    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    if own and employee.manager_id and str(employee.manager_id) == own:
        return
    if own and str(employee_id) == own:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Managers may only manage their direct reports.",
    )


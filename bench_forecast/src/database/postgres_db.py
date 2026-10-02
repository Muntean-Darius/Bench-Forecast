"""PostgreSQL database manager for bench forecast operational records.

Translates between SQLAlchemy ORM models (models.py) and Pydantic schemas (schemas/models.py).
"""

from typing import List, Optional
from datetime import datetime, timedelta
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import (
    Employee as DBEmployee,
    ProjectRole as DBProjectRole,
    Project as DBProject,
    Allocation as DBAllocation,
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db
)
from src.schemas.models import Demand, Employee


logger = logging.getLogger(__name__)


class PostgresManager:
    """PostgreSQL relational database manager for deterministic records."""

    def __init__(self, db_url: Optional[str] = None) -> None:
        self.engine = create_db_engine(database_url=db_url)
        self.session_factory = create_session_factory(self.engine)
        init_db(self.engine)

    def get_bench_forecast(self, horizon_days: int = 90) -> List[Employee]:
        """Fetch employees who become available within the forecast horizon."""
        now = datetime.utcnow().date()
        cutoff = now + timedelta(days=horizon_days)

        employees = []
        with get_db_session(self.session_factory) as session:
            stmt = (
                select(DBEmployee)
                .where(DBEmployee.bench_start_date <= cutoff)
                .order_by(DBEmployee.bench_start_date.asc())
            )
            results = session.execute(stmt).scalars().all()

            for db_emp in results:
                skills_list = []
                if db_emp.skills:
                    skills_list = [s.strip() for s in db_emp.skills.split(",")]
                elif db_emp.primary_role:
                    skills_list = [db_emp.primary_role, db_emp.seniority]

                emp = Employee(
                    id=str(db_emp.id),
                    name=db_emp.full_name,
                    skills=skills_list,
                    current_project=db_emp.current_project,
                    available_from=db_emp.bench_start_date,
                    experience_years=float(db_emp.experience_years),
                    cost_rate=float(db_emp.hourly_cost_rate),
                    profile_text=db_emp.profile_text or "",
                )
                employees.append(emp)

        logger.info(
            f"Forecast query [{horizon_days}d horizon]: {len(employees)} employees "
            f"(available_from <= {cutoff})"
        )
        return employees

    def get_open_demands(self, min_win_probability: float = 0.75) -> List[Demand]:
        """Fetch open roles with win_probability above threshold."""
        prob_percent = int(min_win_probability * 100)
        demands = []

        with get_db_session(self.session_factory) as session:
            stmt = (
                select(DBProjectRole)
                .join(DBProject)
                .where(DBProject.probability >= prob_percent)
                .where(DBProjectRole.status == "Open")
                .order_by(DBProject.probability.desc())
            )
            results = session.execute(stmt).scalars().all()

            for role in results:
                req_skills = []
                if role.required_skills:
                    req_skills = [s.strip() for s in role.required_skills.split(",")]
                elif role.description:
                    req_skills = [role.description[:50]]

                demand = Demand(
                    id=str(role.id),
                    role=role.title,
                    required_skills=req_skills,
                    project_id=str(role.project_id),
                    start_date=role.start_date,
                    headcount=role.headcount,
                    win_probability=role.project.probability / 100.0,
                    description=role.description,
                )
                demands.append(demand)

        logger.info(
            f"Demands query [win_prob>={min_win_probability:.0%}]: {len(demands)} open roles"
        )
        return demands

    def update_allocation(
        self,
        recommendation_id: str,
        employee_id: str,
        target_project_id: str,
        role: str,
        match_score: float,
        approved_by: str = "system",
    ) -> str:
        """Execute an approved allocation (update operational records)."""
        import uuid
        allocation_id_uuid = uuid.uuid4()
        now = datetime.utcnow().date()
        
        with get_db_session(self.session_factory) as session:
            # 1. Find the target role ID
            stmt = select(DBProjectRole).where(
                DBProjectRole.project_id == target_project_id,
                DBProjectRole.title == role,
                DBProjectRole.status == "Open"
            ).limit(1)
            target_role = session.execute(stmt).scalar_one_or_none()
            
            if not target_role:
                # If exact role not found by title, try just getting any open role in the project
                stmt2 = select(DBProjectRole).where(
                    DBProjectRole.project_id == target_project_id,
                    DBProjectRole.status == "Open"
                ).limit(1)
                target_role = session.execute(stmt2).scalar_one_or_none()
                
                if not target_role:
                    logger.warning(f"Could not find matching ProjectRole for {target_project_id}. Creating fallback role.")
                    target_role = DBProjectRole(
                        id=uuid.uuid4(),
                        project_id=uuid.UUID(target_project_id),
                        title=role,
                        target_bill_rate=100.0,
                        status="Open",
                        start_date=now,
                        headcount=1
                    )
                    session.add(target_role)
                    session.flush()

            # 2. Update employee's current project
            stmt3 = select(DBEmployee).where(DBEmployee.id == employee_id)
            emp = session.execute(stmt3).scalar_one_or_none()
            
            if emp:
                emp.current_project = str(target_project_id)
                # Compute projected margin
                from src.database.models import compute_projected_margin
                try:
                    margin = compute_projected_margin(target_role.target_bill_rate, emp.hourly_cost_rate)
                except ValueError:
                    margin = 0.0
            else:
                margin = 0.0
            
            # 3. Create allocation
            alloc = DBAllocation(
                id=allocation_id_uuid,
                employee_id=uuid.UUID(employee_id),
                project_role_id=target_role.id,
                start_date=now,
                end_date=now + timedelta(days=90), # Fallback end date
                projected_margin=margin
            )
            session.add(alloc)

            # 4. Mark role as Filled
            target_role.status = "Filled"
            
            logger.info(
                f"Allocation executed: {employee_id} → {target_project_id} (approver: {approved_by})"
            )

        return str(allocation_id_uuid)


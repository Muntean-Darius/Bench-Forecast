"""SQLite database manager for bench forecast mock data and operational records.

Schema:
- employees: Bench talent with skills, availability, and project_end_date (forecast trigger)
- demands:   Open roles with required skills and win_probability (pipeline filter)
- allocations: Audit trail of approved reallocation decisions
"""

from typing import Any, List, Optional
import sqlite3
from datetime import datetime, timedelta
import logging
from src.schemas.models import Demand, Employee


logger = logging.getLogger(__name__)


class SQLiteManager:
    """SQLite relational database manager for deterministic records."""

    def __init__(self, db_path: str = "./data/mock_db.sqlite") -> None:
        self.db_path = db_path
        self._init_schema()
        self._load_mock_data()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    skills TEXT NOT NULL,
                    current_project TEXT,
                    available_from TEXT NOT NULL,
                    experience_years REAL NOT NULL,
                    cost_rate REAL,
                    profile_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS demands (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    required_skills TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    headcount INTEGER NOT NULL,
                    win_probability REAL NOT NULL DEFAULT 0.0,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS allocations (
                    id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL,
                    target_project_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    match_score REAL NOT NULL,
                    recommendation_id TEXT NOT NULL,
                    approved_by TEXT,
                    approved_at TEXT,
                    executed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (employee_id) REFERENCES employees(id)
                )
            """)

            conn.commit()
            logger.info(f"Database schema initialized: {self.db_path}")

    def _load_mock_data(self) -> None:
        """Load mock employee and demand data into database.

        Employees have project_end_date set — this is the temporal forecast trigger.
        Only employees finishing within the configured horizon (30-90 days) are matched.
        Demands have win_probability set to filter low-confidence pipeline opportunities.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM employees")
            if cursor.fetchone()[0] > 0:
                logger.info("Mock data already exists, skipping load")
                return

            now = datetime.utcnow().isoformat()
            today = datetime.utcnow()

            mock_employees = [
                (
                    "EMP001", "Alice Johnson",
                    "Python,FastAPI,PostgreSQL,LLMs,Docker",
                    "Project-Alpha", (today + timedelta(days=30)).date().isoformat(),
                    8.5, 95.0,
                    """Alice is a senior backend engineer with expertise in building scalable APIs.
She has shipped 3 major microservices using FastAPI and PostgreSQL. Strong in Python ecosystem.
Recently led a team project integrating LLMs into production systems via LangChain and ChromaDB.
Experience with Docker, Kubernetes for deployment. Available in 30 days when Project-Alpha wraps up.""",
                    now, now,
                ),
                (
                    "EMP002", "Bob Chen",
                    "JavaScript,React,TypeScript,Node.js,AWS",
                    "Project-Beta", (today + timedelta(days=45)).date().isoformat(),
                    6.0, 75.0,
                    """Bob is a full-stack engineer comfortable with modern JavaScript tooling.
Expert in React for complex UIs, TypeScript for type safety. Built several Node.js backends.
AWS experience includes Lambda, DynamoDB, S3 deployments.
Interested in transitioning to AI/ML adjacent backend roles. Project-Beta ends in 45 days.""",
                    now, now,
                ),
                (
                    "EMP003", "Carol Williams",
                    "Java,Spring Boot,Kubernetes,Microservices,CI/CD",
                    "Project-Gamma", (today + timedelta(days=60)).date().isoformat(),
                    10.0, 110.0,
                    """Carol is a principal engineer with deep expertise in enterprise Java applications.
Led the architectural redesign of legacy monolith to microservices using Spring Boot and Apache Kafka.
Expert in Kubernetes orchestration, CI/CD pipelines, DevOps practices.
Available for tech lead or architect roles. Project-Gamma ends in 60 days.""",
                    now, now,
                ),
                (
                    "EMP004", "David Patel",
                    "Python,Machine Learning,TensorFlow,Data Analysis,R",
                    None, today.date().isoformat(),
                    5.5, 80.0,
                    """David is a machine learning engineer with strong Python skills.
Experience with TensorFlow, scikit-learn for model development and deployment.
Skilled in statistical analysis, data visualization, R for exploratory analysis.
Passionate about LLMs and prompt engineering. Currently between projects — available immediately.""",
                    now, now,
                ),
            ]

            cursor.executemany(
                """INSERT INTO employees
                   (id, name, skills, current_project, available_from,
                    experience_years, cost_rate, profile_text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                mock_employees,
            )

            mock_demands = [
                (
                    "DEM001", "Senior Backend Engineer - AI Platform",
                    "Python,FastAPI,LLMs,PostgreSQL,Docker",
                    "Project-Delta", today.date().isoformat(), 1, 0.88,
                    """Building the backend API for our new AI-powered workforce platform.
Need an engineer experienced with modern Python web frameworks (FastAPI preferred).
Must have production experience integrating LLMs into applications via LangChain or similar.
Database design and Docker containerization required. This role touches core allocation logic.""",
                    now, now,
                ),
                (
                    "DEM002", "Full Stack Engineer - UI/UX Dashboard",
                    "React,TypeScript,Node.js,API Design,AWS",
                    "Project-Echo", today.date().isoformat(), 1, 0.92,
                    """Frontend and backend engineer for our allocation recommendation dashboard.
React + TypeScript for the UI, Node.js/Express for lightweight backend services.
Must design clean APIs and deploy to AWS. Good UI/UX sensibility needed.""",
                    now, now,
                ),
                (
                    "DEM003", "Tech Lead - Microservices Architecture",
                    "Java,Kubernetes,Microservices,Architecture,Spring Boot",
                    "Project-Foxtrot", (today + timedelta(days=14)).date().isoformat(), 1, 0.82,
                    """Lead architect role for refactoring our allocation matching service into scalable microservices.
Strong Java and Spring Boot experience required. Kubernetes orchestration expertise critical.
Will mentor 2 junior engineers. Strategic role shaping platform evolution.""",
                    now, now,
                ),
                (
                    "DEM004", "ML Engineer - Recommendation Ranking",
                    "Python,Machine Learning,LLMs,Data Science,TensorFlow",
                    "Project-Golf", today.date().isoformat(), 1, 0.78,
                    """ML engineer to build ranking models for allocation recommendations.
Python expertise with ML frameworks (TensorFlow, scikit-learn). LLM experience a plus.
Will work on feature engineering, model evaluation, experimentation.
Goal is to improve match_score predictions with learned models.""",
                    now, now,
                ),
            ]

            cursor.executemany(
                """INSERT INTO demands
                   (id, role, required_skills, project_id, start_date, headcount,
                    win_probability, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                mock_demands,
            )

            conn.commit()
            logger.info(
                f"Mock data loaded: {len(mock_employees)} employees, {len(mock_demands)} demands"
            )

    def get_bench_forecast(self, horizon_days: int = 90) -> List[Employee]:
        """Fetch employees who become available within the forecast horizon.

        Queries available_from in [now, now+horizon_days] — employees finishing
        current projects inside the window — plus anyone already on bench
        (available_from <= now).

        Args:
            horizon_days: Look-ahead window in days (default 90)

        Returns:
            List of Employee objects available within the horizon, sorted soonest first
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            now_str = datetime.utcnow().isoformat()
            cutoff_str = (datetime.utcnow() + timedelta(days=horizon_days)).isoformat()

            # Include employees already on bench AND those becoming free within the window
            cursor.execute(
                """SELECT * FROM employees
                   WHERE available_from <= ?
                   ORDER BY available_from ASC""",
                (cutoff_str,),
            )

            rows = cursor.fetchall()
            employees = []
            for row in rows:
                emp = Employee(
                    id=row["id"],
                    name=row["name"],
                    skills=[s.strip() for s in row["skills"].split(",")],
                    current_project=row["current_project"],
                    available_from=datetime.fromisoformat(row["available_from"]).date(),
                    experience_years=row["experience_years"],
                    cost_rate=row["cost_rate"],
                    profile_text=row["profile_text"],
                )
                employees.append(emp)

            logger.info(
                f"Forecast query [{horizon_days}d horizon]: {len(employees)} employees "
                f"(available_from <= {cutoff_str[:10]})"
            )
            return employees

    def get_bench_employees(self) -> List[Employee]:
        """Legacy: fetch all employees currently available (available_from <= now)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                "SELECT * FROM employees WHERE available_from <= ? ORDER BY available_from ASC",
                (now,),
            )
            rows = cursor.fetchall()
            employees = []
            for row in rows:
                emp = Employee(
                    id=row["id"],
                    name=row["name"],
                    skills=[s.strip() for s in row["skills"].split(",")],
                    current_project=row["current_project"],
                    available_from=datetime.fromisoformat(row["available_from"]).date(),
                    experience_years=row["experience_years"],
                    cost_rate=row["cost_rate"],
                    profile_text=row["profile_text"],
                )
                employees.append(emp)
            return employees

    def get_open_demands(self, min_win_probability: float = 0.75) -> List[Demand]:
        """Fetch open roles with win_probability above threshold.

        Only high-confidence pipeline opportunities are matched against
        bench employees — avoids allocating talent to deals that may not close.

        Args:
            min_win_probability: Minimum pipeline win probability [0.0, 1.0].
                                  Default 0.75 (filter deals with <75% win chance).

        Returns:
            List of Demand objects for high-confidence positions
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM demands WHERE win_probability >= ? ORDER BY win_probability DESC",
                (min_win_probability,),
            )
            rows = cursor.fetchall()

            demands = []
            for row in rows:
                demand = Demand(
                    id=row["id"],
                    role=row["role"],
                    required_skills=[s.strip() for s in row["required_skills"].split(",")],
                    project_id=row["project_id"],
                    start_date=datetime.fromisoformat(row["start_date"]).date(),
                    headcount=row["headcount"],
                    win_probability=row["win_probability"],
                    description=row["description"],
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
        """Execute an approved allocation (update operational records).

        Args:
            recommendation_id: ID of recommendation being approved
            employee_id: Employee to reallocate
            target_project_id: Target project ID
            role: New role
            match_score: Match score for audit trail
            approved_by: User who approved (human from HITL)

        Returns:
            Allocation record ID
        """
        import uuid

        allocation_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute(
                """INSERT INTO allocations
                   (id, employee_id, target_project_id, role, match_score, recommendation_id,
                    approved_by, approved_at, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    allocation_id, employee_id, target_project_id, role,
                    match_score, recommendation_id, approved_by, now, "executed", now,
                ),
            )

            # Deterministic DB update: move employee to new project
            cursor.execute(
                "UPDATE employees SET current_project = ?, updated_at = ? WHERE id = ?",
                (target_project_id, now, employee_id),
            )

            conn.commit()
            logger.info(
                f"Allocation executed: {employee_id} → {target_project_id} (approver: {approved_by})"
            )

        return allocation_id

    def get_allocation_history(self, employee_id: Optional[str] = None) -> List[dict]:
        """Fetch allocation audit trail.

        Args:
            employee_id: Filter by employee (None = all)

        Returns:
            List of allocation records
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if employee_id:
                cursor.execute(
                    "SELECT * FROM allocations WHERE employee_id = ? ORDER BY created_at DESC",
                    (employee_id,),
                )
            else:
                cursor.execute("SELECT * FROM allocations ORDER BY created_at DESC")

            return [dict(row) for row in cursor.fetchall()]

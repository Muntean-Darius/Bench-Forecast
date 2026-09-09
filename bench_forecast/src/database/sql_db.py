"""SQLite database manager for bench forecast mock data and operational records.

Schema:
- employees: Bench talent with skills and availability
- demands: Open roles with required skills
- allocations: Audit trail of approved reallocation decisions
"""

from typing import Any, List
import sqlite3
from datetime import datetime, timedelta
import logging
from src.schemas.models import Demand, Employee


logger = logging.getLogger(__name__)


class SQLiteManager:
    """SQLite relational database manager for deterministic records."""

    def __init__(self, db_path: str = "./data/mock_db.sqlite") -> None:
        """Initialize database connection and create schema if needed.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_schema()
        self._load_mock_data()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Employees table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    skills TEXT NOT NULL,
                    current_project TEXT,
                    available_from TEXT NOT NULL,
                    experience_years REAL NOT NULL,
                    profile_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Demands table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS demands (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    required_skills TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    headcount INTEGER NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Allocations audit trail table
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
        
        This satisfies the requirement to mock data without waiting for Person 2.
        Mock employees have ending projects, mock demands represent open roles.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if data already exists
            cursor.execute("SELECT COUNT(*) FROM employees")
            if cursor.fetchone()[0] > 0:
                logger.info("Mock data already exists, skipping load")
                return
            
            now = datetime.utcnow().isoformat()
            available_today = datetime.utcnow().isoformat()
            available_next_week = (datetime.utcnow() + timedelta(days=7)).isoformat()
            
            # Mock employees (bench talent with available_from dates)
            mock_employees = [
                (
                    "EMP001",
                    "Alice Johnson",
                    "Python,FastAPI,PostgreSQL,LLMs,Docker",
                    "Project-Alpha",
                    available_today,
                    8.5,
                    """Alice is a senior backend engineer with expertise in building scalable APIs.
She has shipped 3 major microservices using FastAPI and PostgreSQL. Strong in Python ecosystem.
Recently led a team project integrating LLMs into production systems.
Experience with Docker, Kubernetes for deployment. Looking for new challenges in AI infrastructure.""",
                    now,
                    now,
                ),
                (
                    "EMP002",
                    "Bob Chen",
                    "JavaScript,React,TypeScript,Node.js,AWS",
                    "Project-Beta",
                    available_next_week,
                    6.0,
                    """Bob is a full-stack engineer comfortable with modern JavaScript tooling.
Expert in React for complex UIs, TypeScript for type safety. Built several Node.js backends.
AWS experience includes Lambda, DynamoDB, S3 deployments.
Interested in transitioning to AI/ML adjacent backend roles.""",
                    now,
                    now,
                ),
                (
                    "EMP003",
                    "Carol Williams",
                    "Java,Spring Boot,Kubernetes,Microservices,CI/CD",
                    "Project-Gamma",
                    available_today,
                    10.0,
                    """Carol is a principal engineer with deep expertise in enterprise Java applications.
Led the architectural redesign of legacy monolith to microservices using Spring Boot.
Expert in Kubernetes orchestration, CI/CD pipelines, DevOps practices.
Interested in tech lead or architect roles. Available immediately from current project.""",
                    now,
                    now,
                ),
                (
                    "EMP004",
                    "David Patel",
                    "Python,Machine Learning,TensorFlow,Data Analysis,R",
                    None,
                    available_today,
                    5.5,
                    """David is a machine learning engineer with strong Python skills.
Experience with TensorFlow, scikit-learn for model development.
Skilled in statistical analysis, data visualization, R for exploratory analysis.
Passionate about LLMs and prompt engineering. Available immediately.""",
                    now,
                    now,
                ),
            ]
            
            cursor.executemany(
                """INSERT INTO employees 
                   (id, name, skills, current_project, available_from, experience_years, profile_text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                mock_employees,
            )
            
            # Mock demands (open roles)
            mock_demands = [
                (
                    "DEM001",
                    "Senior Backend Engineer - AI Platform",
                    "Python,FastAPI,LLMs,PostgreSQL,Docker",
                    "Project-Delta",
                    available_today,
                    1,
                    """Building the backend API for our new AI-powered workforce platform.
Need an engineer experienced with modern Python web frameworks (FastAPI preferred).
Must have production experience integrating LLMs into applications.
Database design and Docker containerization required. This role touches core allocation logic.""",
                    now,
                    now,
                ),
                (
                    "DEM002",
                    "Full Stack Engineer - UI/UX Dashboard",
                    "React,TypeScript,Node.js,API Design,AWS",
                    "Project-Echo",
                    available_today,
                    1,
                    """Frontend and backend engineer for our allocation recommendation dashboard.
React + TypeScript for the UI, Node.js/Express for lightweight backend services.
Must design clean APIs and deploy to AWS. Good UI/UX sensibility needed.""",
                    now,
                    now,
                ),
                (
                    "DEM003",
                    "Tech Lead - Microservices Architecture",
                    "Java,Kubernetes,Microservices,Architecture,Spring Boot",
                    "Project-Foxtrot",
                    available_next_week,
                    1,
                    """Lead architect role for refactoring our allocation matching service into scalable microservices.
Strong Java and Spring Boot experience required. Kubernetes orchestration expertise critical.
Will mentor 2 junior engineers. This is a strategic role shaping our platform evolution.""",
                    now,
                    now,
                ),
                (
                    "DEM004",
                    "ML Engineer - Recommendation Ranking",
                    "Python,Machine Learning,LLMs,Data Science,TensorFlow",
                    "Project-Golf",
                    available_today,
                    1,
                    """ML engineer to build ranking models for allocation recommendations.
Python expertise with ML frameworks (TensorFlow, scikit-learn). LLM experience a plus.
Will work on feature engineering, model evaluation, experimentation.
Goal is to improve match_score predictions with learned models.""",
                    now,
                    now,
                ),
            ]
            
            cursor.executemany(
                """INSERT INTO demands 
                   (id, role, required_skills, project_id, start_date, headcount, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                mock_demands,
            )
            
            conn.commit()
            logger.info(
                f"Mock data loaded: {len(mock_employees)} employees, {len(mock_demands)} demands"
            )

    def get_bench_employees(self) -> List[Employee]:
        """Fetch all employees currently on bench (available_from <= now).
        
        Returns:
            List of Employee objects ready for allocation
        """
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
                    profile_text=row["profile_text"],
                )
                employees.append(emp)
            
            return employees

    def get_open_demands(self) -> List[Demand]:
        """Fetch all open positions.
        
        Returns:
            List of Demand objects for active positions
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM demands ORDER BY start_date ASC")
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
                    description=row["description"],
                )
                demands.append(demand)
            
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
            
            # Insert allocation record (audit trail)
            cursor.execute(
                """INSERT INTO allocations 
                   (id, employee_id, target_project_id, role, match_score, recommendation_id, 
                    approved_by, approved_at, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    allocation_id,
                    employee_id,
                    target_project_id,
                    role,
                    match_score,
                    recommendation_id,
                    approved_by,
                    now,
                    "executed",
                    now,
                ),
            )
            
            # Update employee's current_project (this is the "deterministic" update)
            cursor.execute(
                "UPDATE employees SET current_project = ?, updated_at = ? WHERE id = ?",
                (target_project_id, now, employee_id),
            )
            
            conn.commit()
            logger.info(
                f"Allocation executed: {employee_id} -> {target_project_id} (approval: {approved_by})"
            )
        
        return allocation_id

    def get_allocation_history(self, employee_id: str = None) -> List[dict]:
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


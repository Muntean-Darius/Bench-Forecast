#!/usr/bin/env python3
"""Seed script — populates ChromaDB (RAG) and SQLite from the updated mock_data.json.

Run from the bench_forecast/ directory:
    python3 seed.py

What it does:
  1. Wipes and re-seeds ChromaDB with the new employee CV chunks + financial metadata.
  2. Wipes and re-seeds the SQLite legacy database (employees, demands) so the
     existing API endpoints keep working during the PostgreSQL migration phase.
  3. Prints a financial margin preview table for every project_role.
"""

import json
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seed")

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
BENCH_FORECAST_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCH_FORECAST_DIR / "data"
MOCK_DATA_PATH = DATA_DIR / "mock_data.json"

sys.path.insert(0, str(BENCH_FORECAST_DIR))

# ---------------------------------------------------------------------------
# Load mock data
# ---------------------------------------------------------------------------
logger.info(f"Loading mock data from {MOCK_DATA_PATH}")
with open(MOCK_DATA_PATH, encoding="utf-8") as f:
    data = json.load(f)

employees    = data.get("employees", [])
projects     = data.get("projects", [])
roles        = data.get("project_roles", [])

logger.info(f"  {len(employees)} employees | {len(projects)} projects | {len(roles)} project_roles")


# ===========================================================================
# 1. Seed ChromaDB via RAGPipeline
# ===========================================================================
logger.info("\n── ChromaDB ────────────────────────────────────────────────────")

from src.database.rag import RAGPipeline

# Wipe existing collection and start fresh
import chromadb
chroma_dir = str(DATA_DIR / "chroma_store")
client = chromadb.PersistentClient(path=chroma_dir)

# Delete and recreate the collection for a clean seed
COLLECTION_NAME = "employee_cvs"
try:
    client.delete_collection(COLLECTION_NAME)
    logger.info(f"  Deleted existing collection '{COLLECTION_NAME}'")
except Exception:
    pass  # Collection didn't exist yet

rag = RAGPipeline(persist_dir=chroma_dir, collection_name=COLLECTION_NAME)
total_chunks = rag.bulk_ingest(employees)
logger.info(f"  ✓ Indexed {total_chunks} chunks for {len(employees)} employees")

# Quick sanity check — financially filtered retrieval test
test_candidates = rag.retrieve_candidates(
    role_description="Python FastAPI LangChain LLM backend developer with RAG experience",
    target_bill_rate=95.0,
    k=3,
)
logger.info(f"  Sanity-check retrieval (bill_rate=95, cost<95): {len(test_candidates)} candidates")
for c in test_candidates:
    logger.info(
        f"    [{c['employee_id'][:8]}…] {c['full_name']:25s} "
        f"cost=${c['hourly_cost_rate']:5.2f}/h  "
        f"margin={c['projected_margin_pct']:5.1f}%  "
        f"sim={c['similarity_score']:.3f}"
    )


# ===========================================================================
# 2. Seed SQLite (legacy layer — keeps existing API endpoints working)
# ===========================================================================
logger.info("\n── SQLite (legacy) ─────────────────────────────────────────────")

import sqlite3
from datetime import datetime

SQLITE_PATH = str(DATA_DIR / "mock_db.sqlite")
now = datetime.utcnow().isoformat()

with sqlite3.connect(SQLITE_PATH) as conn:
    cur = conn.cursor()

    # Wipe existing data
    cur.execute("DELETE FROM allocations")
    cur.execute("DELETE FROM demands")
    cur.execute("DELETE FROM employees")
    logger.info("  Cleared existing SQLite rows")

    # Seed employees
    emp_rows = []
    for e in employees:
        skills_str = ", ".join(e.get("skills", [])) if "skills" in e else e.get("primary_role", "")
        # Build a skills string from role + seniority for legacy layer
        legacy_skills = f"{e['primary_role']}, {e['seniority']}"
        emp_rows.append((
            e["id"],
            e["full_name"],          # name
            legacy_skills,           # skills (comma-separated, legacy format)
            None,                    # current_project
            e["bench_start_date"],   # available_from
            8.0,                     # experience_years (placeholder)
            e["hourly_cost_rate"],   # cost_rate
            e["profile_text"],       # profile_text
            now,                     # created_at
            now,                     # updated_at
        ))

    cur.executemany(
        """INSERT OR REPLACE INTO employees
           (id, name, skills, current_project, available_from,
            experience_years, cost_rate, profile_text, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        emp_rows,
    )
    logger.info(f"  ✓ Inserted {len(emp_rows)} employees into SQLite")

    # Seed demands from project_roles
    demand_rows = []
    for r in roles:
        demand_rows.append((
            r["id"],
            r["title"],                      # role
            r.get("description", "")[:100],  # required_skills placeholder
            r["project_id"],                 # project_id
            "2026-10-01",                    # start_date
            1,                               # headcount
            0.85,                            # win_probability (default high)
            r.get("description", ""),        # description
            now,
            now,
        ))

    cur.executemany(
        """INSERT OR REPLACE INTO demands
           (id, role, required_skills, project_id, start_date, headcount,
            win_probability, description, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        demand_rows,
    )
    logger.info(f"  ✓ Inserted {len(demand_rows)} demands into SQLite")
    conn.commit()


# ===========================================================================
# 3. Financial Margin Preview Table
# ===========================================================================
logger.info("\n── Financial Margin Preview ─────────────────────────────────────")
logger.info(f"  {'Role':<45} {'Bill':>7}  {'Cost':>7}  {'Margin':>8}  {'Viable'}")
logger.info(f"  {'-'*45} {'-'*7}  {'-'*7}  {'-'*8}  {'-'*6}")

emp_by_id = {e["id"]: e for e in employees}

for role in roles:
    bill = float(role["target_bill_rate"])
    # Find cheapest viable employee for this role (for illustration)
    viable = [e for e in employees if float(e["hourly_cost_rate"]) < bill]
    if viable:
        cheapest = min(viable, key=lambda e: float(e["hourly_cost_rate"]))
        cost = float(cheapest["hourly_cost_rate"])
        margin = (bill - cost) / bill * 100
        viable_str = f"✓ ({cheapest['full_name'].split()[0]})"
    else:
        cost = 0
        margin = 0
        viable_str = "✗ NONE"

    logger.info(
        f"  {role['title']:<45} ${bill:6.2f}  ${cost:6.2f}  {margin:7.1f}%  {viable_str}"
    )

logger.info("\n── PostgreSQL ──────────────────────────────────────────────────")
from src.database.models import (
    create_db_engine,
    create_session_factory,
    init_db,
    get_db_session,
    Employee as DBEmployee,
    Project as DBProject,
    ProjectRole as DBProjectRole,
)
import uuid
from decimal import Decimal

engine = create_db_engine()
init_db(engine)
session_factory = create_session_factory(engine)

with get_db_session(session_factory) as session:
    # Clear existing data
    from src.database.models import Allocation as DBAllocation
    session.execute(DBAllocation.__table__.delete())
    session.execute(DBProjectRole.__table__.delete())
    session.execute(DBProject.__table__.delete())
    session.execute(DBEmployee.__table__.delete())
    logger.info("  Cleared existing PostgreSQL rows")

    def safe_uuid(id_str):
        if id_str.startswith('p'):
            return uuid.UUID('f' + id_str[1:])
        if id_str.startswith('r'):
            return uuid.UUID('e' + id_str[1:])
        return uuid.UUID(id_str)

    # Seed Projects
    for p in projects:
        project = DBProject(
            id=safe_uuid(p["id"]),
            name=p["name"],
            status=p["status"],
            probability=p["probability"],
            target_margin=Decimal(str(p["target_margin"])),
            total_budget=Decimal(str(p["total_budget"]))
        )
        session.add(project)
    
    # Seed Project Roles
    for r in roles:
        role = DBProjectRole(
            id=safe_uuid(r["id"]),
            project_id=safe_uuid(r["project_id"]),
            title=r["title"],
            target_bill_rate=Decimal(str(r["target_bill_rate"])),
            status=r["status"],
            description=r.get("description", ""),
            start_date=datetime.strptime("2026-10-01", "%Y-%m-%d").date(),
            headcount=1,
            required_skills=r.get("description", "")[:100]
        )
        session.add(role)

    # Seed Employees
    for e in employees:
        emp = DBEmployee(
            id=safe_uuid(e["id"]),
            full_name=e["full_name"],
            primary_role=e["primary_role"],
            seniority=e["seniority"],
            hourly_cost_rate=Decimal(str(e["hourly_cost_rate"])),
            bench_start_date=datetime.strptime(e["bench_start_date"], "%Y-%m-%d").date(),
            cv_document_uri=e.get("cv_document_uri", ""),
            profile_text=e.get("profile_text", ""),
            skills=f"{e['primary_role']}, {e['seniority']}",
            experience_years=8.0,
            current_project=None
        )
        session.add(emp)

    logger.info(f"  ✓ Inserted {len(projects)} projects, {len(roles)} roles, {len(employees)} employees into PostgreSQL")

logger.info("\n✅ Seed complete — ChromaDB + SQLite + PostgreSQL are ready for use.")

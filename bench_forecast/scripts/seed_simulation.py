"""Bench Forecast — full demo data seeder.

Everything about an employee is derived from their CV text:

* ``skills``            — extracted from the CV (deterministic vocabulary match).
* ``job_title``         — deduced from the extracted skills (role taxonomy).
* ``experience_level``  — deduced from years of experience / seniority wording.
* ``profile_text``      — the raw CV text.
* salary / cost rate    — deduced from the experience level (salary bands) with
                          a small role multiplier.

The seeder then builds meaningful projects (completed history, active work and
upcoming pipeline), staffs them with employees (which produces bench history),
and generates financial + bench-burn data so the dashboard has content.

Run from ``bench_forecast`` (requires Ollama for embeddings):

    ./venv/bin/python scripts/seed_simulation.py
    SEED_EMBEDDINGS=false ./venv/bin/python scripts/seed_simulation.py   # fast, no ChromaDB

WARNING: this script RESETS the domain tables (employees, projects, allocations,
financials, …). It keeps nothing.
"""

import os
import random
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import requests
from faker import Faker
from sqlalchemy import select, text

from demo_catalog import (
    OPEN_DEMANDS,
    PRIMARY_PROJECT_NAME,
    PROJECT_BLUEPRINTS,
    SALARY_BANDS,
    infer_role_from_skills,
    title_with_seniority,
)
from src.agents.nodes import _extract_skills_from_text
from src.core.config import Config
from src.database.database import SyncSessionLocal, init_db_sync
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchCost,
    BenchHistory,
    BenchStatusEnum,
    CvApprovalStatusEnum,
    CvReviewRequest,
    DemandStatusEnum,
    Department,
    Document,
    DocumentTypeEnum,
    Employee,
    EmployeeCost,
    EmployeeSkill,
    EmployeeSkillEvidence,
    EmploymentTypeEnum,
    ExperienceLevelEnum,
    ForecastRun,
    HitlDecisionEnum,
    HitlReviewEvent,
    IngestionStatusEnum,
    KpiSnapshot,
    Project,
    ProjectDemand,
    ProficiencyLevelEnum,
    Skill,
    SkillOriginEnum,
    TrainingAssignment,
    TrainingStatusEnum,
    User,
)
from src.services.finance import compute_internal_costs

fake = Faker()
random.seed(42)
Faker.seed(42)

OLLAMA_EMBED_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/embeddings"
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
SEED_EMBEDDINGS = os.getenv("SEED_EMBEDDINGS", "true").lower() in ("1", "true", "yes")

DEPARTMENTS = ["Software Engineering", "Data & AI", "Cloud & DevOps", "Cyber Security", "QA & Testing"]

ROLE_MULTIPLIER = {
    "data scientist": 1.20,
    "machine learning": 1.22,
    "cloud": 1.15,
    "devops": 1.15,
    "sre": 1.18,
    "security": 1.15,
    "cyber": 1.15,
    "java": 1.10,
    "backend": 1.10,
    "data engineer": 1.12,
    "frontend": 1.05,
    "qa": 0.95,
    "analyst": 0.98,
    "sap": 1.08,
    ".net": 1.05,
    "mobile": 1.05,
}


# ===========================================================================
# Stage 0 — reset
# ===========================================================================

def reset_database(session) -> None:
    """Delete all domain rows so the seed starts from a clean slate."""
    for table in [
        "email_notifications", "training_assignments", "cv_review_requests",
        "employee_skill_evidence", "hitl_review_events", "allocations",
        "hiring_recommendations", "documents", "employee_skills",
        "bench_costs", "bench_history", "employee_costs", "kpi_snapshots",
        "forecast_runs", "project_demands", "project_roles", "projects",
        "employees", "departments", "skills", "users",
    ]:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    print("Stage 0: database reset")


# ===========================================================================
# Stage 1 — employees derived from their CVs
# ===========================================================================

_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)\b", re.IGNORECASE)


def deduce_experience_level(resume_text: str) -> ExperienceLevelEnum:
    """Deduce seniority from the CV (explicit years first, then wording)."""
    text = resume_text or ""
    years = [int(x) for x in _YEARS_RE.findall(text)]
    if years:
        y = max(years)
        if y < 2:
            return ExperienceLevelEnum.junior
        if y < 5:
            return ExperienceLevelEnum.mid
        if y < 9:
            return ExperienceLevelEnum.senior
        if y < 14:
            return ExperienceLevelEnum.lead
        return ExperienceLevelEnum.principal

    low = text.lower()
    if re.search(r"\b(intern|trainee|graduate|entry[- ]level)\b", low):
        return ExperienceLevelEnum.junior
    if re.search(r"\bjunior\b", low):
        return ExperienceLevelEnum.junior
    if re.search(r"\bprincipal\b|\barchitect\b|\bhead of\b|\bdirector\b", low):
        return ExperienceLevelEnum.principal
    if re.search(r"\blead\b|\bmanager\b", low):
        return ExperienceLevelEnum.lead
    if re.search(r"\bsenior\b|\bsr\.?\b", low):
        return ExperienceLevelEnum.senior
    return ExperienceLevelEnum.mid


def deduce_salary(level: ExperienceLevelEnum, title: str) -> Decimal:
    """Salary band by seniority, nudged by a role multiplier."""
    lo, hi = SALARY_BANDS[level.value]
    multiplier = Decimal("1.00")
    low_title = (title or "").lower()
    for key, mult in ROLE_MULTIPLIER.items():
        if key in low_title:
            multiplier = Decimal(str(mult))
            break
    base = Decimal(str(round(random.uniform(lo, hi), 2)))
    return (base * multiplier).quantize(Decimal("0.01"))


def stage1_employees(session) -> list:
    """Load CVs, extract skills, deduce title/level/salary, persist employees."""
    print("Stage 1: employees derived from CVs")
    from datasets import load_dataset

    tech_categories = {
        "data-science", "web-designing", "java-developer", "sap-developer",
        "automation-testing", "python-developer", "devops-engineer",
        "network-security-engineer", "database", "hadoop", "etl-developer",
        "dotnet-developer", "blockchain", "testing", "information-technology",
    }
    ds = load_dataset("Divyaamith/Kaggle-Resume", split="train")
    tech = [r for r in ds if str(r.get("Category", "")).lower().strip() in tech_categories]
    random.shuffle(tech)
    tech = tech[:100]

    depts = {}
    for name in DEPARTMENTS:
        dept = Department(name=name, head_count_budget=40)
        session.add(dept)
        depts[name] = dept
    session.flush()
    dept_by_name = {name: dept.id for name, dept in depts.items()}

    emps = []
    for row in tech:
        resume = (row.get("Resume_str") or "").strip()
        if not resume:
            continue
        category = str(row.get("Category") or "")

        # --- everything derived from the CV ---
        skills = _extract_skills_from_text(resume)[:25]
        level = deduce_experience_level(resume)
        base_title, dept_name = infer_role_from_skills(skills, category)
        title = title_with_seniority(base_title, level.value)
        salary = deduce_salary(level, title)
        daily, hourly = compute_internal_costs(salary, Decimal("1.35"))

        # ~78% full-time, ~17% part-time (4h/day), ~5% contractors (8h/day)
        employment_type = random.choices(
            [
                EmploymentTypeEnum.full_time,
                EmploymentTypeEnum.part_time,
                EmploymentTypeEnum.contractor,
            ],
            weights=[78, 17, 5],
            k=1,
        )[0]
        capacity = (
            Decimal("4.00")
            if employment_type == EmploymentTypeEnum.part_time
            else Decimal("8.00")
        )

        first, last = fake.first_name(), fake.last_name()
        emp = Employee(
            id=uuid.uuid4(),
            employee_number=f"EMP-{uuid.uuid4().hex[:6].upper()}",
            first_name=first,
            last_name=last,
            email=f"{first.lower()}.{last.lower()}.{uuid.uuid4().hex[:6]}@example.com",
            department_id=dept_by_name.get(dept_name, dept_by_name["Software Engineering"]),
            job_title=title,
            experience_level=level,
            employment_type=employment_type,
            daily_capacity_hours=capacity,
            bench_status=BenchStatusEnum.on_project,
            hire_date=date.today() - timedelta(days=random.randint(120, 1400)),
            profile_text=resume,
            skills=", ".join(skills) if skills else None,
            hourly_cost_rate=hourly,
            is_active=True,
            current_project=None,
        )
        session.add(emp)
        session.flush()

        session.add(EmployeeCost(
            employee_id=emp.id,
            effective_date=date.today() - timedelta(days=120),
            annual_salary=salary,
            internal_daily_cost=daily,
            internal_hourly_cost=hourly,
            currency="EUR",
            overhead_multiplier=Decimal("1.35"),
        ))
        emps.append({
            "orm": emp,
            "skills": skills,
            "level": level.value,
            "title": title,
            "daily_cost": daily,
            "hourly_cost": hourly,
            "employment_type": employment_type,
            "daily_capacity_hours": capacity,
            "resume": resume,
            "cv_path": None,
            "is_manager": False,
        })

    session.commit()
    print(f"  -> {len(emps)} employees created (skills/title/level/salary from CV)")
    return emps


# ===========================================================================
# Stage 1b — normalised skills with provenance
# ===========================================================================

def stage1b_skills(session, emps: list) -> None:
    """Materialise CV-derived skills in the taxonomy + employee_skills."""
    print("Stage 1b: skills taxonomy + employee_skills")
    cache: dict[str, Skill] = {
        skill.name.lower(): skill
        for skill in session.execute(select(Skill)).scalars().all()
    }
    created = 0
    linked = 0

    for meta in emps:
        emp = meta["orm"]
        seen_for_employee: set[str] = set()
        for raw in meta["skills"]:
            name = str(raw).strip()
            if not name:
                continue
            key = name.lower()
            if key in seen_for_employee:
                continue
            seen_for_employee.add(key)
            skill = cache.get(key)
            if skill is None:
                skill = Skill(name=name, is_verified=False)
                session.add(skill)
                session.flush()
                cache[key] = skill
                created += 1
            session.add(
                EmployeeSkill(
                    employee_id=emp.id,
                    skill_id=skill.id,
                    proficiency_level=ProficiencyLevelEnum.intermediate,
                    source=SkillOriginEnum.cv,
                    is_locked=False,
                )
            )
            linked += 1

    session.commit()
    print(f"  -> {created} skills created, {linked} CV skill links")


# ===========================================================================
# Stage 1c — manager hierarchy
# ===========================================================================

_LEVEL_RANK = {
    "principal": 5,
    "lead": 4,
    "senior": 3,
    "mid": 2,
    "junior": 1,
}


def stage1c_hierarchy(session, emps: list, team_size: int = 10) -> list:
    """Assign every employee a direct manager (except the global head).

    Each department is split into teams of roughly ``team_size`` people. The
    most senior member of each team becomes its lead, which gives the People
    Manager portal realistic scoped teams instead of one manager per department.
    """
    print("Stage 1c: manager hierarchy")
    by_department: dict = {}
    for meta in emps:
        by_department.setdefault(meta["orm"].department_id, []).append(meta)

    def _lead_key(meta):
        return (
            _LEVEL_RANK.get(meta["level"], 1),
            -(meta["orm"].hire_date.toordinal() if meta["orm"].hire_date else 0),
        )

    team_leads: list = []
    for members in by_department.values():
        ordered = sorted(members, key=_lead_key, reverse=True)
        team_count = max(1, (len(ordered) + team_size - 1) // team_size)
        leads = ordered[:team_count]
        for lead in leads:
            lead["is_manager"] = True
            team_leads.append(lead)

    if not team_leads:
        return []

    global_head = max(team_leads, key=_lead_key)
    global_head["is_manager"] = True

    for lead in team_leads:
        if lead["orm"].id != global_head["orm"].id:
            lead["orm"].manager_id = global_head["orm"].id

    # Round-robin non-leads across their department's team leads.
    lead_ids = {lead["orm"].id for lead in team_leads}
    cursor: dict = {}
    for dept_id, members in by_department.items():
        dept_leads = [
            lead for lead in team_leads
            if (lead["orm"].department_id == dept_id)
        ]
        if not dept_leads:
            dept_leads = [global_head]
        for meta in sorted(members, key=_lead_key, reverse=True):
            emp = meta["orm"]
            if emp.id in lead_ids:
                continue
            idx = cursor.get(dept_id, 0)
            lead = dept_leads[idx % len(dept_leads)]
            cursor[dept_id] = idx + 1
            if lead["orm"].id != emp.id:
                emp.manager_id = lead["orm"].id

    global_head["orm"].manager_id = None

    session.commit()
    managers = [m for m in emps if m["is_manager"]]
    print(
        f"  -> {len(managers)} team leads, global head: "
        f"{global_head['orm'].first_name} {global_head['orm'].last_name}"
    )
    return managers


# ===========================================================================
# Stage 2 — CV documents + ChromaDB embeddings (best-effort)
# ===========================================================================

def stage2_documents(session, emps: list) -> None:
    print("Stage 2: CV documents + embeddings")
    if not SEED_EMBEDDINGS:
        print("  -> skipped (SEED_EMBEDDINGS=false)")
        return

    os.makedirs("./storage/cv", exist_ok=True)
    try:
        # Single shared collection (src/database/chroma_client.py).
        from src.database.chroma_client import reset_employee_collection
        collection = reset_employee_collection()
    except Exception as exc:
        print(f"  -> ChromaDB unavailable ({exc}); skipping embeddings")
        return

    ok, failed = 0, 0
    for idx, meta in enumerate(emps):
        emp = meta["orm"]
        cv_text = meta["resume"][:2000]
        try:
            resp = requests.post(OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "prompt": cv_text}, timeout=60)
            resp.raise_for_status()
            embedding = resp.json()["embedding"]
            collection.upsert(
                ids=[f"{emp.id}__cv"],
                embeddings=[embedding],
                documents=[cv_text],
                metadatas=[{
                    "employee_id": str(emp.id),
                    "id": str(emp.id),
                    "name": f"{emp.first_name} {emp.last_name}",
                    "skills": ", ".join(meta["skills"]),
                    "hourly_cost_rate": float(meta["hourly_cost"]),
                    "primary_role": meta["title"],
                    "seniority": meta["level"],
                }],
            )
            ok += 1
        except Exception:
            failed += 1

        session.add(Document(
            employee_id=emp.id,
            document_type=DocumentTypeEnum.cv,
            file_name=f"{emp.first_name}_{emp.last_name}_CV.txt",
            file_path=f"storage/cv/{emp.id}.txt",
            mime_type="text/plain",
            file_size_bytes=len(meta["resume"]),
            version=1,
            is_current=True,
            text_content=meta["resume"][:20000],
            chroma_doc_id=f"{emp.id}__cv" if SEED_EMBEDDINGS else None,
            ingestion_status=IngestionStatusEnum.ingested if SEED_EMBEDDINGS else IngestionStatusEnum.pending,
            approval_status=CvApprovalStatusEnum.approved,
            extracted_skills=meta["skills"],
            profile_applied_at=datetime.utcnow(),
        ))
        if (idx + 1) % 25 == 0:
            print(f"  -> embedded {idx + 1}/{len(emps)}")

    session.commit()
    print(f"  -> {ok} embedded, {failed} failed")


# ===========================================================================
# Stage 3 — projects + demands
# ===========================================================================

def stage3_projects(session) -> dict:
    print("Stage 3: projects + demands")
    today = date.today()
    projects = []

    for bp in PROJECT_BLUEPRINTS:
        start = today + timedelta(days=bp["start_days_ago"]) if bp["start_days_ago"] < 0 else today - timedelta(days=bp["start_days_ago"])
        duration_days = int(bp.get("duration_days", 180))
        proj = Project(
            id=uuid.uuid4(),
            name=bp["name"],
            client_name=bp.get("client"),
            status=bp["status"],
            probability=bp["probability"],
            target_margin=Decimal(str(bp["target_margin"])),
            total_budget=Decimal(str(bp["total_budget"])),
            start_date=start,
            end_date=start + timedelta(days=duration_days),
        )
        session.add(proj)
        session.flush()

        demands = []
        for role in bp["roles"]:
            seniority = role.get("seniority")
            demand = ProjectDemand(
                id=uuid.uuid4(),
                project_id=proj.id,
                role=role["title"],
                description=f"{role['title']} on {bp['name']} ({bp['client']}).",
                required_skills=role["required_skills"],
                hours_per_day=Decimal(str(role.get("hours_per_day", 8))),
                required_experience_level=(
                    ExperienceLevelEnum(seniority) if seniority else None
                ),
                headcount_needed=role["headcount"] - role["fill"],
                headcount_required=role["headcount"],
                min_proficiency="intermediate",
                target_bill_rate=Decimal(str(role["bill_rate"])),
                target_margin=Decimal(str(role["target_margin"])),
                win_probability=Decimal(str(bp["probability"] / 100.0)),
                status=DemandStatusEnum.filled if role["headcount"] - role["fill"] <= 0 else DemandStatusEnum.open,
                needed_by_date=start + timedelta(days=14),
            )
            session.add(demand)
            demands.append({"spec": role, "demand": demand})
        session.flush()
        projects.append({"orm": proj, "bp": bp, "start": start, "demands": demands})

    # Present-day open demands on the primary project (forecast target).
    primary = next(p for p in projects if p["orm"].name == PRIMARY_PROJECT_NAME)
    open_demands = []
    for spec in OPEN_DEMANDS:
        needed_by = today + timedelta(days=int(spec.get("needed_in_days", 14)))
        seniority = spec.get("seniority")
        demand = ProjectDemand(
            id=uuid.uuid4(),
            project_id=primary["orm"].id,
            role=spec["role"],
            description=spec["description"],
            required_skills=spec["required_skills"],
            hours_per_day=Decimal(str(spec.get("hours_per_day", 8))),
            required_experience_level=(
                ExperienceLevelEnum(seniority) if seniority else None
            ),
            headcount_needed=1,
            headcount_required=1,
            min_proficiency="intermediate",
            target_bill_rate=Decimal(str(spec["target_bill_rate"])),
            target_margin=Decimal(str(spec["target_margin"])),
            win_probability=Decimal(str(spec["win_probability"])),
            status=(
                DemandStatusEnum.requires_training
                if spec["role"] == "Python Data Engineer"
                else DemandStatusEnum.open
            ),
            needed_by_date=needed_by,
        )
        session.add(demand)
        open_demands.append(demand)
    session.commit()
    print(f"  -> {len(projects)} projects, {len(open_demands)} open present-day demands")
    return {"projects": projects, "primary": primary, "open_demands": open_demands}


# ===========================================================================
# Stage 4 — staffing (history + active + bench)
# ===========================================================================

def _overlap(emp_skills, required) -> int:
    emp = [s.lower() for s in (emp_skills or [])]
    return sum(1 for r in (required or []) if any(str(r).lower() in e or e in str(r).lower() for e in emp))


def _pick_best(pool, required):
    if not pool:
        return None
    return max(pool, key=lambda m: _overlap(m["skills"], required))


def _make_allocations_for_demand(
    session,
    demand,
    emp_meta,
    status,
    start,
    end,
    hours_per_day=None,
):
    """Create one allocation, respecting the employee's daily capacity."""
    capacity = Decimal(str(emp_meta.get("daily_capacity_hours") or 8))
    requested = Decimal(str(hours_per_day or demand.hours_per_day or 8))
    hours = min(requested, capacity)
    pct = (hours / capacity * Decimal("100")).quantize(Decimal("0.01"))
    session.add(Allocation(
        id=uuid.uuid4(),
        employee_id=emp_meta["orm"].id,
        demand_id=demand.id,
        role_on_project=demand.role,
        allocation_pct=pct,
        allocated_hours_per_day=hours,
        start_date=start,
        end_date=end,
        status=status,
        ai_justification=f"Staffed to {demand.role}.",
    ))


def stage4_staffing(session, emps: list, catalog: dict) -> dict:
    print("Stage 4: staffing (history + active + fractional allocations)")
    today = date.today()
    projects = catalog["projects"]
    open_demands = catalog["open_demands"]

    # ---- 1. Choose the bench pool: best matches for the open demands -------
    bench_ids = set()
    for demand in open_demands:
        for _ in range(2):
            pool = [m for m in emps if m["orm"].id not in bench_ids]
            best = _pick_best(pool, demand.required_skills)
            if best:
                bench_ids.add(best["orm"].id)
    for m in emps:  # pad to ~20 bench
        if len(bench_ids) >= 20:
            break
        bench_ids.add(m["orm"].id)

    # ---- 2. Active seats: fill fractional roles up to each hard cap --------
    active_seats = []
    for p in projects:
        if p["bp"]["status"] != "active":
            continue
        for d in p["demands"]:
            for _ in range(d["spec"]["fill"]):
                active_seats.append((p, d))

    available = [m for m in emps if m["orm"].id not in bench_ids]
    remaining_hours = {
        m["orm"].id: Decimal(str(m["daily_capacity_hours"] or 8))
        for m in available
    }
    project_assignments = set()
    assigned = set()

    for p, d in active_seats:
        needed_hours = Decimal(str(d["spec"].get("hours_per_day", 8)))
        pool = [
            m for m in available
            if remaining_hours.get(m["orm"].id, Decimal("0")) >= needed_hours
            and (m["orm"].id, p["orm"].id) not in project_assignments
        ]
        if not pool:
            continue
        best = _pick_best(pool, d["spec"]["required_skills"])
        remaining_hours[best["orm"].id] -= needed_hours
        project_assignments.add((best["orm"].id, p["orm"].id))
        assigned.add(best["orm"].id)
        _make_allocations_for_demand(
            session,
            d["demand"],
            best,
            AllocationStatusEnum.active,
            p["start"],
            None,
            hours_per_day=needed_hours,
        )
        best["orm"].bench_status = BenchStatusEnum.on_project
        best["orm"].current_project = p["orm"].name
        best["orm"].bench_start_date = None

    # Anyone with no active hours is on the bench.
    for m in emps:
        if m["orm"].id not in assigned:
            bench_ids.add(m["orm"].id)

    # ---- 3. Historical allocations on completed projects -------------------
    hist_count = {m["orm"].id: 0 for m in emps}

    def _pick_history(required):
        pool = [m for m in emps if hist_count[m["orm"].id] < 2] or emps
        best = max(pool, key=lambda m: (_overlap(m["skills"], required), -hist_count[m["orm"].id]))
        hist_count[best["orm"].id] += 1
        return best

    for p in projects:
        if p["bp"]["status"] != "completed":
            continue
        proj_end = p["start"] + timedelta(days=p["bp"]["duration_days"])
        for d in p["demands"]:
            for seat in range(d["spec"]["fill"]):
                best = _pick_history(d["spec"]["required_skills"])
                if not best:
                    continue
                start = p["start"] + timedelta(days=seat * 7)
                end = min(proj_end, start + timedelta(days=random.randint(90, p["bp"]["duration_days"])))
                _make_allocations_for_demand(
                    session,
                    d["demand"],
                    best,
                    AllocationStatusEnum.completed,
                    start,
                    end,
                    hours_per_day=d["spec"].get("hours_per_day", 8),
                )

    session.flush()

    # ---- 4. Mark the bench + set bench_start_date --------------------------
    bench_meta = [m for m in emps if m["orm"].id in bench_ids]
    for m in bench_meta:
        emp = m["orm"]
        emp.bench_status = BenchStatusEnum.bench
        emp.current_project = None
        emp.bench_start_date = today - timedelta(days=random.randint(5, 45))

    session.commit()
    print(
        f"  -> {len(assigned)} on active projects, {len(bench_meta)} on bench, "
        "fractional + historical allocations created"
    )
    return {"bench_ids": bench_ids}


# ===========================================================================
# Stage 4b — project-earned skills + training paths
# ===========================================================================

def _skill_lookup(session) -> dict:
    return {
        skill.name.lower(): skill
        for skill in session.execute(select(Skill)).scalars().all()
    }


def _ensure_skill(session, cache: dict, name: str) -> Skill:
    key = str(name).strip().lower()
    skill = cache.get(key)
    if skill is None:
        skill = Skill(name=str(name).strip(), is_verified=False)
        session.add(skill)
        session.flush()
        cache[key] = skill
    return skill


def stage4b_skills_trainings(session, emps: list, catalog: dict) -> None:
    """Grant project skills and create realistic upskilling assignments."""
    print("Stage 4b: project skills + trainings")
    cache = _skill_lookup(session)
    emps_by_id = {m["orm"].id: m for m in emps}

    allocations = session.execute(select(Allocation)).scalars().all()
    evidence_seen = {
        (e.employee_id, e.skill_id, e.source, e.source_ref)
        for e in session.execute(select(EmployeeSkillEvidence)).scalars().all()
    }
    existing_links = {
        (es.employee_id, es.skill_id): es
        for es in session.execute(select(EmployeeSkill)).scalars().all()
    }

    project_links = 0
    for allocation in allocations:
        if allocation.status not in {
            AllocationStatusEnum.active,
            AllocationStatusEnum.approved_by_manager,
            AllocationStatusEnum.completed,
        }:
            continue
        demand = session.get(ProjectDemand, allocation.demand_id)
        if not demand:
            continue
        raw_skills = demand.required_skills or []
        if isinstance(raw_skills, dict):
            raw_skills = raw_skills.get("skills", list(raw_skills.keys()))
        if not isinstance(raw_skills, list):
            raw_skills = []

        for raw in raw_skills:
            skill = _ensure_skill(session, cache, raw)
            link = existing_links.get((allocation.employee_id, skill.id))
            if link is None:
                link = EmployeeSkill(
                    employee_id=allocation.employee_id,
                    skill_id=skill.id,
                    proficiency_level=ProficiencyLevelEnum.intermediate,
                    source=SkillOriginEnum.project,
                    source_ref=demand.id,
                    is_locked=True,
                )
                session.add(link)
                existing_links[(allocation.employee_id, skill.id)] = link
                project_links += 1
            else:
                link.source = SkillOriginEnum.project
                link.source_ref = demand.id
                link.is_locked = True

            key = (allocation.employee_id, skill.id, SkillOriginEnum.project, demand.id)
            if key not in evidence_seen:
                session.add(EmployeeSkillEvidence(
                    employee_id=allocation.employee_id,
                    skill_id=skill.id,
                    source=SkillOriginEnum.project,
                    source_ref=demand.id,
                    note=f"Earned on {demand.role}.",
                ))
                evidence_seen.add(key)
    session.flush()

    # ---- Trainings for bench employees ------------------------------------
    bench_meta = [
        m for m in emps
        if m["orm"].bench_status == BenchStatusEnum.bench
    ]
    open_demands = catalog["open_demands"]
    trainings = 0
    completed = 0

    for idx, meta in enumerate(bench_meta[:18]):
        demand = open_demands[idx % len(open_demands)]
        required = [
            str(s).strip()
            for s in (demand.required_skills or [])
            if str(s).strip()
        ]
        missing = [
            s for s in required
            if s.lower() not in {x.lower() for x in meta["skills"]}
        ]
        if not missing:
            missing = required[:2]
        if not missing:
            continue

        target_date = date.today() + timedelta(days=random.randint(21, 90))
        is_done = idx % 5 == 0
        training = TrainingAssignment(
            employee_id=meta["orm"].id,
            project_id=demand.project_id,
            demand_id=demand.id,
            title=f"{demand.role} upskilling path",
            targeted_skills=missing,
            ai_upskilling_path=(
                f"Week 1-2: {missing[0]} fundamentals; "
                f"week 3-4: hands-on {demand.role} scenarios; "
                f"week 5-6: shadowing and assessment."
            ),
            status=(
                TrainingStatusEnum.completed
                if is_done
                else TrainingStatusEnum.assigned
            ),
            target_completion_date=(
                date.today() - timedelta(days=7) if is_done else target_date
            ),
            completed_at=datetime.utcnow() if is_done else None,
            notes="Seeded upskilling path.",
        )
        session.add(training)
        session.flush()
        trainings += 1
        if is_done:
            completed += 1
            for raw in missing:
                skill = _ensure_skill(session, cache, raw)
                link = existing_links.get((meta["orm"].id, skill.id))
                if link is None:
                    link = EmployeeSkill(
                        employee_id=meta["orm"].id,
                        skill_id=skill.id,
                        proficiency_level=ProficiencyLevelEnum.intermediate,
                        source=SkillOriginEnum.training,
                        source_ref=training.id,
                        is_locked=False,
                    )
                    session.add(link)
                    existing_links[(meta["orm"].id, skill.id)] = link
                key = (
                    meta["orm"].id,
                    skill.id,
                    SkillOriginEnum.training,
                    training.id,
                )
                if key not in evidence_seen:
                    session.add(EmployeeSkillEvidence(
                        employee_id=meta["orm"].id,
                        skill_id=skill.id,
                        source=SkillOriginEnum.training,
                        source_ref=training.id,
                        note=f"Completed {training.title}.",
                    ))
                    evidence_seen.add(key)

    # ---- Refresh demand roster statuses from actual allocations -----------
    statuses = {DemandStatusEnum.open: 0, DemandStatusEnum.partially_filled: 0,
                DemandStatusEnum.filled: 0, DemandStatusEnum.requires_training: 0}
    for demand in session.execute(select(ProjectDemand)).scalars().all():
        if demand.status == DemandStatusEnum.requires_training:
            statuses[DemandStatusEnum.requires_training] += 1
            continue
        filled = session.execute(
            select(Allocation).where(
                Allocation.demand_id == demand.id,
                Allocation.status.in_([
                    AllocationStatusEnum.active,
                    AllocationStatusEnum.approved_by_manager,
                    AllocationStatusEnum.pending_manager_approval,
                    AllocationStatusEnum.completed,
                ]),
            )
        ).scalars().all()
        required = max(int(demand.headcount_required or 1), 1)
        demand.headcount_needed = max(required - len(filled), 0)
        if len(filled) >= required:
            demand.status = DemandStatusEnum.filled
        elif len(filled) > 0:
            demand.status = DemandStatusEnum.partially_filled
        else:
            demand.status = DemandStatusEnum.open
        statuses[demand.status] += 1

    session.commit()
    print(
        f"  -> {project_links} project skill links, "
        f"{trainings} trainings ({completed} completed), "
        f"demands: {statuses[DemandStatusEnum.filled]} filled / "
        f"{statuses[DemandStatusEnum.partially_filled]} partial / "
        f"{statuses[DemandStatusEnum.open]} open"
    )


# ===========================================================================
# Stage 5 — financials: bench history + bench burn + KPIs
# ===========================================================================

def stage5_financials(session, emps: list, staffing: dict) -> None:
    print("Stage 5: financials (bench history + burn + KPIs)")
    today = date.today()
    bench_ids = staffing["bench_ids"]

    employees = session.execute(select(Employee)).scalars().all()
    cost_by_emp = {
        c.employee_id: c
        for c in session.execute(select(EmployeeCost)).scalars().all()
    }

    for emp in employees:
        allocs = session.execute(
            select(Allocation).where(Allocation.employee_id == emp.id).order_by(Allocation.start_date)
        ).scalars().all()
        allocs = [a for a in allocs if a.start_date]

        cost = cost_by_emp.get(emp.id)
        daily_cost = cost.internal_daily_cost if cost else Decimal("500.00")

        # Bench gaps between historical allocations
        cursor = None
        for a in sorted(allocs, key=lambda x: x.start_date):
            if cursor and a.start_date > cursor:
                gap_days = (a.start_date - cursor).days
                if gap_days >= 5:
                    _add_bench_period(session, emp.id, cursor, a.start_date, daily_cost)
            end = a.end_date or a.start_date
            cursor = max(cursor, end) if cursor else end

        # Currently-benched employees: open bench period running to today.
        # Started when they actually hit the bench (bench_start_date), so the
        # weekly bench trend reflects the ramp-up rather than their last project.
        if emp.id in bench_ids:
            start = emp.bench_start_date or (today - timedelta(days=30))
            if start >= today:
                start = today - timedelta(days=30)
            _add_bench_period(session, emp.id, start, today, daily_cost)
            session.add(BenchHistory(
                employee_id=emp.id, bench_start=start, bench_end=None,
                duration_days=max((today - start).days, 1),
            ))
        elif cursor:
            session.add(BenchHistory(
                employee_id=emp.id, bench_start=cursor,
                bench_end=min(cursor + timedelta(days=14), today),
                duration_days=14,
            ))

    session.flush()

    # ---- KPI snapshots (last 30 days, daily) -------------------------------
    # Bench ratio is computed from the ACTUAL bench periods just created (so it
    # matches the bench-utilisation chart). MTTR and human-intervention follow
    # gentle, improving trends with small noise — not random per-day values —
    # so the KPI trend tells a coherent story.
    total = len(employees) or 1
    periods = [
        (c.employee_id, c.period_start, c.period_end)
        for c in session.execute(select(BenchCost)).scalars().all()
    ]

    def _bench_on(day: date) -> int:
        return len({e for e, s, en in periods if s <= day <= en})

    for i in range(30):
        d = today - timedelta(days=29 - i)
        progress = i / 29.0
        bench_ratio = Decimal(str(round(_bench_on(d) / total * 100, 2)))
        # MTTR improves 24 → 15 days; human intervention 30% → 22%.
        mttr = max(1.0, 24.0 - 9.0 * progress + random.uniform(-0.4, 0.4))
        human = max(0.0, 30.0 - 8.0 * progress + random.uniform(-0.8, 0.8))
        session.add(KpiSnapshot(
            snapshot_date=d,
            bench_ratio_pct=bench_ratio.quantize(Decimal("0.01")),
            mttr_days=Decimal(str(round(mttr, 2))),
            human_intervention_pct=Decimal(str(round(human, 2))),
        ))

    session.commit()
    print("  -> bench periods, bench costs and 30 KPI snapshots created")


def _add_bench_period(session, employee_id, start: date, end: date, daily_cost: Decimal) -> None:
    days = max((end - start).days, 1)
    session.add(BenchCost(
        employee_id=employee_id,
        period_start=start,
        period_end=end,
        bench_days=days,
        daily_cost=daily_cost,
        total_cost=(daily_cost * days).quantize(Decimal("0.01")),
    ))


# ===========================================================================
# Stage 6 — observability history (forecast runs + HITL events)
# ===========================================================================

def stage6_observability(session) -> None:
    print("Stage 6: observability history")
    manager = session.execute(select(User).where(User.username == "manager")).scalars().first()
    if not manager:
        print("  -> no manager user yet; skipping")
        return

    completed = session.execute(
        select(Allocation).where(Allocation.status == AllocationStatusEnum.completed).limit(40)
    ).scalars().all()
    for i in range(8):
        run = ForecastRun(
            id=uuid.uuid4(),
            run_date=datetime.utcnow() - timedelta(days=random.randint(5, 60)),
            forecast_horizon_days=90,
            triggered_by=manager.id,
            status="completed",
        )
        session.add(run)
        session.flush()
        for alloc in random.sample(completed, min(5, len(completed))):
            session.add(HitlReviewEvent(
                forecast_run_id=run.id,
                allocation_id=alloc.id,
                reviewer_id=manager.id,
                decision=random.choice([HitlDecisionEnum.approved, HitlDecisionEnum.rejected]),
                reviewed_at=datetime.utcnow() - timedelta(days=random.randint(1, 60)),
            ))
    session.commit()
    print("  -> forecast runs + HITL review events created")


# ===========================================================================
# Stage 6b — demo CV approval queue
# ===========================================================================

def stage6b_cv_reviews(session, emps: list) -> None:
    """Seed pending/rejected CV reviews without touching active profiles."""
    print("Stage 6b: CV approval queue")
    candidates = [m for m in emps if m["orm"].manager_id is not None][:3]
    pending = 0
    rejected = 0

    for idx, meta in enumerate(candidates):
        emp = meta["orm"]
        proposed = list(dict.fromkeys(
            [str(s) for s in (meta["skills"] or [])][:8]
            + ["Kafka", "Docker", "Terraform"]
        ))
        is_rejected = idx == 2
        status = (
            CvApprovalStatusEnum.rejected
            if is_rejected
            else CvApprovalStatusEnum.pending_approval
        )
        summary = (
            f"Updated professional summary for {emp.first_name} {emp.last_name}. "
            f"Highlights: {', '.join(proposed[:5])}."
        )

        doc = Document(
            employee_id=emp.id,
            document_type=DocumentTypeEnum.cv,
            file_name=f"{emp.first_name}_{emp.last_name}_CV_v2.txt",
            file_path=f"storage/cv/{emp.id}_v2.txt",
            mime_type="text/plain",
            file_size_bytes=len(meta["resume"] or ""),
            version=2,
            is_current=False,
            text_content=(meta["resume"] or "")[:20000],
            ingestion_status=IngestionStatusEnum.pending,
            approval_status=status,
            extracted_skills=proposed,
            extracted_summary=summary,
            pending_profile_updates={
                "summary": summary,
                "skills": proposed,
                "document_id": str(uuid.uuid4()),
            },
            review_comment=(
                "Please attach evidence for the Terraform claim."
                if is_rejected else None
            ),
            reviewed_at=datetime.utcnow() if is_rejected else None,
        )
        session.add(doc)
        session.flush()

        session.add(CvReviewRequest(
            document_id=doc.id,
            employee_id=emp.id,
            manager_id=emp.manager_id,
            status=status,
            proposed_skills=proposed,
            proposed_summary=summary,
            manager_comment=(
                "Please attach evidence for the Terraform claim."
                if is_rejected else None
            ),
            reviewed_at=datetime.utcnow() if is_rejected else None,
        ))
        if is_rejected:
            rejected += 1
        else:
            pending += 1

    session.commit()
    print(f"  -> {pending} pending CV review(s), {rejected} rejected example(s)")


# ===========================================================================
# Orchestration
# ===========================================================================

def _print_summary() -> None:
    """Print the target database and the row counts the UI depends on."""
    print("\nSeed summary")
    print(f"  target : {Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{Config.POSTGRES_DB}")
    with SyncSessionLocal() as session:
        counts = {}
        for table in (
            "employees",
            "projects",
            "project_demands",
            "allocations",
            "employee_skills",
            "training_assignments",
            "cv_review_requests",
            "forecast_runs",
            "hitl_review_events",
            "users",
        ):
            counts[table] = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        for table, count in counts.items():
            print(f"  {table:<22} {count}")


def main() -> None:
    print(
        "Seeding PostgreSQL at "
        f"{Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{Config.POSTGRES_DB}"
    )
    init_db_sync()
    with SyncSessionLocal() as session:
        reset_database(session)
        emps = stage1_employees(session)
        stage1b_skills(session, emps)
        stage1c_hierarchy(session, emps)
        stage2_documents(session, emps)
        catalog = stage3_projects(session)
        staffing = stage4_staffing(session, emps, catalog)
        stage4b_skills_trainings(session, emps, catalog)
        stage5_financials(session, emps, staffing)
        stage6b_cv_reviews(session, emps)
        # ensure the manager/admin accounts exist before observability history
        from src.auth.auth import init_auth_db
        init_auth_db()
        stage6_observability(session)

    init_auth_db()
    _print_summary()
    print("Data seed simulation complete.")


if __name__ == "__main__":
    main()

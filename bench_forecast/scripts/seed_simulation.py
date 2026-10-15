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
from src.database.database import SyncSessionLocal, init_db_sync
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchCost,
    BenchHistory,
    BenchStatusEnum,
    DemandStatusEnum,
    Department,
    Document,
    DocumentTypeEnum,
    Employee,
    EmployeeCost,
    EmploymentTypeEnum,
    ExperienceLevelEnum,
    ForecastRun,
    HitlDecisionEnum,
    HitlReviewEvent,
    IngestionStatusEnum,
    KpiSnapshot,
    Project,
    ProjectDemand,
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
        "hitl_review_events", "allocations", "hiring_recommendations", "documents",
        "employee_skills", "bench_costs", "bench_history", "employee_costs",
        "kpi_snapshots", "forecast_runs", "project_demands", "project_roles",
        "projects", "employees", "departments", "skills", "users",
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
            employment_type=EmploymentTypeEnum.full_time,
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
            "resume": resume,
            "cv_path": None,
        })

    session.commit()
    print(f"  -> {len(emps)} employees created (skills/title/level/salary from CV)")
    return emps


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
        proj = Project(
            id=uuid.uuid4(),
            name=bp["name"],
            status=bp["status"],
            probability=bp["probability"],
            target_margin=Decimal(str(bp["target_margin"])),
            total_budget=Decimal(str(bp["total_budget"])),
        )
        session.add(proj)
        session.flush()

        demands = []
        for role in bp["roles"]:
            demand = ProjectDemand(
                id=uuid.uuid4(),
                project_id=proj.id,
                role=role["title"],
                description=f"{role['title']} on {bp['name']} ({bp['client']}).",
                required_skills=role["required_skills"],
                headcount_needed=role["headcount"] - role["fill"],
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
        demand = ProjectDemand(
            id=uuid.uuid4(),
            project_id=primary["orm"].id,
            role=spec["role"],
            description=spec["description"],
            required_skills=spec["required_skills"],
            headcount_needed=1,
            min_proficiency="intermediate",
            target_bill_rate=Decimal(str(spec["target_bill_rate"])),
            target_margin=Decimal(str(spec["target_margin"])),
            win_probability=Decimal(str(spec["win_probability"])),
            status=DemandStatusEnum.open,
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


def _make_allocations_for_demand(session, demand, emp_meta, status, start, end):
    session.add(Allocation(
        id=uuid.uuid4(),
        employee_id=emp_meta["orm"].id,
        demand_id=demand.id,
        role_on_project=demand.role,
        allocation_pct=Decimal("100.00"),
        start_date=start,
        end_date=end,
        status=status,
        ai_justification=f"Staffed to {demand.role}.",
    ))


def stage4_staffing(session, emps: list, catalog: dict) -> dict:
    print("Stage 4: staffing (history + active + bench)")
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

    # ---- 2. Active seats: staff everyone not on the bench ------------------
    active_seats = []
    for p in projects:
        if p["bp"]["status"] != "active":
            continue
        for d in p["demands"]:
            for _ in range(d["spec"]["fill"]):
                active_seats.append((p, d))

    available = [m for m in emps if m["orm"].id not in bench_ids]
    assigned = set()
    for p, d in active_seats:
        pool = [m for m in available if m["orm"].id not in assigned]
        if not pool:
            break
        best = _pick_best(pool, d["spec"]["required_skills"])
        assigned.add(best["orm"].id)
        _make_allocations_for_demand(session, d["demand"], best, AllocationStatusEnum.active, p["start"], None)
        best["orm"].bench_status = BenchStatusEnum.on_project
        best["orm"].current_project = p["orm"].name
        best["orm"].bench_start_date = None

    # Anyone not assigned to an active seat is on the bench.
    for m in emps:
        if m["orm"].id not in assigned:
            bench_ids.add(m["orm"].id)

    # ---- 3. Historical allocations on completed projects -------------------
    # Spread history so most employees have 1–2 prior projects; anyone with no
    # history ends up benched (which is exactly the "never worked" case).
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
                _make_allocations_for_demand(session, d["demand"], best, AllocationStatusEnum.completed, start, end)

    session.flush()

    # ---- 4. Mark the bench + set bench_start_date --------------------------
    bench_meta = [m for m in emps if m["orm"].id in bench_ids]
    for m in bench_meta:
        emp = m["orm"]
        emp.bench_status = BenchStatusEnum.bench
        emp.current_project = None
        emp.bench_start_date = today - timedelta(days=random.randint(5, 45))

    session.commit()
    print(f"  -> {len(assigned)} on active projects, {len(bench_meta)} on bench, history created")
    return {"bench_ids": bench_ids}


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
# Orchestration
# ===========================================================================

def main() -> None:
    init_db_sync()
    with SyncSessionLocal() as session:
        reset_database(session)
        emps = stage1_employees(session)
        stage2_documents(session, emps)
        catalog = stage3_projects(session)
        staffing = stage4_staffing(session, emps, catalog)
        stage5_financials(session, emps, staffing)
        # ensure the manager exists before observability history
        from src.auth.auth import init_auth_db
        init_auth_db()
        stage6_observability(session)

    init_auth_db()
    print("Data seed simulation complete.")


if __name__ == "__main__":
    main()

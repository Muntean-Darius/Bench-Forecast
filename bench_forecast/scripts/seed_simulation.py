import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import random
import uuid
from datetime import datetime, timedelta, date
from decimal import Decimal

import pdfplumber
from fpdf import FPDF
from datasets import load_dataset
import chromadb
import requests
from faker import Faker

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, String, Integer, Numeric, Date, ForeignKey
from sqlalchemy.orm import declarative_base, Mapped, mapped_column

# Import all models from the application
from src.database.models import (
    Base, Employee, Department, Skill, EmployeeSkill, Document,
    Project, ProjectDemand, Allocation, ForecastRun, HitlReviewEvent, User,
    EmployeeCost, BenchHistory, BenchCost,
    ExperienceLevelEnum, BenchStatusEnum, DocumentTypeEnum, IngestionStatusEnum,
    DemandStatusEnum, AllocationStatusEnum, HitlDecisionEnum, EmploymentTypeEnum
)

# Configuration
DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/bench_forecast")
engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
chroma_client = chromadb.PersistentClient(path="./chroma_data")
fake = Faker()

# ---------------------------------------------------------------------------
# Stage 1: HR & Finance Setup
# ---------------------------------------------------------------------------
async def stage1_hr_and_finance(session: AsyncSession):
    print("Stage 1: HR & Finance Setup")
    
    # Load the whole dataset
    dataset = load_dataset("Divyaamith/Kaggle-Resume", split="train")
    
    # Hardcode the exact technical categories from the Kaggle dataset
    acceptable_categories = {
        "data-science",
        "web-designing",
        "java-developer",
        "sap-developer",
        "automation-testing",
        "python-developer",
        "devops-engineer",
        "network-security-engineer",
        "database",
        "hadoop",
        "etl-developer",
        "dotnet-developer",
        "blockchain",
        "testing",
        "information-technology"
    }
    
    def is_tech(row):
        cat = str(row.get("Category", "")).lower().strip()
        return cat in acceptable_categories
        
    tech_dataset = dataset.filter(is_tech)
    
    # Now shuffle the purely technical resumes and take 100
    dataset = tech_dataset.shuffle(seed=42).select(range(100))
    
    # IT Consulting Departments
    dept_names = ["Software Engineering", "Data & AI", "Cloud & DevOps", "Cyber Security", "QA & Testing"]
    existing_depts_res = await session.execute(select(Department).where(Department.name.in_(dept_names)))
    depts_by_name = {d.name: d for d in existing_depts_res.scalars().all()}
    
    depts = []
    for d in dept_names:
        if d in depts_by_name:
            depts.append(depts_by_name[d])
        else:
            dept = Department(name=d, head_count_budget=50)
            session.add(dept)
            depts.append(dept)
    await session.flush()
    
    employees = []
    for row in dataset:
        resume_text = row.get("Resume_str")
        
        if not resume_text or not str(resume_text).strip():
            continue
            
        category = row.get("Category")
        if not category or not str(category).strip():
            category = "Software Engineer"

        exp_level = random.choice(list(ExperienceLevelEnum))
        first_name = fake.first_name()
        last_name = fake.last_name()
        
        unique_email = f"{first_name.lower()}.{last_name.lower()}.{uuid.uuid4().hex[:6]}@example.com"
        
        emp = Employee(
            employee_number=f"EMP-{uuid.uuid4().hex[:6].upper()}",
            first_name=first_name,
            last_name=last_name,
            email=unique_email,
            department_id=random.choice(depts).id,
            job_title=category,
            experience_level=exp_level,
            employment_type=EmploymentTypeEnum.full_time,
            bench_status=BenchStatusEnum.on_project,
            profile_text=resume_text
        )
        session.add(emp)
        employees.append(emp)
        
        if len(employees) >= 100:
            break
        
    await session.flush()
    
    # employee_costs: varying pay by seniority level and role
    ROLE_MULTIPLIERS = {
        "data-science": Decimal("1.20"),
        "blockchain": Decimal("1.25"),
        "devops-engineer": Decimal("1.15"),
        "network-security-engineer": Decimal("1.15"),
        "python-developer": Decimal("1.10"),
        "java-developer": Decimal("1.05"),
        "dotnet-developer": Decimal("1.05"),
    }

    SENIORITY_BASE_HOURLY = {
        ExperienceLevelEnum.junior: (Decimal("35.00"), Decimal("45.00")),
        ExperienceLevelEnum.mid: (Decimal("55.00"), Decimal("70.00")),
        ExperienceLevelEnum.senior: (Decimal("85.00"), Decimal("110.00")),
        ExperienceLevelEnum.lead: (Decimal("115.00"), Decimal("140.00")),
        ExperienceLevelEnum.principal: (Decimal("150.00"), Decimal("185.00")),
    }

    for emp in employees:
        base_min, base_max = SENIORITY_BASE_HOURLY.get(
            emp.experience_level, (Decimal("55.00"), Decimal("70.00"))
        )
        base_hourly = Decimal(str(round(random.uniform(float(base_min), float(base_max)), 2)))
        multiplier = ROLE_MULTIPLIERS.get(str(emp.job_title).lower().strip(), Decimal("1.00"))
        
        hourly = (base_hourly * multiplier).quantize(Decimal("0.01"))
        daily = (hourly * Decimal("8.00")).quantize(Decimal("0.01"))
        salary = (daily * Decimal("220")).quantize(Decimal("0.01"))

        # Also store on employee legacy column for endpoints that read directly
        emp.hourly_cost_rate = hourly

        cost = EmployeeCost(
            employee_id=emp.id,
            annual_salary=salary,
            internal_daily_cost=daily,
            internal_hourly_cost=hourly,
            currency="EUR",
            overhead_multiplier=Decimal("1.35")
        )
        session.add(cost)
    
    await session.commit()
    return employees

# ---------------------------------------------------------------------------
# Stage 2: Document Ingestion Pipeline
# ---------------------------------------------------------------------------
async def stage2_document_pipeline(session: AsyncSession, employees: list):
    print("Stage 2: Document Ingestion Pipeline (Generating PDFs & Embeddings)")
    try:
        chroma_client.delete_collection("employee_profiles")
    except Exception:
        pass
    collection = chroma_client.get_or_create_collection("employee_profiles")
    os.makedirs("./storage/cv", exist_ok=True)
    
    for idx, emp in enumerate(employees):
        pdf_path = f"./storage/cv/{emp.id}.pdf"
        
        # 1. Render actual .pdf files to disk using the raw unstructured text
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=10)
        safe_text = emp.profile_text.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 5, txt=safe_text)
        pdf.output(pdf_path)
        
        # 2. Extract text via pdfplumber
        extracted_text = ""
        with pdfplumber.open(pdf_path) as pdf_file:
            for page in pdf_file.pages:
                extracted_text += page.extract_text() + "\n"
        
        # 3. STRICT Embed to ChromaDB via Ollama
        # NO TRY/EXCEPT. If Ollama is down, this crashes immediately.
        resp = requests.post("http://localhost:11434/api/embeddings", json={
            "model": "nomic-embed-text",
            "prompt": extracted_text[:2000]
        })
        resp.raise_for_status()
        
        embedding_data = resp.json()
        if "embedding" not in embedding_data:
            raise ValueError(f"Ollama returned 200 OK but no 'embedding' key: {embedding_data}")
            
        embedding = embedding_data["embedding"]
            
        doc_id = str(uuid.uuid4())
        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[extracted_text[:2000]],
            metadatas=[{"employee_id": str(emp.id)}]
        )
        
        # 4. Link chroma_doc_id
        doc = Document(
            employee_id=emp.id,
            document_type=DocumentTypeEnum.cv,
            file_name=f"{emp.id}.pdf",
            file_path=pdf_path,
            text_content=extracted_text,
            chroma_doc_id=doc_id,
            ingestion_status=IngestionStatusEnum.ingested
        )
        session.add(doc)
        
        if (idx + 1) % 10 == 0:
            print(f"  -> Ingested {idx + 1}/{len(employees)} documents")
            
    await session.commit()

# ---------------------------------------------------------------------------
# Stage 3: Historical Project & Allocation Engine
# ---------------------------------------------------------------------------
async def stage3_historical_projects(session: AsyncSession, employees: list):
    print("Stage 3: Historical Project & Allocation Engine")
    today = date.today()
    projects = []
    
    for i in range(40):
        start = today - timedelta(days=random.randint(60, 360))
        end = start + timedelta(days=random.randint(30, 90))
        
        proj = Project(
            name=f"Historical Client Project {i}",
            status="completed" if end < today else "active",
            probability=100,
            target_margin=Decimal("25.00"),
            total_budget=Decimal("100000.00")
        )
        session.add(proj)
        projects.append((proj, start, end))
        
    await session.flush()
    
    for emp in employees:
        emp_projs = random.sample(projects, k=random.randint(1, 3))
        emp_projs.sort(key=lambda x: x[1])
        
        last_end = today - timedelta(days=365)
        
        for proj, start, end in emp_projs:
            if start > last_end:
                gap = (start - last_end).days
                bh = BenchHistory(
                    employee_id=emp.id, bench_start=last_end, 
                    bench_end=start, duration_days=gap
                )
                bc = BenchCost(
                    employee_id=emp.id, period_start=last_end, 
                    period_end=start, bench_days=gap, 
                    daily_cost=Decimal("500.00"), 
                    total_cost=Decimal("500.00") * gap
                )
                session.add_all([bh, bc])
            
            status = AllocationStatusEnum.completed if end < today else AllocationStatusEnum.active
            alloc = Allocation(
                employee_id=emp.id,
                role_on_project=emp.job_title,
                allocation_pct=Decimal("100.00"),
                start_date=start,
                end_date=end,
                status=status
            )
            session.add(alloc)
            last_end = end
            
    await session.commit()

# ---------------------------------------------------------------------------
# Stage 4: Mock Observability & HITL History
# ---------------------------------------------------------------------------
async def stage4_observability(session: AsyncSession, employees: list):
    print("Stage 4: Mock Observability & HITL History")
    
    allocs = (await session.execute(select(Allocation).limit(50))).scalars().all()
    
    from src.auth.auth import hash_password
    user_res = await session.execute(select(User).where(User.username == "manager"))
    user = user_res.scalars().first()
    if not user:
        user = User(
            username="manager",
            password_hash=hash_password("manager"),
            email="manager@example.com",
            full_name="Darius Muntean",
            role="manager"
        )
        session.add(user)
        await session.flush()
    
    for _ in range(10):
        run = ForecastRun(forecast_horizon_days=90, triggered_by=user.id)
        session.add(run)
        
    await session.flush()
    
    for alloc in allocs:
        hitl = HitlReviewEvent(
            allocation_id=alloc.id,
            reviewer_id=user.id,
            decision=random.choice(list(HitlDecisionEnum))
        )
        session.add(hitl)
    await session.commit()

# ---------------------------------------------------------------------------
# Stage 5: Present Day State
# ---------------------------------------------------------------------------
async def stage5_present_day(session: AsyncSession, employees: list):
    print("Stage 5: Present Day State")
    proj = Project(
        name="Active Needs Project",
        probability=85,
        target_margin=Decimal("30.00"),
        total_budget=Decimal("500000.00"),
        status="active"
    )
    session.add(proj)
    await session.flush()
    
    role_specs = [
        ("Senior Cloud Engineer", ["AWS", "Docker", "Kubernetes", "Terraform", "Linux"], 120.0, 30.0),
        ("Full-Stack Python/React Specialist", ["Python", "FastAPI", "React", "TypeScript", "PostgreSQL"], 105.0, 28.0),
        ("DevOps Architect", ["CI/CD", "Docker", "Kubernetes", "Linux", "Terraform"], 115.0, 32.0),
        ("Backend Services Engineer", ["Python", "FastAPI", "REST", "PostgreSQL", "Docker"], 95.0, 25.0),
        ("Data & ML Engineer", ["Python", "SQL", "Pandas", "Machine Learning", "Docker"], 110.0, 29.0),
    ]

    for role_title, skills, bill_rate, margin in role_specs:
        demand = ProjectDemand(
            project_id=proj.id,
            role=role_title,
            required_skills=skills,
            headcount_needed=1,
            target_bill_rate=Decimal(str(bill_rate)),
            target_margin=Decimal(str(margin)),
            win_probability=Decimal("0.85"),
            status=DemandStatusEnum.open,
            needed_by_date=date.today() + timedelta(days=14),
            description=f"Core engineering role for {proj.name} requiring expertise in {', '.join(skills[:3])}."
        )
        session.add(demand)
        
    bench_count = min(20, len(employees))
    bench_emps = random.sample(employees, bench_count)
    for emp in bench_emps:
        emp.bench_status = BenchStatusEnum.bench
        emp.bench_start_date = date.today() - timedelta(days=random.randint(5, 40))
        emp.current_project = None
        
    await session.commit()

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSessionLocal() as session:
        emps = await stage1_hr_and_finance(session)
        await stage2_document_pipeline(session, emps)
        await stage3_historical_projects(session, emps)
        await stage4_observability(session, emps)
        await stage5_present_day(session, emps)
        print("Data Seed Simulation complete.")

    from src.auth.auth import init_auth_db
    init_auth_db()


if __name__ == "__main__":
    asyncio.run(main())
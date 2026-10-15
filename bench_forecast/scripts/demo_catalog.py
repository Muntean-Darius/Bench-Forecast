"""Reference catalogue for the demo seeder.

This module is **reference data only** — it is imported by
``scripts/seed_simulation.py``. It contains:

* ``ROLE_TAXONOMY``  — roles used to *deduce* an employee's job title from the
  skills actually extracted from their CV (never to invent skills).
* ``CATEGORY_TO_TITLE`` — fallback title when a CV yields no recognisable skill.
* ``PROJECT_BLUEPRINTS`` — realistic projects (completed / active / upcoming)
  with their roles, budgets and margins.
* ``OPEN_DEMANDS`` — the open roles on the present-day project that the
  forecast agent is tested against.

The skill vocabulary here is deliberately aligned with what the bundled résumé
corpus actually contains (SQL/Java/Oracle/Linux/JavaScript/SAP/.NET/…), so the
agent can produce meaningful allocate/train decisions. One niche role
(Site Reliability Engineer) intentionally asks for skills nobody on the bench
has, to exercise the external-hire path; one role is a partial match, to
exercise the training path.

Nothing here hard-codes employee skills: employee skills always come from the
CV text via ``src.agents.nodes._extract_skills_from_text``.
"""

# Skills that describe soft skills / non-technical traits. They are excluded
# from CV-derived skill enrichment so technical profiles stay focused.
SOFT_SKILLS = {
    "leadership",
    "communication",
    "customer service",
    "sales",
    "marketing",
    "recruiting",
    "hr",
    "payroll",
    "compliance",
    "accounting",
    "finance",
    "business analysis",
}

# ---------------------------------------------------------------------------
# Role taxonomy — used ONLY to infer a job title from CV-extracted skills.
# ---------------------------------------------------------------------------
ROLE_TAXONOMY = [
    {"title": "Java Backend Developer", "skills": ["Java", "Spring", "SQL", "REST", "Microservices"], "department": "Software Engineering"},
    {"title": "Python Developer", "skills": ["Python", "SQL", "REST", "Linux"], "department": "Software Engineering"},
    {"title": "Frontend Developer", "skills": ["JavaScript", "HTML", "CSS", "jQuery", "Bootstrap"], "department": "Software Engineering"},
    {"title": ".NET Developer", "skills": ["C#", ".NET", "ASP.NET", "SQL Server", "Azure"], "department": "Software Engineering"},
    {"title": "Database Developer", "skills": ["SQL", "Oracle", "MySQL", "SQL Server"], "department": "Data & AI"},
    {"title": "Data Analyst", "skills": ["SQL", "Excel", "Tableau", "Data Analysis", "Statistics"], "department": "Data & AI"},
    {"title": "Data Scientist", "skills": ["Python", "Machine Learning", "Statistics", "SQL"], "department": "Data & AI"},
    {"title": "ETL Developer", "skills": ["ETL", "SQL", "Data Warehouse", "Oracle"], "department": "Data & AI"},
    {"title": "Cloud Engineer", "skills": ["AWS", "Linux", "Networking", "CI/CD", "Git"], "department": "Cloud & DevOps"},
    {"title": "Systems/Network Engineer", "skills": ["Linux", "Networking", "Active Directory", "TCP/IP", "Firewall"], "department": "Cloud & DevOps"},
    {"title": "Security Analyst", "skills": ["Cybersecurity", "Firewall", "TCP/IP", "Encryption", "SIEM"], "department": "Cyber Security"},
    {"title": "QA Engineer", "skills": ["Selenium", "QA", "Manual Testing", "Test Automation"], "department": "QA & Testing"},
    {"title": "SAP Consultant", "skills": ["SAP", "Oracle ERP", "SQL", "Business Analysis"], "department": "Software Engineering"},
    {"title": "Business Analyst", "skills": ["Business Analysis", "Stakeholder Management", "SQL", "Excel", "Agile"], "department": "Data & AI"},
]

# Fallback: dataset category → human-readable title.
CATEGORY_TO_TITLE = {
    "data-science": "Data Scientist",
    "web-designing": "Frontend Developer",
    "java-developer": "Java Backend Developer",
    "sap-developer": "SAP Consultant",
    "automation-testing": "QA Engineer",
    "python-developer": "Python Developer",
    "devops-engineer": "Cloud Engineer",
    "network-security-engineer": "Security Analyst",
    "database": "Database Developer",
    "hadoop": "Big Data Developer",
    "etl-developer": "ETL Developer",
    "dotnet-developer": ".NET Developer",
    "blockchain": "Blockchain Developer",
    "testing": "QA Engineer",
    "information-technology": "IT Support Specialist",
}

_SENIORITY_PREFIX = {
    "junior": "Junior ",
    "mid": "",
    "senior": "Senior ",
    "lead": "Lead ",
    "principal": "Principal ",
}


def title_with_seniority(base_title: str, experience_level: str) -> str:
    """Prefix a role title with a seniority word (e.g. 'Senior Data Analyst')."""
    prefix = _SENIORITY_PREFIX.get(str(experience_level or "mid").lower(), "")
    return f"{prefix}{base_title}".strip()


def infer_role_from_skills(skills, category: str = "") -> tuple:
    """Deduce ``(job_title, department)`` from CV-extracted skills.

    Scores every taxonomy entry by how many of its skills appear in the CV
    skill list and returns the best match. Falls back to the dataset category
    mapping, then to a generic IT title.
    """
    emp = {str(s).strip().lower() for s in (skills or [])}
    best = None
    best_score = 0
    for role in ROLE_TAXONOMY:
        score = sum(1 for s in role["skills"] if s.strip().lower() in emp)
        if score > best_score:
            best_score = score
            best = role

    if best and best_score > 0:
        return best["title"], best["department"]

    fallback = CATEGORY_TO_TITLE.get(str(category or "").lower().strip(), "IT Support Specialist")
    return fallback, "Software Engineering"


# ---------------------------------------------------------------------------
# Project blueprints
#
# status:  "completed" (history) | "active" (in progress) | "Pipeline" (upcoming)
# Each role: title, required_skills, bill_rate, target_margin, headcount, fill
#   fill = number of seats already staffed (allocations); the remainder becomes
#   an open ProjectDemand.
# ---------------------------------------------------------------------------
def _role(title, skills, bill_rate, margin, headcount=1, fill=0):
    return {
        "title": title,
        "required_skills": skills,
        "bill_rate": bill_rate,
        "target_margin": margin,
        "headcount": headcount,
        "fill": fill,
    }


PROJECT_BLUEPRINTS = [
    # ---------------- Completed (history) ----------------
    {
        "name": "Nimbus Infrastructure Consolidation",
        "client": "Aurora Retail Group",
        "status": "completed",
        "probability": 100,
        "target_margin": 24.0,
        "total_budget": 1_200_000.0,
        "start_days_ago": 400,
        "duration_days": 280,
        "roles": [
            _role("Systems/Network Engineer", ["Linux", "Networking", "Active Directory", "TCP/IP"], 110, 24, headcount=12, fill=12),
            _role("Cloud Engineer", ["AWS", "Linux", "Networking", "CI/CD"], 115, 25, headcount=10, fill=10),
            _role("QA Engineer", ["Selenium", "QA", "Manual Testing"], 88, 23, headcount=8, fill=8),
        ],
    },
    {
        "name": "Atlas Enterprise Data Warehouse",
        "client": "Global Bank",
        "status": "completed",
        "probability": 100,
        "target_margin": 27.0,
        "total_budget": 950_000.0,
        "start_days_ago": 520,
        "duration_days": 300,
        "roles": [
            _role("ETL Developer", ["ETL", "SQL", "Data Warehouse", "Oracle"], 112, 27, headcount=12, fill=12),
            _role("Database Developer", ["SQL", "Oracle", "SQL Server"], 108, 26, headcount=9, fill=9),
            _role("Business Analyst", ["Business Analysis", "SQL", "Excel"], 85, 26, headcount=5, fill=5),
        ],
    },
    {
        "name": "Orion Digital Banking Portal",
        "client": "Meridian Bank",
        "status": "completed",
        "probability": 100,
        "target_margin": 26.0,
        "total_budget": 1_450_000.0,
        "start_days_ago": 640,
        "duration_days": 300,
        "roles": [
            _role("Frontend Developer", ["JavaScript", "HTML", "CSS", "jQuery"], 100, 26, headcount=10, fill=10),
            _role("Java Backend Developer", ["Java", "Spring", "SQL", "REST"], 118, 27, headcount=10, fill=10),
            _role("QA Engineer", ["Selenium", "Manual Testing", "QA"], 90, 25, headcount=7, fill=7),
        ],
    },
    {
        "name": "Vega Customer Analytics Platform",
        "client": "Retail Metrics Ltd",
        "status": "completed",
        "probability": 100,
        "target_margin": 28.0,
        "total_budget": 720_000.0,
        "start_days_ago": 360,
        "duration_days": 190,
        "roles": [
            _role("Data Analyst", ["SQL", "Excel", "Tableau"], 96, 28, headcount=9, fill=9),
            _role("Database Developer", ["SQL", "Oracle", "MySQL"], 108, 28, headcount=8, fill=8),
            _role("Frontend Developer", ["JavaScript", "HTML", "CSS"], 98, 27, headcount=7, fill=7),
        ],
    },
    {
        "name": "Helios Payments Modernisation",
        "client": "FinTech Partners",
        "status": "completed",
        "probability": 100,
        "target_margin": 25.0,
        "total_budget": 1_100_000.0,
        "start_days_ago": 700,
        "duration_days": 260,
        "roles": [
            _role("Java Backend Developer", ["Java", "Spring", "SQL"], 120, 25, headcount=11, fill=11),
            _role("Security Analyst", ["Cybersecurity", "Firewall", "Encryption"], 122, 26, headcount=8, fill=8),
            _role(".NET Developer", ["C#", ".NET", "ASP.NET", "SQL Server"], 116, 25, headcount=9, fill=9),
        ],
    },
    # ---------------- Active (in progress) ----------------
    {
        "name": "Titan Risk Reporting Engine",
        "client": "Meridian Bank",
        "status": "active",
        "probability": 100,
        "target_margin": 29.0,
        "total_budget": 1_350_000.0,
        "start_days_ago": 95,
        "duration_days": 240,
        "roles": [
            _role("Database Developer", ["SQL", "Oracle", "SQL Server"], 110, 29, headcount=8, fill=8),
            _role("Java Backend Developer", ["Java", "Spring", "REST"], 116, 28, headcount=8, fill=8),
            _role("Data Analyst", ["SQL", "Excel", "Statistics"], 98, 30, headcount=5, fill=5),
            _role("QA Engineer", ["Selenium", "QA", "Test Automation"], 90, 26, headcount=4, fill=4),
            _role("Systems/Network Engineer", ["Linux", "Networking", "Firewall"], 108, 28, headcount=5, fill=5),
        ],
    },
    {
        "name": "Meridian Logistics Control Tower",
        "client": "Meridian Logistics",
        "status": "active",
        "probability": 100,
        "target_margin": 27.0,
        "total_budget": 2_100_000.0,
        "start_days_ago": 70,
        "duration_days": 260,
        "roles": [
            _role("Frontend Developer", ["JavaScript", "HTML", "CSS"], 100, 26, headcount=6, fill=6),
            _role("Java Backend Developer", ["Java", "Spring", "SQL"], 116, 28, headcount=6, fill=6),
            _role("Database Developer", ["SQL", "MySQL", "Oracle"], 106, 27, headcount=6, fill=6),
            _role("Systems/Network Engineer", ["Linux", "Networking", "TCP/IP"], 108, 27, headcount=5, fill=5),
            _role("Security Analyst", ["Cybersecurity", "SIEM", "TCP/IP"], 120, 28, headcount=3, fill=3),
            _role("Business Analyst", ["Business Analysis", "Stakeholder Management", "Agile"], 88, 25, headcount=4, fill=4),
        ],
    },
    {
        "name": "Aurora Retail Omnichannel Platform",
        "client": "Aurora Retail Group",
        "status": "active",
        "probability": 100,
        "target_margin": 28.0,
        "total_budget": 2_400_000.0,
        "start_days_ago": 130,
        "duration_days": 300,
        "roles": [
            _role("Java Backend Developer", ["Java", "Spring", "SQL"], 108, 28, headcount=6, fill=6),
            _role("Frontend Developer", ["JavaScript", "HTML", "CSS"], 100, 27, headcount=5, fill=5),
            _role("Database Developer", ["SQL", "Oracle", "MySQL"], 106, 28, headcount=4, fill=4),
            _role("Systems/Network Engineer", ["Linux", "Networking", "Active Directory"], 108, 28, headcount=3, fill=3),
            _role("QA Engineer", ["Selenium", "QA", "Manual Testing"], 90, 26, headcount=2, fill=2),
        ],
    },
    # ---------------- Upcoming (Pipeline) ----------------
    {
        "name": "Zenith Analytics Modernisation",
        "client": "Global Bank",
        "status": "Pipeline",
        "probability": 60,
        "target_margin": 32.0,
        "total_budget": 1_800_000.0,
        "start_days_ago": -45,
        "duration_days": 300,
        "roles": [
            _role("Data Analyst", ["SQL", "Tableau", "Statistics"], 104, 32, headcount=2, fill=0),
            _role("ETL Developer", ["ETL", "SQL", "Data Warehouse"], 112, 30, headcount=1, fill=0),
        ],
    },
    {
        "name": "Pegasus Reporting Automation",
        "client": "Retail Metrics Ltd",
        "status": "Pipeline",
        "probability": 55,
        "target_margin": 30.0,
        "total_budget": 1_200_000.0,
        "start_days_ago": -60,
        "duration_days": 260,
        "roles": [
            _role("Database Developer", ["SQL", "SQL Server"], 110, 30, headcount=2, fill=0),
            _role("Business Analyst", ["Business Analysis", "SQL", "Excel"], 90, 29, headcount=1, fill=0),
        ],
    },
    {
        "name": "Phoenix Core Banking Replacement",
        "client": "Meridian Bank",
        "status": "Pipeline",
        "probability": 45,
        "target_margin": 31.0,
        "total_budget": 3_200_000.0,
        "start_days_ago": -90,
        "duration_days": 420,
        "roles": [
            _role("Java Backend Developer", ["Java", "Spring", "SQL"], 125, 31, headcount=3, fill=0),
            _role(".NET Developer", ["C#", ".NET", "SQL Server"], 118, 30, headcount=2, fill=0),
            _role("Security Analyst", ["Cybersecurity", "SIEM", "Encryption"], 126, 32, headcount=1, fill=0),
        ],
    },
]

# ---------------------------------------------------------------------------
# Open demands on the present-day target project (used by the forecast agent).
#
# Roles 1–6 map onto skills the bench actually has (→ direct allocation).
# Role 7 is a partial match (→ training). Role 8 is deliberately unstaffable
# (→ external hire).
# ---------------------------------------------------------------------------
PRIMARY_PROJECT_NAME = "Aurora Retail Omnichannel Platform"

OPEN_DEMANDS = [
    {
        "role": "Senior Java Backend Developer",
        "required_skills": ["Java", "Spring", "SQL", "REST"],
        "target_bill_rate": 118.0,
        "target_margin": 30.0,
        "win_probability": 0.90,
        "needed_in_days": 14,
        "description": "Own the core Java/Spring services powering the omnichannel checkout.",
    },
    {
        "role": "Database Developer",
        "required_skills": ["SQL", "Oracle", "MySQL"],
        "target_bill_rate": 108.0,
        "target_margin": 28.0,
        "win_probability": 0.85,
        "needed_in_days": 21,
        "description": "Design schemas and tune queries across Oracle and MySQL estates.",
    },
    {
        "role": "Frontend Developer",
        "required_skills": ["JavaScript", "HTML", "CSS", "jQuery"],
        "target_bill_rate": 100.0,
        "target_margin": 27.0,
        "win_probability": 0.85,
        "needed_in_days": 30,
        "description": "Build and maintain the customer-facing web storefront.",
    },
    {
        "role": "Systems/Network Engineer",
        "required_skills": ["Linux", "Networking", "TCP/IP", "Firewall"],
        "target_bill_rate": 110.0,
        "target_margin": 28.0,
        "win_probability": 0.82,
        "needed_in_days": 45,
        "description": "Operate and secure the retail network and Linux server estate.",
    },
    {
        "role": "Data Analyst",
        "required_skills": ["SQL", "Excel", "Tableau", "Data Analysis"],
        "target_bill_rate": 96.0,
        "target_margin": 27.0,
        "win_probability": 0.80,
        "needed_in_days": 60,
        "description": "Deliver trading and merchandising insight to the retail business.",
    },
    {
        "role": "QA Engineer",
        "required_skills": ["Selenium", "QA", "Manual Testing", "SQL"],
        "target_bill_rate": 90.0,
        "target_margin": 25.0,
        "win_probability": 0.85,
        "needed_in_days": 21,
        "description": "Own regression and manual QA for the omnichannel releases.",
    },
    {
        "role": "Python Data Engineer",
        "required_skills": ["Python", "SQL", "Pandas", "NumPy"],
        "target_bill_rate": 112.0,
        "target_margin": 29.0,
        "win_probability": 0.80,
        "needed_in_days": 40,
        "description": "Build Python data pipelines with Pandas/NumPy for demand forecasting.",
    },
    {
        "role": "Site Reliability Engineer",
        "required_skills": ["Kubernetes", "Prometheus", "Grafana", "Go"],
        "target_bill_rate": 125.0,
        "target_margin": 31.0,
        "win_probability": 0.80,
        "needed_in_days": 75,
        "description": "Run the observability stack and Go-based reliability tooling.",
    },
]

# Seniority → (salary_min, salary_max) annual EUR bands. Salary is therefore
# derived from the experience level that is itself deduced from the CV.
SALARY_BANDS = {
    "junior": (38_000.0, 50_000.0),
    "mid": (55_000.0, 74_000.0),
    "senior": (85_000.0, 112_000.0),
    "lead": (115_000.0, 142_000.0),
    "principal": (145_000.0, 185_000.0),
}

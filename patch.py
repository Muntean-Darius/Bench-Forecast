with open("bench_forecast/src/services/finance.py", "r") as f:
    code = f.read()

code = code.replace(
    "from src.database.financial_models import BenchCost, EmployeeCost, ProjectFinancial",
    "from src.database.models import BenchCost, EmployeeCost, Project"
)

new_func = """def get_project_budget_consumed(
    db: Session,
    project_id: UUID,
) -> dict:
    stmt = select(Project).where(Project.id == project_id)
    pf = db.execute(stmt).scalar_one_or_none()

    if pf is None:
        return {
            "project_id": str(project_id),
            "error": "No project record found",
        }

    total = pf.total_budget
    consumed = 0.0  # Legacy ProjectFinancial had this, fallback to 0

    remaining = float(total) - consumed
    pct = (consumed / float(total) * 100) if total > 0 else 0.0

    return {
        "project_id": str(project_id),
        "billing_type": "T&M",
        "budget_total": float(total),
        "budget_consumed": consumed,
        "budget_remaining": remaining,
        "pct_consumed": round(pct, 2),
        "currency": "USD",
        "effective_date": "2026-01-01",
    }"""

import re
code = re.sub(r'def get_project_budget_consumed\(.*?return \{.*?\n    \}', new_func, code, flags=re.DOTALL)

with open("bench_forecast/src/services/finance.py", "w") as f:
    f.write(code)

with open("bench_forecast/src/api/routers/finance.py", "r") as f:
    code = f.read()

code = code.replace(
    "from src.database.financial_models import BenchCost, EmployeeCost, ProjectFinancial",
    "from src.database.models import BenchCost, EmployeeCost, Project"
)

import re

# Remove create_project_financial endpoint
code = re.sub(
    r'@router\.post\(\n\s*"/project-financials".*?def create_project_financial\(.*?\).*?ProjectFinancialRead:\n.*?except IntegrityError.*?from exc',
    '',
    code,
    flags=re.DOTALL
)

with open("bench_forecast/src/api/routers/finance.py", "w") as f:
    f.write(code)

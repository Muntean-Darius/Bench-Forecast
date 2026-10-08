import asyncio
from sqlalchemy import select
from decimal import Decimal
from src.database.database import SyncSessionLocal
from src.database.models import Employee, ExperienceLevelEnum
import logging

logging.basicConfig(level=logging.INFO)

COSTS = {
    ExperienceLevelEnum.junior: Decimal("45.00"),
    ExperienceLevelEnum.mid: Decimal("70.00"),
    ExperienceLevelEnum.senior: Decimal("110.00"),
    ExperienceLevelEnum.lead: Decimal("140.00"),
    ExperienceLevelEnum.principal: Decimal("170.00")
}

def main():
    with SyncSessionLocal() as db:
        result = db.execute(select(Employee))
        employees = result.scalars().all()
        for emp in employees:
            cost = COSTS.get(emp.experience_level, Decimal("85.00"))
            emp.hourly_cost_rate = cost
            print(f"Updated {emp.first_name} {emp.last_name} ({emp.experience_level}) to {cost}")
        db.commit()

if __name__ == "__main__":
    main()

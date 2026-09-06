from typing import Any, List
from src.schemas.models import Demand, Employee


class SQLiteManager:
    """SQLite relational database manager for deterministic records."""

    def __init__(self, db_path: str = "./data/mock_db.sqlite") -> None:
        ...

    def get_bench_employees(self) -> List[Employee]:
        ...

    def get_open_demands(self) -> List[Demand]:
        ...

    def update_allocation(self, employee_id: str, project_id: str) -> None:
        ...

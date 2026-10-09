import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date
from decimal import Decimal

from src.database.models import Employee, ProjectDemand, HitlReviewEvent, KpiSnapshot, BenchStatusEnum, HitlDecisionEnum

logger = logging.getLogger(__name__)

async def calculate_daily_kpis(session: AsyncSession) -> KpiSnapshot:
    """Calculates daily metrics and inserts a KpiSnapshot into the database."""
    
    # 1. Calculate bench_ratio_pct
    total_employees_query = select(func.count(Employee.id)).where(Employee.is_active == True)
    bench_employees_query = select(func.count(Employee.id)).where(
        Employee.is_active == True,
        Employee.bench_status.in_([BenchStatusEnum.bench, BenchStatusEnum.upcoming_bench])
    )
    
    total_emp_result = await session.execute(total_employees_query)
    total_employees = total_emp_result.scalar() or 0
    
    bench_emp_result = await session.execute(bench_employees_query)
    bench_employees = bench_emp_result.scalar() or 0
    
    bench_ratio_pct = Decimal("0.00")
    if total_employees > 0:
        bench_ratio_pct = Decimal(bench_employees) / Decimal(total_employees) * Decimal("100.00")
        
    # 2. Calculate mttr_days (Mean Time to Resolution)
    # Using created_at vs updated_at of filled/closed demands, or just placeholder query logic
    # For now we'll average the difference in days for demands that have been filled.
    # Note: Using SQLAlchemy's func to calculate date difference may vary by dialect.
    # We will compute it in Python for simplicity if needed, or use a basic SQL func.
    # Here we assume project_demands.status == 'filled' and compare needed_by_date or created_at to updated_at.
    mttr_query = select(ProjectDemand).where(ProjectDemand.status == 'filled')
    mttr_result = await session.execute(mttr_query)
    filled_demands = mttr_result.scalars().all()
    
    mttr_days = None
    if filled_demands:
        total_days = 0
        valid_demands = 0
        for demand in filled_demands:
            if demand.created_at and demand.updated_at:
                days = (demand.updated_at - demand.created_at).days
                total_days += days
                valid_demands += 1
        
        if valid_demands > 0:
            mttr_days = Decimal(total_days) / Decimal(valid_demands)

    # 3. Calculate human_intervention_pct
    # Percentage of AI proposals that managers rejected
    total_reviews_query = select(func.count(HitlReviewEvent.id))
    rejected_reviews_query = select(func.count(HitlReviewEvent.id)).where(
        HitlReviewEvent.decision == HitlDecisionEnum.rejected
    )
    
    total_rev_result = await session.execute(total_reviews_query)
    total_reviews = total_rev_result.scalar() or 0
    
    rejected_rev_result = await session.execute(rejected_reviews_query)
    rejected_reviews = rejected_rev_result.scalar() or 0
    
    human_intervention_pct = Decimal("0.00")
    if total_reviews > 0:
        human_intervention_pct = Decimal(rejected_reviews) / Decimal(total_reviews) * Decimal("100.00")

    # Create snapshot
    snapshot = KpiSnapshot(
        snapshot_date=date.today(),
        bench_ratio_pct=bench_ratio_pct.quantize(Decimal("0.01")),
        mttr_days=mttr_days.quantize(Decimal("0.01")) if mttr_days is not None else None,
        human_intervention_pct=human_intervention_pct.quantize(Decimal("0.01"))
    )
    
    session.add(snapshot)
    await session.commit()
    
    logger.info(f"Generated KPI Snapshot: Bench={bench_ratio_pct:.2f}%, MTTR={mttr_days}, Interv={human_intervention_pct:.2f}%")
    return snapshot

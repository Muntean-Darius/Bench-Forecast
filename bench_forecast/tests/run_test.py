"""Manual smoke test: run the forecast planner, then approve a proposal.

Not collected by pytest (function is ``manual_flow``). Run with:

    ./venv/bin/python tests/run_test.py
"""

import asyncio
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO)

from sqlalchemy import select

from src.database.database import AsyncSessionLocal
from src.database.models import Allocation, AllocationStatusEnum, User
from src.services.forecast_runner import run_forecast_pipeline
from src.services.hitl_service import HITLService


async def manual_flow():
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).limit(1))
    if not user:
        print("No users in the database — run the seeder first.")
        return
    user_id = user.id

    print("1. Running the forecast planner …")
    state = await run_forecast_pipeline(horizon_days=90, user_id=user_id)
    run_id = state.get("forecast_run_id")
    decisions = state.get("decisions") or []
    print(f"   run={run_id}  decisions={len(decisions)}")

    print("2. Approving the first pending proposal for this run …")
    async with AsyncSessionLocal() as db:
        alloc = await db.scalar(
            select(Allocation)
            .where(
                Allocation.forecast_run_id == uuid.UUID(run_id),
                Allocation.status == AllocationStatusEnum.pending_manager_approval,
            )
            .limit(1)
        )
        if not alloc:
            print("   no pending proposal was produced for this run.")
            return
        await HITLService(db).approve_allocation(alloc.id, reviewer_id=user_id)
        print(f"   approved allocation {alloc.id}")

    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Allocation, alloc.id)
        print(f"   status now: {refreshed.status.value}")


if __name__ == "__main__":
    asyncio.run(manual_flow())

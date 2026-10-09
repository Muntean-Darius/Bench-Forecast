import asyncio
import uuid
import logging
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

logging.basicConfig(level=logging.INFO)

from src.services.forecast_runner import run_forecast_pipeline
from src.agents.graph import build_forecast_graph
from langgraph.types import Command

from src.database.database import AsyncSessionLocal
from src.database.models import User
from sqlalchemy import select

async def test_manual_flow():
    # Setup or fetch a valid user from DB to satisfy foreign key constraints
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).limit(1))
        if not user:
            user = User(email=f"test_{uuid.uuid4()}@example.com", role="admin")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        user_id = user.id

    horizon_days = 90
    print("1. Triggering forecast pipeline...")
    
    paused_state = await run_forecast_pipeline(horizon_days, user_id)
    run_id = paused_state.get("forecast_run_id") or paused_state.get("run_id")
    print(f"\nPipeline paused. Run ID: {run_id}")
    
    print("\n2. Injecting human approval and resuming execution...")
    graph = build_forecast_graph()
    config = {"configurable": {"thread_id": str(run_id)}}
    approval_payload = {"human_approved": True, "rejection_feedback": "Looks good, approved."}
    
    # 1. Update the state with the human feedback
    await graph.aupdate_state(config, approval_payload)
    
    # 2. Resume execution from the paused node
    final_state = await graph.ainvoke(None, config=config)
    
    print(f"\nFinal State keys: {list(final_state.keys())}")
    print(f"Execution Status: {final_state.get('execution_status')}")

if __name__ == "__main__":
    asyncio.run(test_manual_flow())
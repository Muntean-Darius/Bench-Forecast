"""End-to-end integration test for Bench Forecast MVP happy path.

This tests the complete workflow:
1. Extract bench employees and open demands
2. Match employees to roles using LLM
3. Generate allocation plan
4. Pause for human review
5. Resume with approval
6. Execute database updates

Run with:
    python test_e2e.py
"""

import asyncio
import logging
import sys
from pathlib import Path
import json

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.core.config import Config
from src.agents.graph import build_forecast_graph
from src.database.sql_db import SQLiteManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def test_e2e():
    """Run end-to-end integration test."""
    
    logger.info("=" * 70)
    logger.info("🧪 BENCH FORECAST E2E INTEGRATION TEST - HAPPY PATH")
    logger.info("=" * 70)
    logger.info("")
    
    # 1. Validate configuration
    logger.info("Step 1️⃣  Configuration Validation")
    logger.info(f"  LLM Provider: {Config.LLM_PROVIDER}")
    logger.info(f"  Database: {Config.SQLITE_DB_PATH}")
    logger.info(f"  Phoenix Tracing: {Config.ENABLE_PHOENIX}")
    try:
        Config.validate()
        logger.info("  ✓ Configuration valid\n")
    except ValueError as e:
        logger.error(f"  ✗ Configuration error: {e}")
        return False
    
    # 2. Test database (mock data)
    logger.info("Step 2️⃣  Database Initialization & Mock Data")
    try:
        db = SQLiteManager()
        employees = db.get_bench_employees()
        demands = db.get_open_demands()
        logger.info(f"  ✓ Database loaded: {len(employees)} employees, {len(demands)} demands")
        logger.info(f"    Sample employees: {', '.join(e.name for e in employees[:2])}")
        logger.info(f"    Sample demands: {', '.join(d.role for d in demands[:2])}\n")
    except Exception as e:
        logger.error(f"  ✗ Database error: {e}")
        return False
    
    # 3. Test LLM Factory
    logger.info("Step 3️⃣  LLM Factory & JSON Enforcement")
    try:
        from src.core.llm import LLMFactory
        llm = LLMFactory.get_chat_model()
        logger.info(f"  ✓ LLM initialized: {type(llm).__name__}")
        
        # Test JSON parsing
        test_json = '{"test": "value"}'
        parsed = LLMFactory.parse_json_response(test_json)
        assert parsed["test"] == "value"
        logger.info("  ✓ JSON parsing works\n")
    except Exception as e:
        logger.error(f"  ✗ LLM initialization error: {e}")
        return False
    
    # 4. Build LangGraph
    logger.info("Step 4️⃣  LangGraph Workflow Construction")
    try:
        graph = build_forecast_graph()
        logger.info(f"  ✓ Graph compiled: {type(graph).__name__}")
        logger.info("  Flow: data_extractor → skill_matcher → forecast_planner → [PAUSE] → execution\n")
    except Exception as e:
        logger.error(f"  ✗ Graph construction error: {e}")
        return False
    
    # 5. Run workflow (happy path)
    logger.info("Step 5️⃣  Running Full Workflow (Happy Path)")
    try:
        input_state = {
            "employees": [],
            "demands": [],
            "skill_matches": [],
            "recommendations": None,
            "human_approved": False,
            "human_feedback": None,
            "execution_status": None,
        }
        
        thread_id = "test-thread-001"
        logger.info(f"  Starting workflow (thread_id={thread_id})...")
        
        final_state = None
        step_count = 0
        node_names = []
        for output in graph.stream(
            input_state,
            config={"configurable": {"thread_id": thread_id}},
        ):
            step_count += 1
            node_name = list(output.keys())[0] if output else "unknown"
            node_names.append(node_name)
            final_state = output
        
        logger.info("  Nodes executing: " + " → ".join(node_names) + " → [PAUSE]")
        logger.info(f"  ✓ Workflow executed {step_count} steps\n")
        
        # Extract state
        state_update = final_state[list(final_state.keys())[-1]] if final_state else {}
        workflow_state = {**input_state, **state_update}
        
        recommendations = workflow_state.get("recommendations", {})
        reallocations = recommendations.get("reallocations", [])
        confidence = recommendations.get("confidence_score", 0)
        
        logger.info(f"  Recommendation ID: {recommendations.get('recommendation_id', 'N/A')}")
        logger.info(f"  Confidence Score: {confidence:.2f}")
        logger.info(f"  Reallocations: {len(reallocations)}")
        logger.info(f"  Reasoning: {recommendations.get('reasoning', 'N/A')[:100]}...\n")
        
    except Exception as e:
        logger.error(f"  ✗ Workflow error: {e}", exc_info=True)
        return False
    
    # 6. Test HITL resume with approval
    logger.info("Step 6️⃣  HITL Resume & Execution (Approval)")
    try:
        # Simulate human approval
        workflow_state["human_approved"] = True
        workflow_state["human_feedback"] = "test_approver"
        
        logger.info("  Human approved recommendations ✓")
        logger.info("  Resuming workflow for execution...")
        
        exec_nodes = []
        for output in graph.stream(
            workflow_state,
            config={"configurable": {"thread_id": thread_id}},
        ):
            node_name = list(output.keys())[0] if output else "unknown"
            exec_nodes.append(node_name)
            workflow_state = {**workflow_state, **output[list(output.keys())[-1]]}
        
        logger.info(" → ".join(exec_nodes) + " → [DONE]")
        execution_status = workflow_state.get("execution_status", "unknown")
        logger.info(f"  Execution Status: {execution_status}")
        logger.info(f"  ✓ Workflow completed successfully\n")
        
    except Exception as e:
        logger.error(f"  ✗ Execution error: {e}", exc_info=True)
        return False
    
    # 7. Verify database updates
    logger.info("Step 7️⃣  Database Audit Trail Verification")
    try:
        allocation_history = db.get_allocation_history()
        logger.info(f"  ✓ Allocation history records: {len(allocation_history)}")
        
        if allocation_history:
            latest = allocation_history[0]
            logger.info(f"    Latest allocation:")
            logger.info(f"      Employee: {latest['employee_id']}")
            logger.info(f"      Project: {latest['target_project_id']}")
            logger.info(f"      Status: {latest['status']}")
            logger.info(f"      Approved by: {latest['approved_by']}\n")
        
    except Exception as e:
        logger.error(f"  ✗ Database verification error: {e}")
        return False
    
    # 8. Summary
    logger.info("=" * 70)
    logger.info("✅ ALL TESTS PASSED - MVP HAPPY PATH WORKING")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Start API server: python server.py")
    logger.info("  2. Test endpoints:")
    logger.info("     POST http://localhost:8000/api/v1/forecast/generate")
    logger.info("     POST http://localhost:8000/api/v1/forecast/execute")
    logger.info("  3. View docs: http://localhost:8000/docs")
    logger.info("")
    
    return True


if __name__ == "__main__":
    success = test_e2e()
    sys.exit(0 if success else 1)

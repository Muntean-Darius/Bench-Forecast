import logging
from typing import Dict, Any, List
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from src.database.models import ForecastRun, Allocation, ProjectDemand, Employee
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevance, context_recall
from datasets import Dataset

logger = logging.getLogger(__name__)

async def run_ragas_evaluation(session: AsyncSession, forecast_run_id: str) -> Dict[str, Any]:
    """
    Background service to evaluate AI proposals for a given forecast run using RAGAS.
    Fetches the demand description (question), retrieved profiles (contexts), and AI justification (answer).
    """
    try:
        run_uuid = uuid.UUID(forecast_run_id)
    except ValueError:
        logger.error(f"Invalid forecast_run_id format: {forecast_run_id}")
        return {}

    # Fetch the forecast run
    run_result = await session.execute(select(ForecastRun).where(ForecastRun.id == run_uuid))
    forecast_run = run_result.scalar_one_or_none()
    
    if not forecast_run:
        logger.warning(f"ForecastRun {forecast_run_id} not found.")
        return {}

    # Fetch allocations tied to this run
    allocations_result = await session.execute(
        select(Allocation)
        .where(Allocation.forecast_run_id == run_uuid)
    )
    allocations = allocations_result.scalars().all()

    if not allocations:
        logger.info(f"No allocations found for forecast_run {forecast_run_id}.")
        return {}

    questions = []
    contexts_list = []
    answers = []
    ground_truths = []

    for alloc in allocations:
        if not alloc.demand_id or not alloc.employee_id:
            continue
            
        # Fetch demand
        demand_result = await session.execute(select(ProjectDemand).where(ProjectDemand.id == alloc.demand_id))
        demand = demand_result.scalar_one_or_none()
        
        # Fetch employee
        emp_result = await session.execute(select(Employee).where(Employee.id == alloc.employee_id))
        employee = emp_result.scalar_one_or_none()
        
        if not demand or not employee:
            continue

        question = demand.description or demand.role
        answer = alloc.ai_justification or ""
        # The context could be the employee's profile text (which was retrieved from vector store)
        context = employee.profile_text or "No profile text available"
        
        questions.append(question)
        contexts_list.append([context])
        answers.append(answer)
        # Ragas context_recall often requires a ground_truth. We will use the question or required skills as a proxy.
        ground_truths.append([question])

    if not questions:
        logger.info(f"Not enough valid data to evaluate run {forecast_run_id}.")
        return {}

    data = {
        "question": questions,
        "contexts": contexts_list,
        "answer": answers,
        "ground_truths": ground_truths
    }

    dataset = Dataset.from_dict(data)

    try:
        logger.info(f"Running RAGAS evaluation on {len(questions)} allocation proposals...")
        result = evaluate(
            dataset=dataset,
            metrics=[faithfulness, answer_relevance, context_recall]
        )
        
        scores = result.to_pandas().mean().to_dict()
        f_score = scores.get("faithfulness", 0.0)
        ar_score = scores.get("answer_relevance", 0.0)
        cr_score = scores.get("context_recall", 0.0)
        
        # Update ForecastRun with scores
        forecast_run.ragas_faithfulness = Decimal(str(f_score)).quantize(Decimal("0.0001"))
        forecast_run.ragas_answer_relevance = Decimal(str(ar_score)).quantize(Decimal("0.0001"))
        forecast_run.ragas_context_recall = Decimal(str(cr_score)).quantize(Decimal("0.0001"))
        
        await session.commit()
        
        logger.info(f"RAGAS evaluation complete for {forecast_run_id}: Faithfulness={f_score:.4f}, Answer Relevance={ar_score:.4f}, Context Recall={cr_score:.4f}")
        return {
            "faithfulness": f_score,
            "answer_relevance": ar_score,
            "context_recall": cr_score
        }
        
    except Exception as e:
        logger.error(f"Error evaluating with RAGAS: {e}", exc_info=True)
        return {}

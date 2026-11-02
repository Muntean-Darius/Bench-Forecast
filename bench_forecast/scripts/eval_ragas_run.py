"""Run a real RAGAS evaluation on the AI allocation proposals produced by
actual forecast runs (allocations linked to a forecast_run_id).

Metrics (ragas 0.3.1):
  * faithfulness       — did the AI justification stick to what the model saw?
  * answer_relevancy   — is the justification relevant to the demand?
  * context_recall     — did the retrieved CV cover the required skills?

Grounding context mirrors what the app actually shows the LLM in
``FinancialMatchPrompts.build_role_evaluation_prompt`` (src/core/llm.py):

  1. ROLE block   — title, bill rate, target margin, job description
  2. CANDIDATE block — name, role, seniority, cost, projected margin, similarity
  3. retrieved CV excerpt (here the full employee profile_text)

faithfulness / answer_relevancy are judged against the FULL context (1+2+3),
because the justification legitimately cites role requirements and margin
figures from the prompt. context_recall is judged against the retrieved CV
alone (3), because it measures CV coverage of the required skills.

LLM for judging: DeepSeek (chat). Embeddings: local Ollama nomic-embed-text.
Results are printed and written back to forecast_runs.ragas_* columns.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from langchain_ollama import OllamaEmbeddings
from langchain_openai import ChatOpenAI
from ragas import evaluate
from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics._answer_relevance import ResponseRelevancy
from ragas.metrics._context_recall import ContextRecall
from ragas.metrics._faithfulness import Faithfulness
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import Config
from src.database.database import get_sync_db
from src.database.models import Allocation, Employee, ForecastRun, ProjectDemand


def _skills_txt(skills) -> str:
    return ", ".join(skills) if isinstance(skills, list) else str(skills or "")


def build_samples(db: Session) -> tuple[list[SingleTurnSample], list[dict]]:
    """One sample per real-run allocation, with both context variants."""
    rows = db.execute(
        select(Allocation, ProjectDemand, Employee, ForecastRun)
        .join(ProjectDemand, Allocation.demand_id == ProjectDemand.id)
        .join(Employee, Allocation.employee_id == Employee.id)
        .join(ForecastRun, Allocation.forecast_run_id == ForecastRun.id)
        .where(Allocation.ai_justification.isnot(None))
        .where(Allocation.forecast_run_id.isnot(None))
    ).all()

    full_samples: list[SingleTurnSample] = []
    cv_samples: list[SingleTurnSample] = []
    meta: list[dict] = []
    for alloc, demand, emp, run in rows:
        skills_txt = _skills_txt(demand.required_skills)
        question = f"Role: {demand.role}. Required skills: {skills_txt}."
        if demand.description:
            question += f" Context: {demand.description}"

        cv = emp.profile_text or "No profile text available"
        answer = (alloc.ai_justification or "").strip()
        if len(answer) < 10:
            continue

        # What the app actually shows the LLM (mirrors build_role_evaluation_prompt).
        role_block = (
            f"Open role: {demand.role}. Target bill rate: ${float(demand.target_bill_rate or 0):.2f}/h. "
            f"Target margin: {float(demand.target_margin or 0):.1f}% minimum. "
            f"Job description: {demand.description or demand.role}."
        )
        name = emp.full_name or f"{emp.first_name or ''} {emp.last_name or ''}".strip()
        candidate_block = (
            f"Candidate: {name} — {emp.primary_role or ''}, seniority {emp.seniority or ''}, "
            f"hourly cost ${float(emp.hourly_cost_rate or 0):.2f}/h, "
            f"projected margin {float(alloc.ai_projected_margin or 0):.1f}%, "
            f"similarity score {float(alloc.ai_match_score or 0):.2f}."
        )

        full_ctx = [role_block, candidate_block, cv]
        cv_ctx = [cv]

        full_samples.append(
            SingleTurnSample(
                user_input=question,
                retrieved_contexts=full_ctx,
                response=answer,
                reference=skills_txt,
            )
        )
        cv_samples.append(
            SingleTurnSample(
                user_input=question,
                retrieved_contexts=cv_ctx,
                response=answer,
                reference=skills_txt,
            )
        )
        meta.append(
            {
                "run_id": str(run.id),
                "run_date": run.run_date.date().isoformat(),
                "allocation_id": str(alloc.id),
                "allocation_status": alloc.status.value
                if hasattr(alloc.status, "value")
                else str(alloc.status),
                "employee": name,
                "role": demand.role,
            }
        )
    return (full_samples, cv_samples), meta


def _means(score_lists: list[list[float | None]]) -> dict[str, float | None]:
    out = {}
    for key, vals in score_lists.items():
        valid = [v for v in vals if v is not None]
        out[key] = round(sum(valid) / len(valid), 4) if valid else None
    return out


def _run_eval(samples, metrics, label) -> dict:
    print(f"Evaluating {len(samples)} proposals — {label} …")
    result = evaluate(
        dataset=EvaluationDataset(samples=samples),
        metrics=metrics,
        show_progress=False,
    )
    return result.to_pandas()


def main() -> None:
    api_key = Config.DEEPSEEK_API_KEY or ""
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY missing in .env")

    judge_llm = ChatOpenAI(
        model=Config.DEEPSEEK_MODEL or "deepseek-chat",
        base_url=Config.DEEPSEEK_BASE_URL or "https://api.deepseek.com/v1",
        api_key=api_key,
        temperature=0,
        timeout=120,
        max_retries=3,
    )
    llm = LangchainLLMWrapper(judge_llm)
    embeddings = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(
            model=Config.EMBEDDING_MODEL or "nomic-embed-text",
            base_url=Config.OLLAMA_BASE_URL or "http://localhost:11434",
        )
    )

    with get_sync_db() as db:
        (full_samples, cv_samples), meta = build_samples(db)

    if not full_samples:
        raise SystemExit("No real-run allocations with justifications found.")

    # Faithfulness + relevancy: judged against the FULL context the LLM saw.
    df_f = _run_eval(
        full_samples,
        [Faithfulness(llm=llm), ResponseRelevancy(llm=llm, embeddings=embeddings, strictness=2)],
        "faithfulness + answer_relevancy (full app context)",
    )
    # Context recall: judged against the retrieved CV alone.
    df_cr = _run_eval(cv_samples, [ContextRecall(llm=llm)], "context_recall (CV only)")

    per_proposal = []
    for i, m in enumerate(meta):
        row_f, row_cr = df_f.iloc[i], df_cr.iloc[i]
        per_proposal.append(
            {
                **m,
                "faithfulness": float(row_f["faithfulness"]),
                "answer_relevancy": float(row_f["answer_relevancy"]),
                "context_recall": float(row_cr["context_recall"]),
            }
        )

    print("\nPer proposal:")
    for p in per_proposal:
        print(
            f"  {p['run_date']}  {p['employee']:<22} {p['role']:<30} "
            f"{p['allocation_status']:<26} "
            f"f={p['faithfulness']:.3f} ar={p['answer_relevancy']:.3f} "
            f"cr={p['context_recall']:.3f}"
        )

    per_run: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for p in per_proposal:
        for k in ("faithfulness", "answer_relevancy", "context_recall"):
            per_run[p["run_id"]][k].append(p[k])

    summary = {rid: _means(cols) for rid, cols in per_run.items()}
    print("\nPer run:")
    for rid, means in summary.items():
        print(f"  run {rid[:8]}: {means}")

    flat = defaultdict(list)
    for cols in per_run.values():
        for k, vals in cols.items():
            flat[k].extend(vals)
    overall = _means(flat)
    print(f"\nOverall: {overall}")

    # Persist per-run scores back into forecast_runs (provable in the app).
    with get_sync_db() as db:
        for run_id, means in summary.items():
            run = db.get(ForecastRun, uuid.UUID(run_id))
            if run is None:
                continue
            run.ragas_faithfulness = means.get("faithfulness")
            run.ragas_answer_relevance = means.get("answer_relevancy")
            run.ragas_context_recall = means.get("context_recall")
        db.commit()
    print("\nScores written to forecast_runs.ragas_* columns.")

    out = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "judge_llm": Config.DEEPSEEK_MODEL,
        "embeddings": Config.EMBEDDING_MODEL,
        "samples": len(per_proposal),
        "contexts": {
            "faithfulness": "role block + candidate block + retrieved CV (what the app shows the LLM)",
            "answer_relevancy": "user_input vs response",
            "context_recall": "retrieved CV only vs required skills",
        },
        "overall": overall,
        "per_run": summary,
        "per_proposal": per_proposal,
    }
    report_path = Path(__file__).resolve().parents[2] / "docs" / "ragas_results.json"
    report_path.write_text(json.dumps(out, indent=2))
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()

from typing import Any, Dict
from fastapi import FastAPI
from pydantic import BaseModel
from src.schemas.models import AllocationRecommendation

app = FastAPI(title="Bench Forecast API", version="1.0.0")


class GenerateForecastRequest(BaseModel):
    department_id: str = "ALL"


class ExecuteForecastRequest(BaseModel):
    recommendation_id: str
    approved: bool


class FeedbackRequest(BaseModel):
    recommendation_id: str
    feedback: str


@app.post("/api/v1/forecast/generate", response_model=AllocationRecommendation)
async def generate_forecast(payload: GenerateForecastRequest):
    """Trigger agentic bench forecast generation."""
    ...


@app.post("/api/v1/forecast/execute")
async def execute_forecast(payload: ExecuteForecastRequest) -> Dict[str, Any]:
    """Execute approved allocation actions."""
    ...


@app.post("/api/v1/forecast/feedback")
async def forecast_feedback(payload: FeedbackRequest) -> Dict[str, Any]:
    """Submit human-in-the-loop review feedback."""
    ...

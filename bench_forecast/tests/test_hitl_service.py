import os
import sys
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    DemandStatusEnum,
    Employee,
    ExperienceLevelEnum,
    HiringRecommendation,
    HiringRecommendationStatusEnum,
    ProjectDemand,
)
from src.services.hitl_service import HITLService


def _make_employee(name: str, skills: str, cost: float) -> Employee:
    emp = Employee(
        id=uuid.uuid4(),
        first_name=name,
        last_name="Tester",
        job_title="Engineer",
        bench_status="bench",
        experience_level=ExperienceLevelEnum.senior,
        is_active=True,
        skills=skills,
        profile_text=skills,
    )
    emp.employee_costs = []
    emp.hourly_cost_rate = Decimal(str(cost))
    return emp


@pytest.mark.asyncio
async def test_reject_allocation_proposes_replacement_for_same_demand():
    """Rejecting a proposal must surface a new candidate for the same role.

    Regression test for the reported bug where rejecting simply removed the
    proposal and left the role with no replacement.
    """
    session = AsyncMock()
    service = HITLService(session)

    demand_id = uuid.uuid4()
    rejected_employee_id = uuid.uuid4()

    demand = ProjectDemand(
        id=demand_id,
        project_id=uuid.uuid4(),
        role="Data Engineer",
        description="Build data pipelines with Python and Spark",
        status=DemandStatusEnum.open,
        headcount_needed=1,
        target_bill_rate=Decimal("150.00"),
        required_skills=["Python", "Spark"],
    )
    rejected = Allocation(
        id=uuid.uuid4(),
        employee_id=rejected_employee_id,
        demand_id=demand_id,
        role_on_project="Data Engineer",
        allocation_pct=Decimal("100.00"),
        status=AllocationStatusEnum.rejected_by_manager,
        forecast_run_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )

    weak = _make_employee("Weak", "Excel", 40.0)
    strong = _make_employee("Strong", "Python, Spark, Airflow", 95.0)

    taken_result = MagicMock()
    taken_result.scalars.return_value.all.return_value = [rejected_employee_id]
    busy_result = MagicMock()
    busy_result.scalars.return_value.all.return_value = []
    emp_result = MagicMock()
    emp_result.scalars.return_value.all.return_value = [weak, strong]

    session.get.side_effect = [demand]
    session.execute.side_effect = [taken_result, busy_result, emp_result]

    replacement = await service._propose_replacement(rejected)

    assert replacement is not None, "A replacement proposal should have been generated"
    assert replacement.demand_id == demand_id
    assert replacement.employee_id != rejected_employee_id

    # The strongest skill match (Python + Spark) must win.
    assert replacement.employee_id == strong.id
    assert replacement.status == AllocationStatusEnum.pending_manager_approval
    assert replacement.forecast_run_id == rejected.forecast_run_id
    assert replacement.ai_match_score is not None
    assert replacement.ai_projected_margin is not None
    assert session.add.called
    assert session.flush.await_count == 1


def _allocation_with_feedback(demand_id, feedback):
    return Allocation(
        id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        demand_id=demand_id,
        role_on_project="Site Reliability Engineer",
        allocation_pct=Decimal("100.00"),
        status=AllocationStatusEnum.rejected_by_manager,
        manager_notes=feedback,
    )


def _empty_result():
    r = MagicMock()
    r.scalars.return_value.all.return_value = []
    r.scalars.return_value.first.return_value = None
    return r


@pytest.mark.asyncio
async def test_propose_replacement_recommends_hire_when_no_candidates():
    """When every bench candidate is excluded, an external hire is recommended."""
    session = AsyncMock()
    service = HITLService(session)

    demand_id = uuid.uuid4()
    demand = ProjectDemand(
        id=demand_id,
        project_id=uuid.uuid4(),
        role="Data Engineer",
        status=DemandStatusEnum.open,
        headcount_needed=1,
        required_skills=["Python", "SQL"],
    )
    rejected = _allocation_with_feedback(demand_id, None)
    rejected.role_on_project = "Data Engineer"

    session.get.side_effect = [demand]
    session.execute.side_effect = [
        _empty_result(),   # demand allocations
        _empty_result(),   # busy allocations
        _empty_result(),   # bench candidates
        _empty_result(),   # existing open hire recommendation
    ]

    replacement = await service._propose_replacement(rejected)

    assert replacement is None
    # A HiringRecommendation must have been added instead.
    added = [c.args[0] for c in session.add.call_args_list]
    assert any(isinstance(obj, HiringRecommendation) for obj in added)
    rec = next(obj for obj in added if isinstance(obj, HiringRecommendation))
    assert rec.status == HiringRecommendationStatusEnum.open
    assert rec.role == "Data Engineer"


@pytest.mark.asyncio
async def test_propose_replacement_honours_feedback_skill_requirement():
    """A feedback skill nobody has (e.g. Go) must trigger an external hire."""
    session = AsyncMock()
    service = HITLService(session)

    demand_id = uuid.uuid4()
    demand = ProjectDemand(
        id=demand_id,
        project_id=uuid.uuid4(),
        role="Site Reliability Engineer",
        description="Improve platform reliability with observability and Go tooling",
        status=DemandStatusEnum.open,
        headcount_needed=1,
        required_skills=["Kubernetes", "Terraform"],
    )
    rejected = _allocation_with_feedback(
        demand_id, "I want someone who already knows Go"
    )

    # Bench candidate has Kubernetes/Terraform but NOT Go.
    candidate = _make_employee("NoGo", "Kubernetes, Terraform, Linux", 95.0)

    taken_result = MagicMock()
    taken_result.scalars.return_value.all.return_value = []
    busy_result = MagicMock()
    busy_result.scalars.return_value.all.return_value = []
    emp_result = MagicMock()
    emp_result.scalars.return_value.all.return_value = [candidate]

    session.get.side_effect = [demand]
    session.execute.side_effect = [taken_result, busy_result, emp_result, _empty_result()]

    replacement = await service._propose_replacement(rejected)

    assert replacement is None
    added = [c.args[0] for c in session.add.call_args_list]
    recs = [obj for obj in added if isinstance(obj, HiringRecommendation)]
    assert recs, "Expected an external-hire recommendation for the missing Go skill"
    assert "Go" in recs[0].required_skills

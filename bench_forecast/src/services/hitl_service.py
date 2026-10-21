import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.core.observability import workflow

from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    Employee,
    ForecastRun,
    HitlReviewEvent,
    HitlDecisionEnum,
    BenchStatusEnum,
    ProjectDemand,
    DemandStatusEnum,
    Project,
    HiringRecommendation,
    HiringRecommendationStatusEnum,
)

logger = logging.getLogger(__name__)

# Default internal hourly cost by seniority band, used when neither the
# employee_costs table nor the legacy hourly_cost_rate column is populated.
_SENIORITY_COST_DEFAULTS = {
    "junior": 40.0,
    "mid": 65.0,
    "senior": 95.0,
    "lead": 125.0,
    "principal": 160.0,
}

class HITLService:
    """Service to handle Human-in-the-Loop review actions for allocations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _review_workflow(self, allocation: Allocation, decision: str, reviewer_id: uuid.UUID):
        """Workflow boundary for one human approve/reject decision.

        Reusing the allocation's ``forecast_run_id`` joins the human decision to
        the same trace as the AI proposal (the platform treats retries and HITL
        actions as part of one logical run). Allocations created outside a
        forecast get their own stable ``hitl-<id>`` workflow id.
        """
        run_id = getattr(allocation, "forecast_run_id", None)
        return workflow(
            name="bench-forecast-review",
            workflow_id=str(run_id) if run_id else f"hitl-{allocation.id}",
            version="v2",
            context={
                "decision": decision,
                "allocation_id": str(allocation.id),
                "demand_id": str(allocation.demand_id) if allocation.demand_id else None,
                "role": allocation.role_on_project,
            },
            user_id=str(reviewer_id),
        )

    async def submit_proposal(self, allocation_id: uuid.UUID) -> Allocation:
        """Transitions an AI proposal to pending_manager_approval."""
        allocation = await self.session.get(Allocation, allocation_id)
        if not allocation:
            raise ValueError(f"Allocation {allocation_id} not found")
            
        if allocation.status != AllocationStatusEnum.proposed_by_ai:
            raise ValueError(f"Allocation must be 'proposed_by_ai', got '{allocation.status}'")

        allocation.status = AllocationStatusEnum.pending_manager_approval
        await self.session.commit()
        await self.session.refresh(allocation)
        return allocation

    async def _maybe_complete_run(self, forecast_run_id) -> None:
        """Mark a forecast run completed once it has no open proposals left."""
        if not forecast_run_id:
            return
        # The session is configured with autoflush=False, so flush the status
        # change before counting the run's remaining open proposals.
        await self.session.flush()
        remaining = await self.session.execute(
            select(func.count(Allocation.id)).where(
                Allocation.forecast_run_id == forecast_run_id,
                Allocation.status.in_([
                    AllocationStatusEnum.proposed_by_ai,
                    AllocationStatusEnum.pending_manager_approval,
                ]),
            )
        )
        if (remaining.scalar() or 0) == 0:
            run = await self.session.get(ForecastRun, forecast_run_id)
            if run and run.status in ("queued", "running", "paused_for_review"):
                run.status = "completed"

    async def approve_allocation(self, allocation_id: uuid.UUID, reviewer_id: uuid.UUID, time_to_decision_seconds: int = None) -> Allocation:
        """Approves an allocation, updating status and logging the event."""
        allocation = await self.session.get(Allocation, allocation_id)
        if not allocation:
            raise ValueError(f"Allocation {allocation_id} not found")

        if allocation.status == AllocationStatusEnum.approved_by_manager:
            return allocation

        if allocation.status not in [
            AllocationStatusEnum.pending_manager_approval,
            AllocationStatusEnum.proposed_by_ai
        ]:
            raise ValueError(f"Cannot approve allocation in status '{allocation.status}'")

        with self._review_workflow(allocation, "approve", reviewer_id):
            allocation.status = AllocationStatusEnum.approved_by_manager
            allocation.approved_by = reviewer_id
            allocation.approved_at = datetime.utcnow()

            # Update the associated Employee's bench status and current project
            employee = await self.session.get(Employee, allocation.employee_id)
            if employee:
                employee.bench_status = BenchStatusEnum.on_project
                employee.bench_start_date = None

                project_name = None
                if allocation.demand_id:
                    demand = await self.session.get(ProjectDemand, allocation.demand_id)
                    if demand:
                        if demand.project_id:
                            proj = await self.session.get(Project, demand.project_id)
                            if proj:
                                project_name = proj.name
                        if not project_name and demand.role:
                            project_name = f"Project ({demand.role})"

                        if demand.headcount_needed and demand.headcount_needed > 0:
                            demand.headcount_needed -= 1
                        if demand.headcount_needed <= 0:
                            demand.status = DemandStatusEnum.filled

                if not project_name:
                    project_name = allocation.role_on_project or "Assigned Project"

                employee.current_project = project_name

            event = HitlReviewEvent(
                forecast_run_id=allocation.forecast_run_id,
                allocation_id=allocation.id,
                reviewer_id=reviewer_id,
                decision=HitlDecisionEnum.approved,
                time_to_decision_seconds=time_to_decision_seconds,
                reviewed_at=datetime.utcnow()
            )
            self.session.add(event)
            await self._maybe_complete_run(allocation.forecast_run_id)
            await self.session.commit()

            # Re-fetch with joinedload for Pydantic serialization
            stmt = (
                select(Allocation)
                .options(
                    joinedload(Allocation.project_demand).joinedload(ProjectDemand.project),
                    joinedload(Allocation.employee).selectinload(Employee.employee_costs),
                )
                .where(Allocation.id == allocation_id)
            )
            res = await self.session.execute(stmt)
            return res.scalars().first()

    async def reject_allocation(self, allocation_id: uuid.UUID, reviewer_id: uuid.UUID, feedback: str, time_to_decision_seconds: int = None) -> Allocation:
        """Rejects an allocation with manager feedback.

        In addition to recording the rejection, this re-runs candidate
        selection for the rejected allocation's demand and persists a fresh
        ``pending_manager_approval`` proposal for the next-best bench
        candidate. This is what makes the UI's "Reject & Re-run AI" action
        surface a replacement proposal instead of simply emptying the slot.
        """
        allocation = await self.session.get(Allocation, allocation_id)
        if not allocation:
            raise ValueError(f"Allocation {allocation_id} not found")

        with self._review_workflow(allocation, "reject", reviewer_id):
            allocation.status = AllocationStatusEnum.rejected_by_manager
            allocation.manager_notes = feedback

            event = HitlReviewEvent(
                forecast_run_id=allocation.forecast_run_id,
                allocation_id=allocation.id,
                reviewer_id=reviewer_id,
                decision=HitlDecisionEnum.rejected,
                rejection_feedback=feedback,
                time_to_decision_seconds=time_to_decision_seconds,
                reviewed_at=datetime.utcnow()
            )
            self.session.add(event)

            # Generate a replacement proposal for the same role. A failure here
            # must NOT roll back the rejection itself, so it is isolated in a
            # try/except and only logged.
            try:
                replacement = await self._propose_replacement(allocation)
                if replacement:
                    logger.info(
                        f"reject_allocation: generated replacement proposal "
                        f"{replacement.id} for demand {allocation.demand_id}"
                    )
                else:
                    logger.info(
                        f"reject_allocation: no replacement candidate available "
                        f"for demand {allocation.demand_id}"
                    )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    f"reject_allocation: failed to generate replacement proposal "
                    f"for demand {allocation.demand_id}: {exc}",
                    exc_info=True,
                )

            await self._maybe_complete_run(allocation.forecast_run_id)
            await self.session.commit()

            stmt = (
                select(Allocation)
                .options(
                    joinedload(Allocation.project_demand).joinedload(ProjectDemand.project),
                    joinedload(Allocation.employee).selectinload(Employee.employee_costs),
                )
                .where(Allocation.id == allocation_id)
            )
            res = await self.session.execute(stmt)
            return res.scalars().first()

    async def _propose_replacement(self, rejected: Allocation) -> Optional[Allocation]:
        """Re-run candidate selection for a rejected allocation's demand.

        Excludes every employee already tied to the demand (proposed, pending,
        approved, active or previously rejected) and scores the remaining bench
        talent on skill coverage plus projected margin. The best candidate is
        persisted as a new ``pending_manager_approval`` proposal.
        """
        demand_id = rejected.demand_id
        if not demand_id:
            return None

        demand = await self.session.get(ProjectDemand, demand_id)
        if not demand or demand.status != DemandStatusEnum.open:
            return None
        if demand.headcount_needed is not None and demand.headcount_needed <= 0:
            return None

        # Reuse the deterministic scoring helpers from the agent nodes so the
        # replacement is evaluated exactly like an AI-proposed allocation.
        from src.agents.nodes import (
            _build_match_justification,
            _build_upskilling_path,
            _derive_required_skills,
            _employee_skill_profile,
            _extract_skills_from_text,
            _match_skills,
        )

        role_title = demand.role or rejected.role_on_project or "Engineer"

        # Normalise the demand's required skills.
        raw_skills = demand.required_skills or []
        if isinstance(raw_skills, dict):
            raw_skills = raw_skills.get("skills", list(raw_skills.keys()))
        required_skills = (
            [str(s) for s in raw_skills if s]
            if isinstance(raw_skills, (list, tuple, set))
            else []
        )
        if not required_skills:
            required_skills = _derive_required_skills(
                demand.role or "", demand.description or "", []
            )

        # Honour the manager's rejection feedback: any skill named in the
        # feedback becomes a HARD requirement for the replacement candidate.
        # e.g. "I want someone who already knows Go" → must-have ["Go"].
        feedback_text = (rejected.manager_notes or "").strip()
        feedback_skills = _extract_skills_from_text(feedback_text)
        if feedback_skills:
            logger.info(
                f"reject_allocation: feedback mandates {feedback_skills} for '{role_title}'"
            )

        scoring_skills = list(required_skills)
        for skill in feedback_skills:
            if skill.lower() not in {s.lower() for s in scoring_skills}:
                scoring_skills.append(skill)

        # Every employee already attached to this demand is off-limits — this
        # includes the allocation that was just rejected.
        excluded: set = {str(rejected.employee_id)}
        taken_res = await self.session.execute(
            select(Allocation.employee_id).where(Allocation.demand_id == demand_id)
        )
        for emp_id in taken_res.scalars().all():
            if emp_id:
                excluded.add(str(emp_id))

        # Also exclude anyone already in-flight or staffed elsewhere so the
        # replacement does not double-book a candidate across projects.
        busy_res = await self.session.execute(
            select(Allocation.employee_id).where(
                Allocation.status.in_([
                    AllocationStatusEnum.proposed_by_ai,
                    AllocationStatusEnum.pending_manager_approval,
                    AllocationStatusEnum.approved_by_manager,
                    AllocationStatusEnum.active,
                ])
            )
        )
        for emp_id in busy_res.scalars().all():
            if emp_id:
                excluded.add(str(emp_id))

        # Available bench talent, with each candidate's skill profile resolved
        # once so we can apply the feedback hard-filter and score in one pass.
        emp_res = await self.session.execute(
            select(Employee)
            .options(selectinload(Employee.employee_costs))
            .where(
                Employee.is_active == True,
                Employee.bench_status.in_([BenchStatusEnum.bench, BenchStatusEnum.upcoming_bench]),
            )
        )
        candidate_profiles = []
        for emp in emp_res.scalars().all():
            if str(emp.id) in excluded:
                continue
            profile = _employee_skill_profile({
                "skills": getattr(emp, "skills", None),
                "profile_text": emp.profile_text,
                "job_title": getattr(emp, "job_title", ""),
            })
            candidate_profiles.append((emp, profile))

        # Apply the feedback's hard requirements (must possess ALL of them).
        if feedback_skills:
            _matched, missing_feedback, _cov = (None, [], None)
            qualifying = []
            for emp, profile in candidate_profiles:
                _matched, missing_feedback, _cov = _match_skills(profile, feedback_skills)
                if not missing_feedback:
                    qualifying.append((emp, profile))
            candidate_profiles = qualifying

        # No bench employee (matching the manager's stated requirements) →
        # recommend an external hire instead of forcing a poor-fit candidate.
        if not candidate_profiles:
            reason = (
                f"No bench employee matches the required skills for '{role_title}'."
            )
            if feedback_skills:
                reason = (
                    f"Manager feedback requires {', '.join(feedback_skills)}, which no "
                    f"available bench employee possesses. External hire recommended for "
                    f"'{role_title}'."
                )
            await self._create_hiring_recommendation(
                demand=demand,
                rejected=rejected,
                role_title=role_title,
                skills=scoring_skills,
                justification=reason,
            )
            return None

        bill_rate = float(demand.target_bill_rate or 0.0)
        if bill_rate <= 0 and demand.win_probability:
            bill_rate = float(demand.win_probability) * 200
        if bill_rate <= 0:
            bill_rate = 100.0

        best = None
        best_score = -1.0
        for emp, profile in candidate_profiles:
            matched, missing, coverage = _match_skills(profile, scoring_skills)

            cost = 0.0
            costs = getattr(emp, "employee_costs", None) or []
            if costs:
                latest = sorted(
                    costs,
                    key=lambda c: getattr(c, "effective_date", None) or date.min,
                    reverse=True,
                )[0]
                cost = float(latest.internal_hourly_cost or 0.0)
            if cost <= 0 and getattr(emp, "hourly_cost_rate", None):
                cost = float(emp.hourly_cost_rate)
            if cost <= 0:
                sen = str(
                    emp.experience_level.value
                    if hasattr(emp.experience_level, "value")
                    else (emp.experience_level or "mid")
                ).lower()
                cost = _SENIORITY_COST_DEFAULTS.get(sen, 65.0)

            margin = round(((bill_rate - cost) / bill_rate) * 100, 2) if bill_rate > 0 else 0.0
            base = coverage if coverage is not None else 0.6
            # Weight skill coverage heavily, with a small margin tie-breaker so
            # equally-skilled candidates are ranked by financial fit.
            score = (0.85 * base) + (0.15 * min(1.0, max(0.0, margin / 100.0)))

            if score > best_score:
                best_score = score
                best = (emp, profile, matched, missing, cost, margin)

        if not best:
            return None

        emp, profile, matched, missing, _cost, margin = best
        upskilling = _build_upskilling_path(missing, role_title) if missing else None
        justification = _build_match_justification(
            f"{emp.first_name} {emp.last_name}".strip(),
            role_title,
            matched,
            missing,
            None,
            action="train" if missing else "allocate",
            candidate_skills=profile,
        )
        if feedback_text:
            justification = f"{justification} (Revised per manager feedback: \"{feedback_text}\")"

        replacement = Allocation(
            id=uuid.uuid4(),
            employee_id=emp.id,
            demand_id=demand_id,
            role_on_project=role_title,
            allocation_pct=Decimal("100.00"),
            status=AllocationStatusEnum.pending_manager_approval,
            ai_justification=justification,
            ai_match_score=Decimal(str(round(min(1.0, max(0.0, best_score)), 4))),
            ai_projected_margin=Decimal(str(margin)),
            ai_upskilling_path=upskilling,
            forecast_run_id=rejected.forecast_run_id,
            created_by=rejected.created_by,
            start_date=date.today(),
        )
        self.session.add(replacement)
        await self.session.flush()
        return replacement

    async def _create_hiring_recommendation(
        self,
        demand: ProjectDemand,
        rejected: Allocation,
        role_title: str,
        skills: list,
        justification: str,
    ) -> HiringRecommendation:
        """Persist an open external-hire recommendation for a demand."""
        # Avoid stacking duplicate open recommendations for the same demand.
        existing = await self.session.execute(
            select(HiringRecommendation).where(
                HiringRecommendation.demand_id == demand.id,
                HiringRecommendation.status == HiringRecommendationStatusEnum.open,
            )
        )
        rec = existing.scalars().first()
        if rec:
            rec.required_skills = skills
            rec.justification = justification
        else:
            rec = HiringRecommendation(
                id=uuid.uuid4(),
                project_id=getattr(demand, "project_id", None),
                demand_id=demand.id,
                role=role_title,
                required_skills=skills,
                headcount=1,
                justification=justification,
                status=HiringRecommendationStatusEnum.open,
                forecast_run_id=rejected.forecast_run_id,
                created_by=rejected.created_by,
            )
            self.session.add(rec)
        await self.session.flush()
        logger.info(
            f"reject_allocation: created external-hire recommendation for "
            f"'{role_title}' (demand {demand.id})"
        )
        return rec

    async def get_pending_hiring(self) -> List[HiringRecommendation]:
        """Fetch all open external-hire recommendations."""
        stmt = (
            select(HiringRecommendation)
            .options(joinedload(HiringRecommendation.project))
            .where(HiringRecommendation.status == HiringRecommendationStatusEnum.open)
            .order_by(HiringRecommendation.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def dismiss_hiring(self, recommendation_id: uuid.UUID) -> HiringRecommendation:
        """Mark an external-hire recommendation as dismissed."""
        rec = await self.session.get(HiringRecommendation, recommendation_id)
        if not rec:
            raise ValueError(f"Hiring recommendation {recommendation_id} not found")
        rec.status = HiringRecommendationStatusEnum.dismissed
        await self.session.commit()
        return rec

    async def get_pending_allocations(self) -> List[Allocation]:
        """Fetch all allocations awaiting manager review."""
        stmt = (
            select(Allocation)
            .options(
                joinedload(Allocation.project_demand).joinedload(ProjectDemand.project),
                joinedload(Allocation.employee).selectinload(Employee.employee_costs),
            )
            .where(
                Allocation.status.in_([
                    AllocationStatusEnum.pending_manager_approval,
                    AllocationStatusEnum.proposed_by_ai,
                ])
            )
            .order_by(Allocation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        allocations = list(result.scalars().all())

        # Resolve each employee's most recent hourly cost rate so the HITL UI can
        # render cost rate / margin without empty fields. Fallback chain:
        # employee_costs (latest) → legacy hourly_cost_rate → seniority default.
        sen_defaults = {
            "junior": 40.0,
            "mid": 65.0,
            "senior": 95.0,
            "lead": 125.0,
            "principal": 160.0,
        }
        for allocation in allocations:
            employee = allocation.employee
            if employee is None:
                continue

            cost_rate = None
            employee_costs = getattr(employee, "employee_costs", None) or []
            if employee_costs:
                latest = sorted(
                    employee_costs,
                    key=lambda c: getattr(c, "effective_date", None) or date.min,
                    reverse=True,
                )[0]
                if latest.internal_hourly_cost:
                    cost_rate = latest.internal_hourly_cost

            if cost_rate is None and employee.hourly_cost_rate:
                cost_rate = employee.hourly_cost_rate

            if cost_rate is None:
                sen = str(
                    employee.experience_level.value
                    if hasattr(employee.experience_level, "value")
                    else (employee.experience_level or "mid")
                ).lower()
                cost_rate = Decimal(str(sen_defaults.get(sen, 65.0)))

            cost_dec = Decimal(str(cost_rate))
            # Attach the resolved value for serialisation without marking the
            # mapped column dirty (this is a read-only GET endpoint; get_db
            # commits on success, so a plain assignment would persist a derived
            # value into the legacy column).
            set_committed_value(employee, "hourly_cost_rate", cost_dec)
            employee.cost_rate = cost_dec

        return allocations

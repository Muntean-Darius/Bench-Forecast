"""Pydantic v2 schemas — Phase 3 Financial Domain.

Covers:
  EmployeeCostCreate / EmployeeCostRead     — employee_costs table I/O
  ProjectFinancialCreate / ProjectFinancialRead — project_financials table I/O
  BenchCostRead                             — bench_costs table read-only (derived)
  BenchBurnCalculateRequest                 — trigger a bench cost calculation
  MonthlyBenchBurnResponse                  — /finance/bench-burn/monthly response
  ProjectBudgetResponse                     — /finance/projects/{id}/budget response
  OrgBenchSummaryResponse                   — /finance/bench-costs/summary response
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared config — all read schemas use orm_mode (from_attributes)
# ---------------------------------------------------------------------------

class _ReadBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# EmployeeCost schemas
# =============================================================================

class EmployeeCostCreate(BaseModel):
    """Payload to create a new employee cost record.

    `internal_daily_cost` and `internal_hourly_cost` are optional — if omitted
    the API will compute them from `annual_salary` and `overhead_multiplier`
    via `services.finance.compute_internal_costs()`.
    """

    model_config = ConfigDict(strict=False)

    employee_id: UUID = Field(..., description="Employee UUID")
    effective_date: date = Field(
        ...,
        description="Date from which this cost rate becomes active",
    )
    annual_salary: Decimal = Field(
        ...,
        gt=0,
        description="Gross annual salary (before overhead)",
    )
    currency: str = Field(
        default="EUR",
        min_length=3,
        max_length=3,
        description="ISO 4217 currency code",
    )
    overhead_multiplier: Decimal = Field(
        default=Decimal("1.35"),
        ge=Decimal("1.0"),
        description="Overhead factor (≥ 1.0). 1.35 = 35 % employer overhead.",
    )
    # Optional overrides — server will compute from salary if not provided
    internal_daily_cost: Optional[Decimal] = Field(
        default=None,
        gt=0,
        description=(
            "Fully-loaded daily cost. If None, computed as "
            "annual_salary / 220 * overhead_multiplier."
        ),
    )
    internal_hourly_cost: Optional[Decimal] = Field(
        default=None,
        gt=0,
        description=(
            "Hourly cost = internal_daily_cost / 8. "
            "If None, computed automatically."
        ),
    )


class EmployeeCostRead(_ReadBase):
    """Serialised employee cost record returned by the API."""

    id: UUID
    employee_id: UUID
    effective_date: date
    annual_salary: Decimal
    currency: str
    overhead_multiplier: Decimal
    internal_daily_cost: Decimal
    internal_hourly_cost: Decimal
    created_at: datetime


# =============================================================================
# ProjectFinancial schemas
# =============================================================================

class ProjectFinancialCreate(BaseModel):
    """Payload to create or update project financial configuration."""

    model_config = ConfigDict(strict=False)

    project_id: UUID = Field(..., description="Project UUID")
    billing_type: str = Field(
        default="time_and_materials",
        description="time_and_materials | fixed_price | internal",
    )
    client_daily_rate: Decimal = Field(
        ...,
        ge=0,
        description="Daily rate billed to the client per person",
    )
    budget_total: Decimal = Field(
        ...,
        ge=0,
        description="Total approved project budget",
    )
    budget_consumed: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        description="Budget consumed so far (defaults to 0 on creation)",
    )
    currency: str = Field(
        default="EUR",
        min_length=3,
        max_length=3,
        description="ISO 4217 currency code",
    )
    effective_date: date = Field(
        ...,
        description="Date from which this financial config is active",
    )


class ProjectFinancialRead(_ReadBase):
    """Serialised project financial record returned by the API."""

    id: UUID
    project_id: UUID
    billing_type: str
    client_daily_rate: Decimal
    budget_total: Decimal
    budget_consumed: Decimal
    currency: str
    effective_date: date


# =============================================================================
# BenchCost schemas (read-only — rows are created by the service engine)
# =============================================================================

class BenchCostRead(_ReadBase):
    """Serialised bench cost ledger row."""

    id: UUID
    employee_id: UUID
    period_start: date
    period_end: date
    bench_days: int
    daily_cost: Decimal
    total_cost: Decimal
    currency: str
    calculated_at: datetime


# =============================================================================
# Request schemas — trigger calculations
# =============================================================================

class BenchBurnCalculateRequest(BaseModel):
    """Trigger bench cost calculation for one employee over a date range."""

    model_config = ConfigDict(strict=False)

    employee_id: UUID = Field(..., description="Target employee UUID")
    period_start: date = Field(..., description="First day of bench period (inclusive)")
    period_end: date = Field(..., description="Last day of bench period (inclusive)")
    overwrite: bool = Field(
        default=False,
        description=(
            "If True, delete and recompute any existing BenchCost row for "
            "this exact period. Default False raises a 409 on conflict."
        ),
    )


# =============================================================================
# Dashboard response schemas
# =============================================================================

class MonthlyBenchBurnResponse(BaseModel):
    """Response for GET /finance/bench-burn/monthly."""

    year: int
    month: int
    period_start: str
    period_end: str
    total_bench_cost: float
    employee_count: int
    currency: str


class ProjectBudgetResponse(BaseModel):
    """Response for GET /finance/projects/{project_id}/budget."""

    project_id: str
    billing_type: Optional[str] = None
    budget_total: Optional[float] = None
    budget_consumed: Optional[float] = None
    budget_remaining: Optional[float] = None
    pct_consumed: Optional[float] = None
    currency: Optional[str] = None
    effective_date: Optional[str] = None
    error: Optional[str] = None


class EmployeeBenchCostItem(BaseModel):
    """Single bench cost entry in per-employee breakdown."""

    id: str
    employee_id: str
    period_start: str
    period_end: str
    bench_days: int
    daily_cost: float
    total_cost: float
    currency: str
    calculated_at: str


class OrgEmployeeBenchItem(BaseModel):
    """Per-employee aggregate in the org-wide bench summary."""

    employee_id: str
    total_cost: float
    total_bench_days: int
    currency: str


class OrgBenchSummaryResponse(BaseModel):
    """Response for GET /finance/bench-costs/summary."""

    period_start: str
    period_end: str
    grand_total_bench_cost: float
    employee_count: int
    currency: str
    employees: list[OrgEmployeeBenchItem]

/**
 * Centralized API client for the FastAPI backend at /api/v1/...
 * Vite proxy forwards /api → http://localhost:8000 in development.
 */

import type {
  Candidate,
  CvDiff,
  CvReviewSummary,
  CvStatus,
  EmployeeCapacity,
  ProjectDetail,
  ProjectSummary,
  TeamMember,
  TrainingAssignment,
} from '../types'

const BASE = '/api/v1'

// ─── Structured API errors ───────────────────────────────────────────────────

export interface ApiErrorDetail {
  code?: string
  message?: string
  [key: string]: unknown
}

export class ApiError extends Error {
  status: number
  detail: ApiErrorDetail | string

  constructor(status: number, detail: ApiErrorDetail | string) {
    const message =
      typeof detail === 'string'
        ? detail
        : detail?.message || JSON.stringify(detail)
    super(`API ${status}: ${message}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function parseError(res: Response): Promise<ApiError> {
  const text = await res.text()
  try {
    const parsed = JSON.parse(text)
    return new ApiError(res.status, parsed.detail ?? parsed)
  } catch {
    return new ApiError(res.status, text)
  }
}

// ─── Token helper ─────────────────────────────────────────────────────────────

function getToken(): string | null {
  return localStorage.getItem('bf_token')
}

// ─── Core request wrapper ─────────────────────────────────────────────────────

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
  })
  if (!res.ok) throw await parseError(res)
  return res.json() as Promise<T>
}

// ─── Multipart upload helper ──────────────────────────────────────────────────

async function uploadFile<T>(
  path: string,
  formData: FormData,
): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!res.ok) throw await parseError(res)
  return res.json() as Promise<T>
}

// ─── API Method Catalog ───────────────────────────────────────────────────────

export const api = {
  // ── Health ────────────────────────────────────────────────────────────────
  health: () =>
    request<{ status: string; version: string; llm_provider: string; phoenix_enabled: boolean }>('/health'),

  // ── Supply ────────────────────────────────────────────────────────────────
  getEmployees: (horizonDays = 90) =>
    request<any[]>(`/employees?horizon_days=${horizonDays}`),

  // ── Demand & Projects ─────────────────────────────────────────────────────
  getDemands: (minWinProbability = 0.75, horizonDays = 365) =>
    request<any[]>(`/demands?min_win_probability=${minWinProbability}&horizon_days=${horizonDays}`),

  getProjects: () =>
    request<ProjectSummary[]>('/projects'),

  getProject: (projectId: string) =>
    request<ProjectDetail>(`/projects/${projectId}`),

  getProjectRecommendations: (projectId: string, demandId: string) =>
    request<Candidate[]>(
      `/projects/${projectId}/recommendations?demand_id=${demandId}`,
    ),

  assignToDemand: (
    projectId: string,
    demandId: string,
    body: {
      employee_id: string
      allocated_hours_per_day?: number
      start_date?: string | null
      end_date?: string | null
      ai_justification?: string
    },
  ) =>
    request<any>(`/projects/${projectId}/demands/${demandId}/assign`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // ── Team & capacity ───────────────────────────────────────────────────────
  getTeam: () =>
    request<TeamMember[]>('/team'),

  getTeamCandidates: (demandId: string, minHours?: number) =>
    request<Candidate[]>(
      `/team/candidates?demand_id=${demandId}` +
        (minHours !== undefined ? `&min_hours=${minHours}` : ''),
    ),

  getEmployeeCapacity: (employeeId: string) =>
    request<EmployeeCapacity>(`/employees/${employeeId}/capacity`),

  getEmployeeSkills: (employeeId: string) =>
    request<Record<string, any>>(`/employees/${employeeId}/skills`),

  // ── CV reviews ────────────────────────────────────────────────────────────
  getCvReviews: (status?: string) =>
    request<CvReviewSummary[]>(
      `/cv-reviews${status ? `?status=${status}` : ''}`,
    ),

  getCvReview: (reviewId: string) =>
    request<CvDiff>(`/cv-reviews/${reviewId}`),

  approveCvReview: (reviewId: string, comment?: string) =>
    request<CvReviewSummary>(`/cv-reviews/${reviewId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ comment: comment ?? null }),
    }),

  rejectCvReview: (reviewId: string, comment: string) =>
    request<CvReviewSummary>(`/cv-reviews/${reviewId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ comment }),
    }),

  getCvStatus: (employeeId: string) =>
    request<CvStatus>(`/employees/${employeeId}/cv-status`),

  // ── Trainings ─────────────────────────────────────────────────────────────
  getTrainings: (params?: { employeeId?: string; status?: string }) => {
    const query = new URLSearchParams()
    if (params?.employeeId) query.set('employee_id', params.employeeId)
    if (params?.status) query.set('status', params.status)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<TrainingAssignment[]>(`/trainings${suffix}`)
  },

  createTraining: (body: {
    employee_id: string
    title: string
    targeted_skills: string[]
    ai_upskilling_path?: string
    project_id?: string | null
    demand_id?: string | null
    target_completion_date?: string | null
    notes?: string
  }) =>
    request<TrainingAssignment>('/trainings', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateTraining: (trainingId: string, body: Record<string, unknown>) =>
    request<TrainingAssignment>(`/trainings/${trainingId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  completeTraining: (trainingId: string) =>
    request<TrainingAssignment>(`/trainings/${trainingId}/complete`, {
      method: 'POST',
    }),

  // ── Forecast ──────────────────────────────────────────────────────────────
  runForecast: (horizonDays = 90, projectId?: string, minWinProbability = 0.75) =>
    request<{
      forecast_run_id: string
      status: string
      state_metadata: {
        revision_count: number
        decisions: any[]
        min_win_probability: number
      }
    }>('/forecast/run', {
      method: 'POST',
      body: JSON.stringify({
        horizon_days: horizonDays,
        project_id: projectId || null,
        min_win_probability: minWinProbability,
      }),
    }),

  getForecastRun: (runId: string) =>
    request<{ id: string; status: string; run_date: string; forecast_horizon_days: number; triggered_by: string }>(
      `/forecast/runs/${runId}`,
    ),

  // ── HITL Allocations ──────────────────────────────────────────────────────
  getPendingAllocations: () =>
    request<any[]>('/allocations/pending'),

  approveAllocation: (allocationId: string, reviewerId: string, timeToDecision: number) =>
    request<any>(`/allocations/${allocationId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ reviewer_id: reviewerId, time_to_decision_seconds: timeToDecision }),
    }),

  rejectAllocation: (allocationId: string, reviewerId: string, feedback: string, timeToDecision: number) =>
    request<any>(`/allocations/${allocationId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reviewer_id: reviewerId, feedback, time_to_decision_seconds: timeToDecision }),
    }),

  assignEmployee: (employeeId: string, demandId?: string, projectName?: string, roleOnProject?: string) =>
    request<any>('/allocations/assign', {
      method: 'POST',
      body: JSON.stringify({
        employee_id: employeeId,
        demand_id: demandId ?? null,
        project_name: projectName ?? null,
        role_on_project: roleOnProject ?? null,
      }),
    }),

  releaseEmployee: (employeeId: string) =>
    request<any>('/allocations/release', {
      method: 'POST',
      body: JSON.stringify({ employee_id: employeeId }),
    }),

  // ── External Hiring Recommendations ───────────────────────────────────────
  getPendingHiring: () =>
    request<any[]>('/hiring/pending'),

  dismissHiring: (recommendationId: string, reviewerId?: string) =>
    request<any>(`/hiring/${recommendationId}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ reviewer_id: reviewerId ?? null }),
    }),

  // ── Employee Portal ───────────────────────────────────────────────────────
  getEmployeePortal: (employeeId: string) =>
    request<any>(`/employee/portal/${employeeId}`),

  updateEmployeeProfile: (employeeId: string, profileText: string) =>
    request<{ status: string }>(`/employee/profile/${employeeId}`, {
      method: 'PUT',
      body: JSON.stringify({ profile_text: profileText }),
    }),

  changePassword: (oldPassword: string, newPassword: string) =>
    request<{ status: string }>('/employee/change-password', {
      method: 'POST',
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),

  // ── Documents ─────────────────────────────────────────────────────────────
  uploadDocument: (file: File, employeeId: string, documentType = 'cv') => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('employee_id', employeeId)
    formData.append('document_type', documentType)
    return uploadFile<any>('/documents/upload', formData)
  },

  getEmployeeDocuments: (employeeId: string) =>
    request<any[]>(`/documents/employee/${employeeId}`),

  // ── Analytics (dashboard time series) ─────────────────────────────────────
  getBenchTrend: (weeks = 8) =>
    request<{
      total_employees: number
      weeks: { week_start: string; label: string; on_bench: number; allocated: number }[]
    }>(`/analytics/bench-trend?weeks=${weeks}`),

  getBenchBurnTrend: (months = 6) =>
    request<{
      months: {
        year: number
        month: number
        label: string
        total_bench_cost: number
        total_workforce_cost: number
      }[]
    }>(`/analytics/bench-burn-trend?months=${months}`),

  getKpiTrend: (days = 30) =>
    request<{
      days: {
        date: string
        label: string
        bench_ratio_pct: number
        mttr_days: number | null
        human_intervention_pct: number
      }[]
    }>(`/analytics/kpi-trend?days=${days}`),

  getProjectMargins: () =>
    request<{
      projects: {
        project_id: string
        name: string
        status: string
        allocated_fte: number
        blended_bill_rate: number
        blended_cost_rate: number
        margin_pct: number
        target_margin: number | null
        monthly_revenue: number
        monthly_margin: number
      }[]
    }>('/analytics/project-margins'),

  // ── Finance ───────────────────────────────────────────────────────────────
  getMonthlyBenchBurn: (year: number, month: number) =>
    request<any>(`/finance/bench-burn/monthly?year=${year}&month=${month}`),

  getBenchCostsSummary: (periodStart: string, periodEnd: string) =>
    request<any>(`/finance/bench-costs/summary?period_start=${periodStart}&period_end=${periodEnd}`),

  getEmployeeCosts: (employeeId: string) =>
    request<any>(`/finance/employee-costs/${employeeId}`),

  getProjectBudget: (projectId: string) =>
    request<any>(`/finance/projects/${projectId}/budget`),

  // -- Project creation -------------------------------------------------------
  createProject: (payload: {
    name: string
    client_name?: string
    status?: string
    probability?: number
    target_margin?: number
    total_budget?: number
    start_date?: string | null
    end_date?: string | null
    initial_demands?: {
      role: string
      description?: string
      required_skills?: string[]
      headcount_needed?: number
      target_bill_rate?: number | null
      needed_by_date?: string | null
    }[]
  }) =>
    request<any>('/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- Employee creation -------------------------------------------------------
  createEmployee: (payload: {
    first_name: string
    last_name: string
    email: string
    job_title: string
    experience_level?: string
    employment_type?: string
    bench_status?: string
    hourly_cost_rate?: number
    skills?: string[]
    location?: string
  }) =>
    request<any>('/employees', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- Manager management (admin only) ----------------------------------------
  getManagers: () =>
    request<any[]>('/admin/managers'),

  createManager: (payload: {
    full_name: string
    email: string
    username: string
    password: string
    department_id?: string | null
    employee_id?: string | null
  }) =>
    request<any>('/admin/managers', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // -- Forecast SSE stream URL -------------------------------------------------
  getForecastStreamUrl: (runId: string) =>
    `${BASE}/forecast/runs/${runId}/stream?token=${encodeURIComponent(localStorage.getItem('bf_token') || '')}`,
}
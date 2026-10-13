/**
 * Centralized API client for the FastAPI backend at /api/v1/...
 * Vite proxy forwards /api → http://localhost:8000 in development.
 */

const BASE = '/api/v1'

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
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
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
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
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

  // ── Demand ────────────────────────────────────────────────────────────────
  getDemands: (minWinProbability = 0.75) =>
    request<any[]>(`/demands?min_win_probability=${minWinProbability}`),

  // ── Forecast ──────────────────────────────────────────────────────────────
  runForecast: (horizonDays = 90) =>
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
      body: JSON.stringify({ horizon_days: horizonDays }),
    }),

  getForecastRun: (runId: string) =>
    request<{ id: string; status: string; run_date: string; forecast_horizon_days: number; triggered_by: string }>(
      `/forecast/runs/${runId}`,
    ),

  reviewForecast: (runId: string, humanApproved: boolean, rejectionFeedback?: string) =>
    request<any>(`/forecast/${runId}/review`, {
      method: 'POST',
      body: JSON.stringify({
        human_approved: humanApproved,
        rejection_feedback: rejectionFeedback ?? null,
      }),
    }),

  // ── HITL Allocations ──────────────────────────────────────────────────────
  getPendingAllocations: () =>
    request<any[]>('/api/allocations/pending'),

  approveAllocation: (allocationId: string, reviewerId: string, timeToDecision: number) =>
    request<any>(`/api/allocations/${allocationId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ reviewer_id: reviewerId, time_to_decision_seconds: timeToDecision }),
    }),

  rejectAllocation: (allocationId: string, reviewerId: string, feedback: string, timeToDecision: number) =>
    request<any>(`/api/allocations/${allocationId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reviewer_id: reviewerId, feedback, time_to_decision_seconds: timeToDecision }),
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

  // ── Finance ───────────────────────────────────────────────────────────────
  getMonthlyBenchBurn: (year: number, month: number) =>
    request<any>(`/finance/bench-burn/monthly?year=${year}&month=${month}`),

  getBenchCostsSummary: (periodStart: string, periodEnd: string) =>
    request<any>(`/finance/bench-costs/summary?period_start=${periodStart}&period_end=${periodEnd}`),

  getEmployeeCosts: (employeeId: string) =>
    request<any>(`/finance/employee-costs/${employeeId}`),

  getProjectBudget: (projectId: string) =>
    request<any>(`/finance/projects/${projectId}/budget`),
}

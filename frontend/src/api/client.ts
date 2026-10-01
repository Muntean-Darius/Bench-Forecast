/**
 * API client for the FastAPI backend at /api/v1/...
 * In development, Vite's proxy forwards /api → http://localhost:8000
 */

import type {
  GenerateForecastRequest,
  ExecuteForecastRequest,
  ForecastResponse,
} from '../types'

const BASE = '/api/v1'

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
  return res.json() as Promise<T>
}

// ─── Forecast endpoints ───────────────────────────────────────────────────────

export const api = {
  health: () =>
    request<{ status: string; version: string; active_workflows: number }>('/health'),

  getEmployees: (horizonDays: number = 90) =>
    request<any[]>(`/employees?horizon_days=${horizonDays}`),

  getDemands: (minWinProbability: number = 0.75) =>
    request<any[]>(`/demands?min_win_probability=${minWinProbability}`),

  generateForecast: (payload: GenerateForecastRequest) =>
    request<ForecastResponse>('/forecast/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  executeForecast: (payload: ExecuteForecastRequest) =>
    request<ForecastResponse>('/forecast/execute', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  submitFeedback: (payload: {
    recommendation_id: string
    feedback: string
    approved: boolean
  }) =>
    request<{ status: string }>('/forecast/feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

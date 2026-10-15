// ─── Core domain types mirrored from backend API responses ───────────────────

/**
 * Employee as returned by GET /api/v1/employees
 * Fields: id, name, skills (array), current_project, available_from,
 *         experience_years, cost_rate, profile_text
 */
export interface Employee {
  id: string
  name?: string           // backend returns `name` not `full_name`
  full_name?: string     // alias used in some mapped contexts
  primary_role?: string  // UI-computed from skills[0]
  seniority?: string     // UI-computed from skills[1] when present
  hourly_cost_rate: number
  bench_start_date: string   // mapped from available_from
  available_from?: string
  cv_document_uri?: string | null
  profile_text?: string | null
  skills?: string[] | string | null
  experience_years?: number
  current_project?: string | null
  cost_rate?: number | null
  bench_status?: string | null
  job_title?: string | null
}

export interface Project {
  id: string
  name: string
  status: 'Active' | 'Pipeline' | 'Closed'
  probability: number           // 0-100
  target_margin: number         // percentage, e.g. 32.00
  total_budget: number          // EUR/USD
}

export type RoleStatus = 'Open' | 'Filled' | 'Requires Training'

export interface ProjectRole {
  id: string
  project_id: string
  title: string
  target_bill_rate: number      // hourly
  status: RoleStatus
  description?: string | null
  start_date: string            // ISO date string
  headcount: number
  required_skills?: string | null
  win_probability?: number
  // Joined from Project
  project_name?: string
  project_status?: Project['status']
}

export interface Allocation {
  id: string
  employee_id: string
  project_role_id: string
  start_date: string
  end_date: string
  projected_margin: number      // pre-computed: (bill - cost) / bill * 100
}

// ─── AI / HITL types ──────────────────────────────────────────────────────────

export type ProposalCategory = 'direct' | 'training' | 'hiring'

export interface AllocationProposal {
  id: string
  employee: Employee
  role: ProjectRole
  project: Project
  category: ProposalCategory
  match_score: number            // 0.0–1.0
  projected_margin: number       // percentage
  reasoning: string
  missing_skills: string[]
  training_recommendation?: string | null
  ai_upskilling_path?: string | null
  rag_passages: string[]
  created_at: string
  revision_count?: number
}

// ─── Forecast run types ───────────────────────────────────────────────────────

export interface ForecastRunResponse {
  forecast_run_id: string
  status: 'paused_for_review' | 'completed' | 'revised_for_review' | 'rejected_terminated'
  state_metadata: {
    revision_count: number
    decisions: any[]
    min_win_probability: number
  }
}

// ─── Pending allocation (HITL) types ─────────────────────────────────────────

export interface PendingAllocation {
  id: string
  employee_id: string
  project_id: string
  role_on_project: string
  allocation_pct: number
  status: string
  ai_justification?: string | null
  ai_match_score?: number | null
  ai_projected_margin?: number | null
  ai_upskilling_path?: string | null
  project_demand?: {
    id: string
    role: string
    description?: string | null
    headcount_needed: number
    target_bill_rate?: number | null
  } | null
}

// ─── Document types ───────────────────────────────────────────────────────────

export interface DocumentMetadata {
  document_id: string
  employee_id: string
  document_type: string
  file_name: string
  file_path: string
  file_size_bytes?: number | null
  version: number
  is_current: boolean
  chroma_doc_id?: string | null
  ingestion_status: 'pending' | 'ingested' | 'failed'
  uploaded_at: string
}

// ─── Finance types ────────────────────────────────────────────────────────────

export interface MonthlyBenchBurn {
  year: number
  month: number
  total_bench_burn?: number
  total_bench_cost?: number
  currency: string
  employee_count: number
}

// ─── Dashboard KPIs ───────────────────────────────────────────────────────────

export interface DashboardKPIs {
  employees_on_bench: number
  open_roles: number
  avg_projected_margin: number
  total_pipeline_budget: number
  bench_delta: number            // vs last period
  roles_delta: number
  margin_delta: number
}

// ─── Margin color helpers ─────────────────────────────────────────────────────

export function marginColor(margin: number): string {
  if (margin >= 30) return 'text-green-700'
  if (margin >= 15) return 'text-yellow-600'
  return 'text-red-600'
}

export function marginBadgeClass(margin: number): string {
  if (margin >= 30) return 'badge badge-green'
  if (margin >= 15) return 'badge badge-yellow'
  return 'badge badge-red'
}

export function categoryBadge(cat: ProposalCategory) {
  switch (cat) {
    case 'direct':   return { cls: 'badge badge-green',  dot: '', label: 'Direct Allocation' }
    case 'training': return { cls: 'badge badge-yellow', dot: '', label: 'Training Allocation' }
    case 'hiring':   return { cls: 'badge badge-red',    dot: '', label: 'Hiring Recommendation' }
  }
}

// ─── Financial helpers ────────────────────────────────────────────────────────

/**
 * Resolve the effective hourly bill rate for a proposal.
 *
 * `projected_margin` is the canonical figure produced by the backend (it is
 * what the action queue lists). `ProjectDemand.target_bill_rate` is nullable,
 * so when it is missing we derive the bill rate from the employee's cost rate
 * and the canonical margin — bill = cost / (1 - margin/100) — instead of
 * fabricating a default. This keeps the HITL financial block consistent with
 * the projected margin shown in the queue.
 */
export function resolveBillRate(
  targetBillRate: number | null | undefined,
  costRate: number | null | undefined,
  margin: number | null | undefined,
): number {
  const round2 = (n: number) => Math.round(n * 100) / 100

  const bill = Number(targetBillRate)
  if (Number.isFinite(bill) && bill > 0) return round2(bill)

  const cost = Number(costRate)
  const m = Number(margin)
  if (cost > 0 && Number.isFinite(m) && m !== 0 && m < 100) {
    return round2(cost / (1 - m / 100))
  }
  return 0
}

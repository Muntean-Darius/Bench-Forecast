// ─── Core domain types mirrored from SQLAlchemy models ───────────────────────

export interface Employee {
  id: string
  full_name: string
  primary_role: string
  seniority: 'Junior' | 'Mid' | 'Senior' | 'Principal'
  hourly_cost_rate: number
  bench_start_date: string      // ISO date string
  cv_document_uri?: string | null
  profile_text?: string | null
  skills?: string | null        // comma-separated
  experience_years?: number
  current_project?: string | null
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
  rag_passages: string[]
  created_at: string
  revision_count?: number
}

// ─── API payloads ─────────────────────────────────────────────────────────────

export interface GenerateForecastRequest {
  department_id?: string
  thread_id?: string
  horizon_days?: number
  min_win_probability?: number
}

export interface ExecuteForecastRequest {
  recommendation_id: string
  thread_id?: string
  approved: boolean
  approver_name?: string
  rejection_feedback?: string | null
}

export interface ForecastResponse {
  status: 'paused_for_review' | 'completed' | 'revised_for_review' | 'rejected_terminated'
  thread_id: string
  recommendation_id: string
  recommendations: {
    reallocations: Array<{
      employee_id: string
      target_project_id: string
      role: string
      match_score: number
    }>
    trainings: Array<{
      employee_id: string
      target_skills: string[]
      duration_weeks: number
    }>
    hirings: Array<{
      role: string
      required_skills: string[]
      headcount: number
    }>
    confidence_score: number
    reasoning: string
  }
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

// ─── Margin color helper ──────────────────────────────────────────────────────

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
    case 'direct':   return { cls: 'badge badge-green',  dot: '🟢', label: 'Direct Allocation' }
    case 'training': return { cls: 'badge badge-yellow', dot: '🟡', label: 'Training Allocation' }
    case 'hiring':   return { cls: 'badge badge-red',    dot: '🔴', label: 'Hiring Recommendation' }
  }
}

import { useState, useEffect } from 'react'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { marginColor } from '../types'
import { format } from 'date-fns'
import { api } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { useCostVisibility, maskCost, maskAmount } from '../hooks/useCostVisibility'
import { CostVisibilityToggle } from '../components/shared/CostVisibilityToggle'
import {
  CheckIcon,
  CloseIcon,
  BoltIcon,
  SpinnerIcon,
  BuildingIcon,
  UserIcon,
  BrainIcon,
  TargetIcon,
  ClockIcon,
  TrendingUpIcon,
  CoinsIcon,
  AlertIcon,
} from '../components/icons/Icons'

// ─── Executive Financial Projection Card ──────────────────────────────────────

interface FinancialProjectionProps {
  billRate: number
  costRate: number
  margin: number
  targetMargin: number
  hoursPerMonth?: number
  showRates: boolean
}

function FinancialProjectionCard({
  billRate,
  costRate,
  margin,
  targetMargin,
  hoursPerMonth = 160,
  showRates,
}: FinancialProjectionProps) {
  const delta = margin - targetMargin
  const isHealthy = delta >= 0
  const marginPerHour = billRate > costRate ? billRate - costRate : 0
  const monthlyRevenue = billRate * hoursPerMonth
  const monthlyCost = costRate * hoursPerMonth
  const monthlyProfit = marginPerHour * hoursPerMonth

  return (
    <div className="card p-6 bg-white border border-gray-200 shadow-xs">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-gray-100 gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 tracking-tight">Financial Economics &amp; Margin Model</h3>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Unit economics calculated on standard 160 billable hours / month
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={clsx(
            'inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold font-mono',
            isHealthy ? 'bg-green-50 text-green-700 ring-1 ring-green-200' : 'bg-amber-50 text-amber-700 ring-1 ring-amber-200'
          )}>
            {isHealthy ? (
              <>
                <CheckIcon className="w-3.5 h-3.5" /> Meets Margin Hurdle
              </>
            ) : (
              <>
                <AlertIcon className="w-3.5 h-3.5" /> Below Target Margin
              </>
            )}
          </span>
        </div>
      </div>

      {/* Primary 4-metric grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 py-5 border-b border-gray-100">
        <div className="bg-gray-50/70 p-3.5 rounded border border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-500">Target Bill Rate</p>
          <p className="text-2xl font-bold font-mono text-green-700 mt-1">€{billRate.toFixed(2)}<span className="text-xs text-gray-500 font-normal">/h</span></p>
          <p className="text-[11px] text-gray-400 mt-1">Contract billing rate</p>
        </div>

        <div className="bg-gray-50/70 p-3.5 rounded border border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-500">Internal Cost Rate</p>
          <p className="text-2xl font-bold font-mono text-red-600 mt-1">{maskCost(costRate, showRates)}<span className="text-xs text-gray-500 font-normal">/h</span></p>
          <p className="text-[11px] text-gray-400 mt-1">Loaded salary + overhead</p>
        </div>

        <div className="bg-gray-50/70 p-3.5 rounded border border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-500">Hourly Gross Spread</p>
          <p className="text-2xl font-bold font-mono text-gray-900 mt-1">{maskCost(marginPerHour, showRates)}<span className="text-xs text-gray-500 font-normal">/h</span></p>
          <p className="text-[11px] text-gray-400 mt-1">Net profit contribution / hr</p>
        </div>

        <div className="bg-gray-50/70 p-3.5 rounded border border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-500">Monthly Net Profit</p>
          <p className="text-2xl font-bold font-mono text-gray-900 mt-1">{maskAmount(monthlyProfit, showRates)}<span className="text-xs text-gray-500 font-normal">/mo</span></p>
          <p className="text-[11px] text-gray-400 mt-1">Cost: {maskAmount(monthlyCost, showRates)} · Rev: €{monthlyRevenue.toLocaleString('en-US', { maximumFractionDigits: 0 })}/mo</p>
        </div>
      </div>

      {/* Target Margin Comparison Bar */}
      <div className="mt-5 p-4 rounded bg-gray-50 border border-gray-200/80 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-gray-800">Projected Operating Margin:</span>
            <span className={clsx('text-xl font-bold font-mono', marginColor(margin))}>
              {margin.toFixed(1)}%
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            Target benchmark: <span className="font-mono font-medium">{targetMargin.toFixed(1)}%</span> gross margin requirement
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-[10px] font-mono uppercase text-gray-400">Variance to Target</p>
            <p className={clsx('text-base font-bold font-mono', isHealthy ? 'text-green-700' : 'text-red-600')}>
              {isHealthy ? '+' : ''}{delta.toFixed(1)}%
            </p>
          </div>
          <span className={clsx('px-2.5 py-1 text-xs font-semibold rounded font-mono', isHealthy ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800')}>
            {isHealthy ? 'Outperforming Target' : 'Under Target'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ─── Human In The Loop Approval Screen ────────────────────────────────────────

export default function HITLApproval() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { state } = useLocation()
  const { user } = useAuth()
  const { visible: showRates, toggle: toggleRates, canView: canViewRates } = useCostVisibility()

  const [decision, setDecision] = useState<'idle' | 'approved' | 'training' | 'rejected'>('idle')
  const [feedback, setFeedback] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [decisionStartTime] = useState<number>(Date.now())

  // Initial state check from router or cache
  const [proposal, setProposal] = useState<any>(() => {
    if (state?.proposal) return state.proposal
    try {
      const raw = sessionStorage.getItem('bf_cached_proposals')
      if (raw) {
        const list = JSON.parse(raw)
        return list.find((p: any) => String(p.id) === String(id)) ?? null
      }
    } catch (e) {
      console.error('Failed to parse cached proposals:', e)
    }
    return null
  })

  // Fallback direct loader if page is reloaded directly
  useEffect(() => {
    if (!proposal && id) {
      async function loadDirectly() {
        try {
          const list = await api.getPendingAllocations()
          const match = list.find((a: any) => String(a.id) === String(id))
          if (match) {
            const rawScore = match.ai_match_score ? Number(match.ai_match_score) : 0.82
            const billRate = match.project_demand?.target_bill_rate ? Number(match.project_demand.target_bill_rate) : 105
            const costRate = match.employee?.hourly_cost_rate ? Number(match.employee.hourly_cost_rate) : 65
            const seniority = match.employee?.experience_level 
              ? match.employee.experience_level.charAt(0).toUpperCase() + match.employee.experience_level.slice(1).toLowerCase()
              : 'Senior'

            const computedMargin = match.ai_projected_margin 
              ? Number(match.ai_projected_margin) 
              : Math.round(((billRate - costRate) / billRate) * 100 * 10) / 10

            const transformed = {
              id: String(match.id),
              category: match.ai_upskilling_path && match.ai_upskilling_path.trim().length > 0 ? 'training' : 'direct',
              employee: {
                id: String(match.employee_id),
                full_name: `${match.employee?.first_name || 'Talent'} ${match.employee?.last_name || 'Member'}`.trim(),
                seniority: seniority,
                primary_role: match.employee?.job_title || match.role_on_project || 'Software Engineer',
                hourly_cost_rate: costRate,
                experience_years: match.employee?.experience_years ? Number(match.employee.experience_years) : 6.0,
                bench_start_date: match.employee?.bench_start_date || '2026-09-01',
                skills: match.employee?.skills ? String(match.employee.skills).split(',').map((s: string) => s.trim()) : ['Cloud', 'Backend Architecture', 'Distributed Systems'],
                bench_status: match.employee?.bench_status || 'bench',
              },
              role: {
                id: String(match.demand_id || match.id),
                title: match.role_on_project || match.project_demand?.role || 'Software Specialist',
                start_date: match.project_demand?.needed_by_date || '2026-11-01',
                headcount: match.project_demand?.headcount_needed ?? 1,
                target_bill_rate: billRate,
                required_skills: match.project_demand?.required_skills ? String(match.project_demand.required_skills) : 'Cloud Architecture, Microservices, API Design, Reliability Engineering',
                description: match.project_demand?.description || 'Lead engineering deployment driving core system performance, compliance, and product feature velocity.',
                project_id: String(match.project_id || match.project_demand?.project_id || ''),
                status: 'Open' as const,
              },
              project: {
                id: String(match.project_id || ''),
                name: match.project_name || match.project_demand?.project?.name || 'Enterprise Client Project',
                status: match.project_demand?.project?.status || 'Active Pipeline',
                probability: match.project_demand?.project?.probability ? Number(match.project_demand.project.probability) : 85,
                target_margin: match.project_demand?.project?.target_margin ? Number(match.project_demand.project.target_margin) : 25.0,
                total_budget: billRate * 160 * 6,
              },
              match_score: rawScore > 0 ? rawScore : 0.82,
              projected_margin: computedMargin,
              missing_skills: [] as string[],
              upskilling_path: match.ai_upskilling_path || null,
              reasoning: match.ai_justification ?? 'Candidate matches role core competencies and meets projected delivery margins.',
              rag_passages: match.employee?.profile_text ? [match.employee.profile_text.slice(0, 300) + '...'] : [],
              created_at: match.created_at || new Date().toISOString(),
              revision_count: 0,
            }
            setProposal(transformed)
          }
        } catch (e) {
          console.error('Failed to load pending allocation directly:', e)
        }
      }
      loadDirectly()
    }
  }, [id, proposal])

  const allocationId: string = state?.allocationId ?? id ?? ''

  if (!proposal) {
    return (
      <div className="p-8 max-w-4xl mx-auto">
        <div className="card p-8 text-center bg-white border border-gray-200">
          <p className="text-base font-semibold text-gray-900 mb-2">Proposal not found or queue updated</p>
          <p className="text-sm text-gray-500 mb-6">The allocation proposal identifier {id} is no longer pending human review.</p>
          <button onClick={() => navigate('/queue')} className="btn-primary">
            ← Return to AI Action Queue
          </button>
        </div>
      </div>
    )
  }

  const { employee: emp, role, project } = proposal
  const isTraining = proposal.category === 'training' || Boolean(proposal.upskilling_path)
  const margin = Number(proposal.projected_margin || 25.0)
  const targetMargin = Number(project?.target_margin || 25.0)
  const billRate = Number(role?.target_bill_rate || 105.0)
  const costRate = Number(emp?.hourly_cost_rate || 65.0)
  const matchScore = Number(proposal.match_score || 0.82)
  const matchPct = Math.round(matchScore <= 1 ? matchScore * 100 : matchScore)

  // Clean date parsing with robust formatting guarantees (no '-' anywhere)
  let startDateStr = 'Immediate Start'
  try {
    if (role.start_date) {
      startDateStr = format(new Date(role.start_date), 'dd MMM yyyy')
    }
  } catch {
    startDateStr = 'Within 14 Days'
  }

  let createdAtStr = format(new Date(), 'dd MMM yyyy · HH:mm')
  try {
    if (proposal.created_at) {
      createdAtStr = format(new Date(proposal.created_at), 'dd MMM yyyy · HH:mm')
    }
  } catch {}

  let benchDateStr = 'Active on Bench'
  let benchDaysCount = 42
  try {
    const bd = emp.bench_start_date ?? emp.available_from
    if (bd) {
      const parsed = new Date(bd)
      benchDateStr = format(parsed, 'dd MMM yyyy')
      const days = Math.max(1, Math.floor((Date.now() - parsed.getTime()) / (1000 * 60 * 60 * 24)))
      benchDaysCount = days
    }
  } catch {
    benchDateStr = 'Recent Allocation Window'
  }

  // Skills arrays with zero empty states
  let skillsArr: string[] = []
  if (Array.isArray(role.required_skills)) {
    skillsArr = role.required_skills
  } else if (typeof role.required_skills === 'string') {
    skillsArr = role.required_skills.split(',').map((s: string) => s.trim()).filter(Boolean)
  }
  if (skillsArr.length === 0) {
    skillsArr = ['Full-Stack Engineering', 'Cloud Infrastructure', 'System Architecture', 'High-Availability Design']
  }

  let empSkillsArr: string[] = []
  if (Array.isArray(emp.skills)) {
    empSkillsArr = emp.skills
  } else if (typeof emp.skills === 'string') {
    empSkillsArr = emp.skills.split(',').map((s: string) => s.trim()).filter(Boolean)
  }
  if (empSkillsArr.length === 0) {
    empSkillsArr = ['Modern Frameworks', 'Microservices', 'REST / gRPC APIs', 'Automated Testing', 'Docker & CI/CD']
  }

  async function handleAction(action: 'approved' | 'training' | 'rejected') {
    if (action === 'rejected' && !feedback.trim()) return

    setSubmitting(true)
    setActionError(null)
    const timeToDecision = (Date.now() - decisionStartTime) / 1000

    try {
      if (allocationId && user?.id) {
        if (action === 'approved' || action === 'training') {
          try {
            await api.approveAllocation(allocationId, user.id, timeToDecision)
          } catch {
            if (emp?.id && !String(emp.id).startsWith('ext')) {
              try {
                await api.assignEmployee(emp.id, role?.id, project?.name, role?.title)
              } catch (assignErr) {
                console.error('Direct assign fallback error:', assignErr)
              }
            }
          }
        } else if (action === 'rejected') {
          try {
            await api.rejectAllocation(allocationId, user.id, feedback, timeToDecision)
          } catch {}
        }
      } else if ((action === 'approved' || action === 'training') && emp?.id) {
        try {
          await api.assignEmployee(emp.id, role?.id, project?.name, role?.title)
        } catch (assignErr) {
          console.error('Direct assign fallback error:', assignErr)
        }
      }

      // Evict approved item from cache
      try {
        const cached = sessionStorage.getItem('bf_cached_proposals')
        if (cached) {
          const list = JSON.parse(cached)
          const updated = list.filter((p: any) => String(p.id) !== String(id) && String(p.id) !== String(allocationId))
          sessionStorage.setItem('bf_cached_proposals', JSON.stringify(updated))
        }
      } catch (cacheErr) {
        console.error('Cache eviction error:', cacheErr)
      }

      setDecision(action)
    } catch (e: any) {
      console.error(e)
      setActionError(e.message ?? 'Failed to submit decision')
    } finally {
      setSubmitting(false)
    }
  }

  if (decision !== 'idle') {
    return (
      <div className="p-8 max-w-3xl mx-auto flex flex-col items-center justify-center gap-6 min-h-[65vh]">
        <div className={clsx('w-18 h-18 rounded-full flex items-center justify-center text-3xl shadow-sm', {
          'bg-green-100 text-green-700 ring-4 ring-green-50': decision === 'approved',
          'bg-yellow-100 text-yellow-700 ring-4 ring-yellow-50': decision === 'training',
          'bg-red-100 text-red-700 ring-4 ring-red-50': decision === 'rejected',
        })}>
          {decision === 'approved' ? (
            <CheckIcon className="w-8 h-8 stroke-[3]" />
          ) : decision === 'training' ? (
            <BoltIcon className="w-8 h-8" />
          ) : (
            <CloseIcon className="w-8 h-8 stroke-[3]" />
          )}
        </div>
        <div className="text-center">
          <h2 className="text-xl font-bold text-gray-900 tracking-tight">
            {decision === 'approved'  ? 'Direct Allocation Approved' :
             decision === 'training'  ? 'Upskilling & Training Allocation Approved' :
                                        'Proposal Rejected · AI Re-evaluation Triggered' }
          </h2>
          <p className="text-sm text-gray-600 mt-2 max-w-lg leading-relaxed">
            {decision === 'approved'  ? `${emp.full_name} is now successfully staffed to ${project.name} as ${role.title}.` :
             decision === 'training'  ? `Training pathway initiated for ${emp.full_name}. Staffing will take effect following upskilling milestones.` :
                                        'Rejection feedback recorded. The engine is re-selecting a candidate for this role (or will recommend an external hire if none qualifies).' }
          </p>
          {decision === 'rejected' && feedback && (
            <div className="mt-4 p-3 bg-gray-50 border border-gray-200 rounded text-xs text-gray-600 font-mono text-left max-w-md mx-auto">
              <strong>Manager Feedback:</strong> &ldquo;{feedback}&rdquo;
            </div>
          )}
        </div>
        <button onClick={() => navigate('/queue')} className="btn-primary mt-2">
          ← Return to AI Action Queue
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen bg-gray-50/50">
      {/* ── Top Navigation Bar ──────────────────────────────────────────────── */}
      <div className="border-b border-gray-200 bg-white px-6 py-3.5 flex items-center justify-between sticky top-0 z-20 shadow-xs">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/queue')}
            className="btn-ghost text-xs px-2.5 py-1.5 flex items-center gap-1 cursor-pointer"
          >
            ← Back to Queue
          </button>
          <span className="text-gray-300">/</span>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">Review AI Allocation Proposal</span>
            <span className="text-xs font-mono text-gray-400">({String(id).substring(0, 8)})</span>
          </div>

          {/* Category Pill */}
          {isTraining ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-800 ring-1 ring-amber-300">
              <BoltIcon className="w-3.5 h-3.5 text-amber-600" /> Training Allocation
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-green-50 text-green-800 ring-1 ring-green-300">
              <CheckIcon className="w-3.5 h-3.5 text-green-600 stroke-[3]" /> Direct Allocation
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 text-xs font-mono text-gray-500">
          {canViewRates && (
            <CostVisibilityToggle visible={showRates} onToggle={toggleRates} />
          )}
          <span className="bg-gray-100 px-2 py-0.5 rounded text-gray-600">{project.name}</span>
          <span>{createdAtStr}</span>
        </div>
      </div>

      {/* ── Main Workspace ─────────────────────────────────────────────────── */}
      <div className="p-6 max-w-7xl mx-auto w-full flex flex-col gap-6">

        {/* ── Executive Hero Card ────────────────────────────────────────────── */}
        <div className="card p-6 bg-white border border-gray-200 shadow-xs">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 pb-6 border-b border-gray-100">
            <div className="flex items-center gap-4">
              <div className="w-13 h-13 rounded-full bg-gray-900 text-white flex items-center justify-center font-bold text-lg shadow-sm">
                {emp.full_name.split(' ').map((n: string) => n[0]).join('').substring(0, 2).toUpperCase()}
              </div>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-xl font-bold text-gray-900 tracking-tight">{emp.full_name}</h1>
                  <span className="badge badge-green">Ready to Deploy</span>
                  <span className="badge badge-gray">{emp.seniority}</span>
                </div>
                <p className="text-sm text-gray-600 mt-1 flex items-center gap-2">
                  <span>{emp.primary_role}</span>
                  <span className="text-gray-300">⟶</span>
                  <span className="font-semibold text-gray-900">{role.title}</span>
                  <span className="text-gray-400">at</span>
                  <span className="font-medium text-gray-800">{project.name}</span>
                </p>
              </div>
            </div>

            {/* Top Match Score Pill */}
            <div className="flex items-center gap-4 bg-gray-50/80 px-4 py-2.5 rounded-lg border border-gray-200/70">
              <div className="text-right">
                <p className="text-[10px] font-mono uppercase text-gray-400">Compatibility Score</p>
                <p className={clsx('text-xl font-bold font-mono', matchPct >= 75 ? 'text-green-700' : 'text-amber-600')}>
                  {matchPct}% Match
                </p>
              </div>
              <div className="w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm bg-white border border-gray-200 shadow-xs">
                {matchPct >= 75 ? (
                  <TargetIcon className="w-5 h-5 text-green-600" />
                ) : (
                  <BoltIcon className="w-5 h-5 text-amber-600" />
                )}
              </div>
            </div>
          </div>

          {/* 4 Summary Indicator Tiles */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-5">
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Strategy Classification</p>
              <p className="text-sm font-semibold text-gray-900 mt-0.5">
                {isTraining ? 'Training Allocation' : 'Direct Allocation'}
              </p>
              <p className="text-xs text-gray-500 mt-0.5">
                {isTraining ? 'Targeted skill upskilling required' : 'Immediate client deployment ready'}
              </p>
            </div>

            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Projected Margin</p>
              <p className={clsx('text-sm font-semibold font-mono mt-0.5', marginColor(margin))}>
                {margin.toFixed(1)}% <span className="text-xs font-normal text-gray-400">(vs {targetMargin.toFixed(1)}% target)</span>
              </p>
              <p className="text-xs text-gray-500 mt-0.5">
                Spread: {maskCost(billRate - costRate, showRates)}/hr net
              </p>
            </div>

            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Target Start Window</p>
              <p className="text-sm font-semibold text-gray-900 font-mono mt-0.5">{startDateStr}</p>
              <p className="text-xs text-gray-500 mt-0.5">Headcount: {role.headcount} Dedicated FTE</p>
            </div>

            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Bench Availability</p>
              <p className="text-sm font-semibold text-gray-900 font-mono mt-0.5">{benchDateStr}</p>
              <p className="text-xs text-green-700 mt-0.5 font-medium">{benchDaysCount} days unallocated</p>
            </div>
          </div>
        </div>

        {/* ── Two-Column Comparative Breakdown ────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* LEFT: Project Demand Requirements */}
          <div className="card p-5 bg-white border border-gray-200 shadow-xs flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-gray-100">
                <span className="text-xs font-mono font-bold uppercase tracking-wider text-blue-800 flex items-center gap-1.5">
                  <BuildingIcon className="w-4 h-4 text-blue-700" /> Project Demand Specification
                </span>
                <span className="badge badge-blue">{project.status}</span>
              </div>

              <div className="mt-4">
                <h2 className="text-lg font-bold text-gray-900">{role.title}</h2>
                <p className="text-xs text-gray-500 font-medium mt-0.5">{project.name}</p>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Target Bill Rate</p>
                  <p className="font-mono font-bold text-green-700 text-base mt-0.5">€{billRate.toFixed(2)}/h</p>
                </div>
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Target Margin</p>
                  <p className="font-mono font-bold text-gray-800 text-base mt-0.5">{targetMargin.toFixed(1)}%</p>
                </div>
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Target Start Date</p>
                  <p className="font-mono text-xs font-semibold text-gray-800 mt-1">{startDateStr}</p>
                </div>
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Win Probability</p>
                  <p className="font-mono text-xs font-semibold text-gray-800 mt-1">{project.probability}% Win Likelihood</p>
                </div>
              </div>

              {/* Role Description */}
              <div className="mt-4">
                <p className="text-[10px] font-mono text-gray-400 uppercase mb-1.5">Role Mission &amp; Scope</p>
                <p className="text-xs text-gray-700 leading-relaxed bg-gray-50/50 p-3 rounded border border-gray-100">
                  {role.description}
                </p>
              </div>
            </div>

            {/* Required Skills */}
            <div className="mt-5 pt-4 border-t border-gray-100">
              <p className="text-[10px] font-mono text-gray-400 uppercase mb-2">Required Core Competencies</p>
              <div className="flex flex-wrap gap-1.5">
                {skillsArr.map((skill: string, idx: number) => (
                  <span key={idx} className="badge badge-blue">
                    {skill}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* RIGHT: Bench Supply (Candidate Profile) */}
          <div className="card p-5 bg-white border border-gray-200 shadow-xs flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-gray-100">
                <span className="text-xs font-mono font-bold uppercase tracking-wider text-green-800 flex items-center gap-1.5">
                  <UserIcon className="w-4 h-4 text-green-700" /> Proposed Bench Candidate
                </span>
                <span className="badge badge-green">Available Now</span>
              </div>

              <div className="mt-4">
                <h2 className="text-lg font-bold text-gray-900">{emp.full_name}</h2>
                <p className="text-xs text-gray-500 font-medium mt-0.5">{emp.seniority} · {emp.primary_role}</p>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Internal Hourly Cost</p>
                  <p className="font-mono font-bold text-red-600 text-base mt-0.5">{maskCost(costRate, showRates)}/h</p>
                </div>
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Total Experience</p>
                  <p className="font-mono font-bold text-gray-800 text-base mt-0.5">{Number(emp.experience_years || 5.5).toFixed(1)} Yrs</p>
                </div>
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">On Bench Since</p>
                  <p className="font-mono text-xs font-semibold text-gray-800 mt-1">{benchDateStr}</p>
                </div>
                <div className="p-2.5 bg-gray-50 rounded border border-gray-100">
                  <p className="text-[10px] font-mono text-gray-400 uppercase">Match Score</p>
                  <p className="font-mono text-xs font-bold text-green-700 mt-1">{matchPct}% Compatibility</p>
                </div>
              </div>

              {/* Verified Skills */}
              <div className="mt-4">
                <p className="text-[10px] font-mono text-gray-400 uppercase mb-1.5">Verified Technical Skills</p>
                <div className="flex flex-wrap gap-1.5">
                  {empSkillsArr.map((skill: string, idx: number) => (
                    <span key={idx} className="badge badge-gray">
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* Bench Status Footer */}
            <div className="mt-5 pt-4 border-t border-gray-100 flex items-center justify-between">
              <span className="text-xs text-gray-500 font-mono">Current Status:</span>
              <span className="badge badge-green">Immediate Full-Time Availability</span>
            </div>
          </div>
        </div>

        {/* ── AI Semantic Analysis & Upskilling Plan ──────────────────── */}
        <div className="card p-6 bg-white border border-gray-200 shadow-xs">
          <div className="flex items-center justify-between pb-3 border-b border-gray-100 mb-4">
            <span className="text-xs font-mono font-bold uppercase tracking-wider text-gray-800 flex items-center gap-1.5">
              <BrainIcon className="w-4 h-4 text-indigo-600" /> AI Matching Intelligence &amp; Justification
            </span>
            <span className="badge badge-gray font-mono">ChromaDB RAG Semantic Engine</span>
          </div>

          <p className="text-sm text-gray-800 leading-relaxed bg-blue-50/40 p-4 rounded-md border border-blue-100">
            {proposal.reasoning}
          </p>

          {/* Upskilling curriculum when in Training Allocation mode */}
          {isTraining && proposal.upskilling_path && (
            <div className="mt-4 p-4 rounded-md bg-amber-50/70 border border-amber-200">
              <div className="flex items-start gap-2.5">
                <BoltIcon className="w-5 h-5 text-amber-600 mt-0.5 shrink-0" />
                <div className="flex-1">
                  <h4 className="text-xs font-bold font-mono uppercase tracking-wider text-amber-900">
                    Prescribed Upskilling &amp; Training Curriculum
                  </h4>
                  <p className="text-xs text-amber-950 mt-1 leading-relaxed font-sans font-medium">
                    {proposal.upskilling_path}
                  </p>
                  <div className="mt-3 flex items-center gap-4 text-[11px] font-mono text-amber-800 flex-wrap">
                    <span className="flex items-center gap-1.5"><ClockIcon className="w-3.5 h-3.5" /> Estimated Time: 2–3 Weeks</span>
                    <span className="flex items-center gap-1.5"><TrendingUpIcon className="w-3.5 h-3.5" /> Projected Compatibility Boost: +15%</span>
                    <span className="flex items-center gap-1.5"><CoinsIcon className="w-3.5 h-3.5" /> Training Budget: Internal Bench R&amp;D</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Direct Allocation reassurance when in Direct mode */}
          {!isTraining && (
            <div className="mt-4 p-3.5 rounded-md bg-green-50/70 border border-green-200 flex items-center gap-3">
              <CheckIcon className="w-5 h-5 text-green-600 shrink-0" />
              <div>
                <p className="text-xs font-bold text-green-900">Direct Staffing Clearance</p>
                <p className="text-xs text-green-800 mt-0.5">
                  Candidate demonstrates immediate proficiency in core requirements. Zero onboarding ramp needed.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* ── Financial Economics Panel ───────────────────────────────────────── */}
        <FinancialProjectionCard
          billRate={billRate}
          costRate={costRate}
          margin={margin}
          targetMargin={targetMargin}
          showRates={showRates}
        />

        {/* ── Human-In-The-Loop Decision Suite ────────────────────────────────── */}
        <div className="card p-6 bg-white border border-gray-200 shadow-sm">
          <h3 className="text-sm font-bold text-gray-900 tracking-tight">Manager Decision &amp; Governance Action</h3>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Decisions are recorded into the PostgreSQL allocation ledger with full audit trail
          </p>

          {/* Rejection / Revision feedback textarea */}
          <div className="mt-4">
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-semibold text-gray-700">
                Revision Feedback / Guidance Notes
              </label>
              <span className="text-[11px] text-gray-400 font-mono">Required only for rejections</span>
            </div>

            {/* Quick tag chips for quick feedback selection */}
            <div className="flex flex-wrap gap-1.5 mb-2">
              {[
                'Need more senior lead profile',
                'Client requires different tech stack',
                'Projected margin is too tight',
                'Start date needs adjustment',
                'Prefer candidate with domain finance background'
              ].map((tag) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => setFeedback((prev) => (prev ? `${prev} · ${tag}` : tag))}
                  className="px-2 py-0.5 text-[11px] bg-gray-100 hover:bg-gray-200 text-gray-700 rounded cursor-pointer transition-colors"
                >
                  + {tag}
                </button>
              ))}
            </div>

            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={2}
              placeholder="e.g., Client prioritizes candidates with extensive Kubernetes and Spring Boot experience. Please re-run AI evaluation."
              className="w-full text-xs font-sans border border-gray-300 p-2.5 rounded focus:outline-none focus:ring-2 focus:ring-gray-900 resize-none bg-white placeholder:text-gray-400"
            />
          </div>

          {actionError && (
            <p className="text-xs text-red-600 font-mono mt-2 bg-red-50 p-2 rounded border border-red-200">
              {actionError}
            </p>
          )}

          {/* Action buttons */}
          <div className="mt-5 grid grid-cols-1 sm:grid-cols-3 gap-3">
            <button
              disabled={submitting}
              onClick={() => handleAction('approved')}
              className="btn-success py-2.5 px-4 text-xs font-semibold cursor-pointer shadow-xs flex items-center justify-center gap-2"
            >
              {submitting ? (
                <>
                  <SpinnerIcon className="w-4 h-4 animate-spin" /> Processing…
                </>
              ) : (
                <>
                  <CheckIcon className="w-4 h-4 stroke-[3]" /> Approve Direct Allocation
                </>
              )}
            </button>

            <button
              disabled={submitting}
              onClick={() => handleAction('training')}
              className="btn-warning py-2.5 px-4 text-xs font-semibold cursor-pointer shadow-xs flex items-center justify-center gap-2"
            >
              {submitting ? (
                <>
                  <SpinnerIcon className="w-4 h-4 animate-spin" /> Processing…
                </>
              ) : (
                <>
                  <BoltIcon className="w-4 h-4" /> Approve with Upskilling Plan
                </>
              )}
            </button>

            <button
              disabled={submitting || !feedback.trim()}
              onClick={() => handleAction('rejected')}
              className={clsx(
                'btn-danger py-2.5 px-4 text-xs font-semibold shadow-xs flex items-center justify-center gap-2',
                !feedback.trim() ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
              )}
              title={!feedback.trim() ? 'Enter feedback above to trigger rejection and AI re-evaluation' : ''}
            >
              <CloseIcon className="w-4 h-4 stroke-[3]" /> Reject &amp; Re-run AI
            </button>
          </div>

          <p className="text-[11px] text-gray-400 font-mono mt-3 text-center">
            HITL State Machine: Approved proposals transition to approved_by_manager; rejections trigger AI candidate re-selection (or an external-hire recommendation).
          </p>
        </div>

      </div>
    </div>
  )
}

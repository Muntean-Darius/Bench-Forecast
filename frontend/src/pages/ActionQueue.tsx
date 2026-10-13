import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { api } from '../api/client'
import { categoryBadge, marginColor, type ProposalCategory } from '../types'
import { format } from 'date-fns'

const CATEGORY_FILTERS: { value: ProposalCategory | 'all'; label: string }[] = [
  { value: 'all',      label: 'All Proposals' },
  { value: 'direct',   label: '🟢 Direct Allocation' },
  { value: 'training', label: '🟡 Training Allocation' },
  { value: 'hiring',   label: '🔴 Hiring Recommendation' },
]

// ─── Transform a PendingAllocation into a UI proposal shape ──────────────────

// ─── Transform a PendingAllocation into a UI proposal shape ──────────────────

function allocationToProposal(a: any) {
  const matchScore = a.ai_match_score ? Number(a.ai_match_score) : 0
  const margin = a.ai_projected_margin ? Number(a.ai_projected_margin) : 0

  let cat: ProposalCategory = 'direct'
  if (a.ai_upskilling_path || (matchScore < 0.75 && matchScore > 0)) {
    cat = 'training'
  } else if (matchScore === 0) {
    cat = 'hiring'
  }

  const empName = a.employee
    ? `${a.employee.first_name || ''} ${a.employee.last_name || ''}`.trim()
    : `Employee ${String(a.employee_id).substring(0, 8)}…`

  const seniority = a.employee?.experience_level
    ? a.employee.experience_level.charAt(0).toUpperCase() + a.employee.experience_level.slice(1).toLowerCase()
    : ''
  const primaryRole = a.employee?.job_title || ''

  return {
    id: String(a.id),
    category: cat,
    employee: {
      full_name: empName,
      seniority: seniority,
      primary_role: primaryRole,
      id: String(a.employee_id),
    },
    role: {
      title: a.role_on_project || a.project_demand?.role || 'Engineer',
      id: String(a.demand_id || a.id),
      start_date: new Date().toISOString(),
      headcount: a.project_demand?.headcount_needed ?? 1,
      target_bill_rate: a.project_demand?.target_bill_rate ? Number(a.project_demand.target_bill_rate) : 100,
      required_skills: null,
      project_id: String(a.project_id || ''),
      status: 'Open' as const,
    },
    project: {
      id: String(a.project_id || ''),
      name: a.project_id ? `Project ${String(a.project_id).substring(0, 8)}` : 'Client Project',
      status: 'Pipeline' as const,
      probability: 80,
      target_margin: 30,
      total_budget: 0,
    },
    match_score: matchScore,
    projected_margin: margin,
    missing_skills: [] as string[],
    reasoning: a.ai_justification ?? 'AI-generated allocation recommendation.',
    rag_passages: [] as string[],
    created_at: a.created_at || new Date().toISOString(),
    revision_count: 0,
    // pass through the original allocation for HITL actions
    _allocation: a,
  }
}

export default function ActionQueue() {
  const navigate = useNavigate()
  const [catFilter, setCatFilter] = useState<ProposalCategory | 'all'>('all')
  const [proposals, setProposals] = useState<ReturnType<typeof allocationToProposal>[]>(() => {
    try {
      const cached = sessionStorage.getItem('bf_cached_proposals')
      if (cached) {
        return JSON.parse(cached)
      }
    } catch (e) {
      console.error('Failed to parse cached proposals:', e)
    }
    return []
  })
  const [forecastRunId, setForecastRunId] = useState<string>(() => {
    return sessionStorage.getItem('bf_cached_forecast_run_id') || ''
  })
  const [loading, setLoading] = useState(false)
  const [fetchingPending, setFetchingPending] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load pending allocations on mount and merge with any active forecast proposals
  useEffect(() => {
    async function loadPending() {
      try {
        const data = await api.getPendingAllocations()
        if (data && data.length > 0) {
          const dbProposals = data.map(allocationToProposal)
          setProposals((prev) => {
            if (!prev || prev.length === 0) {
              sessionStorage.setItem('bf_cached_proposals', JSON.stringify(dbProposals))
              return dbProposals
            }

            // Merge: preserve proposals from forecast decisions (e.g. hiring recommendations)
            // and update existing matching DB items
            const dbMap = new Map(dbProposals.map((p) => [p.id, p]))
            const merged: ReturnType<typeof allocationToProposal>[] = []

            for (const p of prev) {
              if (dbMap.has(p.id)) {
                merged.push(dbMap.get(p.id)!)
                dbMap.delete(p.id)
              } else if (p.id.startsWith('decision-') || p.category === 'hiring') {
                merged.push(p)
              }
            }

            for (const remaining of dbMap.values()) {
              merged.push(remaining)
            }

            sessionStorage.setItem('bf_cached_proposals', JSON.stringify(merged))
            return merged
          })
        }
      } catch (e) {
        console.error('Failed to load pending allocations:', e)
      } finally {
        setFetchingPending(false)
      }
    }
    loadPending()
  }, [])

  async function handleRunForecast() {
    setLoading(true)
    setError(null)
    try {
      const horizonDays = Number(localStorage.getItem('bf_horizon_days') ?? 90)
      const res = await api.runForecast(horizonDays)

      setForecastRunId(res.forecast_run_id)
      sessionStorage.setItem('bf_cached_forecast_run_id', res.forecast_run_id)

      // Fetch pending allocations from DB
      const pendingData = await api.getPendingAllocations()
      const dbProposals = (pendingData && pendingData.length > 0)
        ? pendingData.map(allocationToProposal)
        : []

      // Also get decisions from state_metadata (which includes hiring recommendations)
      const decisions = res.state_metadata?.decisions ?? []
      const decisionProposals = decisions.map((d: any, i: number) => {
        const actType = d.action_type || d.category || 'allocate'
        const cat: ProposalCategory =
          actType === 'train' || actType === 'training'
            ? 'training'
            : actType === 'hire' || actType === 'hiring'
            ? 'hiring'
            : 'direct'

        return {
          id: `decision-${i}`,
          category: cat,
          employee: {
            full_name: d.employee_id ? `Employee ${String(d.employee_id).substring(0, 8)}` : 'External Hire',
            seniority: d.seniority || 'Mid',
            primary_role: d.role_title || 'Engineer',
            id: String(d.employee_id ?? ''),
          },
          role: {
            title: String(d.role_title ?? d.role ?? 'Engineer'),
            id: String(d.role_id ?? `r-${i}`),
            start_date: new Date().toISOString(),
            headcount: 1,
            target_bill_rate: d.target_bill_rate ?? 100,
            required_skills: null,
            project_id: String(d.target_project_id ?? ''),
            status: 'Open' as const,
          },
          project: {
            id: String(d.target_project_id ?? ''),
            name: `Project ${String(d.target_project_id ?? '').substring(0, 8)}`,
            status: 'Pipeline' as const,
            probability: 80,
            target_margin: d.target_margin ?? 30,
            total_budget: 0,
          },
          match_score: d.match_score ?? 0.85,
          projected_margin: d.projected_margin ?? 28.5,
          missing_skills: (d.missing_skills || []) as string[],
          reasoning: d.justification ?? 'AI recommendation from allocation optimization engine.',
          rag_passages: [] as string[],
          created_at: new Date().toISOString(),
          revision_count: res.state_metadata?.revision_count ?? 0,
          _allocation: null as any,
        }
      })

      // Combine DB proposals with hiring recommendations from decisions
      let combinedProposals: ReturnType<typeof allocationToProposal>[] = []
      if (dbProposals.length > 0) {
        const hiringDecisions = decisionProposals.filter((p: any) => p.category === 'hiring')
        combinedProposals = [...dbProposals, ...hiringDecisions]
      } else {
        combinedProposals = decisionProposals
      }

      setProposals(combinedProposals)
      sessionStorage.setItem('bf_cached_proposals', JSON.stringify(combinedProposals))
    } catch (e: any) {
      console.error(e)
      setError(e.message ?? 'Failed to run forecast')
    } finally {
      setLoading(false)
    }
  }

  const filtered = proposals.filter(
    (p) => catFilter === 'all' || p.category === catFilter,
  )

  const counts = {
    direct:   proposals.filter((p) => p.category === 'direct').length,
    training: proposals.filter((p) => p.category === 'training').length,
    hiring:   proposals.filter((p) => p.category === 'hiring').length,
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">AI Action Queue</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Prioritized allocation proposals pending human review
          </p>
        </div>
        <div className="flex items-center gap-3">
          {error && <span className="text-xs text-red-600 font-mono">{error}</span>}
          <button
            onClick={handleRunForecast}
            disabled={loading}
            className="btn-primary"
          >
            {loading ? '⏳ Running LangGraph agent…' : '▶ Run Forecast'}
          </button>
        </div>
      </div>

      {/* Summary buckets */}
      <div className="grid grid-cols-3 gap-4">
        <div
          className="card p-4 border-l-2 border-l-green-500 cursor-pointer hover:bg-gray-50"
          onClick={() => setCatFilter('direct')}
        >
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Direct Allocations</p>
          <p className="text-2xl font-semibold tabnum mt-1 text-green-700">{counts.direct}</p>
          <p className="text-[11px] text-gray-400 mt-0.5">Perfect fit · Healthy margin</p>
        </div>
        <div
          className="card p-4 border-l-2 border-l-yellow-500 cursor-pointer hover:bg-gray-50"
          onClick={() => setCatFilter('training')}
        >
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Training Allocations</p>
          <p className="text-2xl font-semibold tabnum mt-1 text-yellow-600">{counts.training}</p>
          <p className="text-[11px] text-gray-400 mt-0.5">Skill gap · Upskilling required</p>
        </div>
        <div
          className="card p-4 border-l-2 border-l-red-500 cursor-pointer hover:bg-gray-50"
          onClick={() => setCatFilter('hiring')}
        >
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Hiring Recommendations</p>
          <p className="text-2xl font-semibold tabnum mt-1 text-red-600">{counts.hiring}</p>
          <p className="text-[11px] text-gray-400 mt-0.5">No internal match found</p>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex items-center gap-1 border-b border-gray-200">
        {CATEGORY_FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setCatFilter(f.value as ProposalCategory | 'all')}
            className={clsx(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              catFilter === f.value
                ? 'border-gray-900 text-gray-900'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
            )}
          >
            {f.label}
            {f.value !== 'all' && (
              <span className="ml-1.5 badge badge-gray">
                {counts[f.value as ProposalCategory]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Proposals list */}
      <div className="card divide-y divide-gray-100">
        {fetchingPending && proposals.length === 0 ? (
          <p className="px-4 py-8 text-sm text-center text-gray-400">Loading pending allocations…</p>
        ) : filtered.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <p className="text-sm text-gray-400">No proposals in this category.</p>
            <p className="text-xs text-gray-400 mt-1 font-mono">Click "Run Forecast" to trigger the AI allocation engine.</p>
          </div>
        ) : (
          filtered.map((proposal) => {
            const { cls, dot, label } = categoryBadge(proposal.category)
            return (
              <div key={proposal.id} className="px-4 py-4 flex items-start gap-4 hover:bg-gray-50">
                {/* Priority dot */}
                <div className="shrink-0 mt-1 text-lg">{dot}</div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={cls}>{label}</span>
                    <span className="text-xs text-gray-400 font-mono">
                      {format(new Date(proposal.created_at), 'dd MMM · HH:mm')}
                    </span>
                    {(proposal.revision_count ?? 0) > 0 && (
                      <span className="badge badge-gray">Rev. {proposal.revision_count}</span>
                    )}
                  </div>

                  <div className="mt-2 flex gap-6 flex-wrap">
                    <div>
                      <p className="text-[10px] font-mono text-gray-400 uppercase">Employee</p>
                      <p className="text-sm font-semibold text-gray-900 leading-tight">
                        {proposal.employee.full_name}
                      </p>
                      <p className="text-xs text-gray-500">
                        {proposal.employee.seniority} {proposal.employee.primary_role}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-mono text-gray-400 uppercase">Role</p>
                      <p className="text-sm font-semibold text-gray-900 leading-tight">{proposal.role.title}</p>
                      <p className="text-xs text-gray-500">{proposal.project.name}</p>
                    </div>
                    <div>
                      <p className="text-[10px] font-mono text-gray-400 uppercase">Match Score</p>
                      <p className={clsx('text-sm font-semibold tabnum', proposal.match_score >= 0.85 ? 'text-green-700' : proposal.match_score >= 0.70 ? 'text-yellow-600' : 'text-red-600')}>
                        {proposal.match_score > 0 ? `${Math.round(proposal.match_score * 100)}%` : '—'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-mono text-gray-400 uppercase">Projected Margin</p>
                      <p className={clsx('text-sm font-semibold tabnum', marginColor(proposal.projected_margin))}>
                        {proposal.projected_margin > 0 ? `${proposal.projected_margin.toFixed(1)}%` : '—'}
                      </p>
                    </div>
                  </div>

                  <p className="mt-2 text-xs text-gray-500 line-clamp-2 leading-relaxed">
                    {proposal.reasoning}
                  </p>

                  {proposal.missing_skills.length > 0 && (
                    <div className="mt-2 flex gap-1 flex-wrap">
                      <span className="text-[10px] font-mono text-gray-400 mr-1">Missing:</span>
                      {proposal.missing_skills.map((s: string) => (
                        <span key={s} className="badge badge-red">{s}</span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Action */}
                <div className="shrink-0">
                  <button
                    onClick={() => navigate(`/queue/${proposal.id}`, {
                      state: { proposal, forecastRunId, allocationId: proposal.id },
                    })}
                    className="btn-primary text-xs px-3 py-1.5"
                  >
                    Review Proposal →
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

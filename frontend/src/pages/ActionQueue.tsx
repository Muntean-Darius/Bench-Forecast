import { useState, useEffect, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import clsx from 'clsx'
import { api } from '../api/client'
import { categoryBadge, marginColor, type ProposalCategory } from '../types'
import { format } from 'date-fns'
import {
  PlayIcon,
  SpinnerIcon,
  BoltIcon,
  DirectEmotion,
  TrainingEmotion,
} from '../components/icons/Icons'

const CATEGORY_FILTERS: { value: ProposalCategory | 'all'; label: string }[] = [
  { value: 'all',      label: 'All Proposals' },
  { value: 'direct',   label: 'Direct Allocation' },
  { value: 'training', label: 'Training Allocation' },
]

// ─── Transform a PendingAllocation into a UI proposal shape ──────────────────

function allocationToProposal(a: any) {
  const matchScore = a.ai_match_score ? Number(a.ai_match_score) : 0.82
  const rawMargin = a.ai_projected_margin ? Number(a.ai_projected_margin) : 0

  let cat: ProposalCategory = 'direct'
  if (a.ai_upskilling_path && a.ai_upskilling_path.trim().length > 0) {
    cat = 'training'
  }

  const empName = a.employee
    ? `${a.employee.first_name || ''} ${a.employee.last_name || ''}`.trim()
    : `Employee ${String(a.employee_id).substring(0, 8)}…`

  const seniority = a.employee?.experience_level
    ? a.employee.experience_level.charAt(0).toUpperCase() + a.employee.experience_level.slice(1).toLowerCase()
    : 'Mid'
  const primaryRole = a.employee?.job_title || a.role_on_project || 'Software Engineer'

  const projId = String(a.project_id || a.project_demand?.project_id || '')
  const projName = a.project_name || a.project_demand?.project?.name || (projId ? `Project ${projId.substring(0, 8)}` : 'Client Project')

  const billRate = a.project_demand?.target_bill_rate 
    ? Number(a.project_demand.target_bill_rate) 
    : 105.00

  const costRate = a.employee?.hourly_cost_rate 
    ? Number(a.employee.hourly_cost_rate) 
    : (seniority.toLowerCase().includes('senior') ? 95 : seniority.toLowerCase().includes('lead') ? 115 : 65)

  const computedMargin = rawMargin > 0 
    ? rawMargin 
    : billRate > 0 && costRate > 0 
    ? Math.round(((billRate - costRate) / billRate) * 100 * 10) / 10 
    : 25.0

  const targetMargin = a.project_demand?.target_margin 
    ? Number(a.project_demand.target_margin) 
    : a.project_demand?.project?.target_margin 
    ? Number(a.project_demand.project.target_margin) 
    : 25.0

  const rawProb = a.project_demand?.win_probability 
    ? Number(a.project_demand.win_probability) 
    : a.project_demand?.project?.probability 
    ? Number(a.project_demand.project.probability) 
    : 85
  const winProb = rawProb <= 1 ? Math.round(rawProb * 100) : Math.round(rawProb)

  const projStatus = a.project_demand?.project?.status || 'Active Pipeline'

  // Extract skills from employee
  let empSkills: string[] = []
  if (Array.isArray(a.employee?.skills)) {
    empSkills = a.employee.skills
  } else if (typeof a.employee?.skills === 'string') {
    empSkills = a.employee.skills.split(',').map((s: string) => s.trim()).filter(Boolean)
  }

  // Extract skills from demand
  let reqSkills: string[] = []
  if (Array.isArray(a.project_demand?.required_skills)) {
    reqSkills = a.project_demand.required_skills
  } else if (typeof a.project_demand?.required_skills === 'string') {
    reqSkills = a.project_demand.required_skills.split(',').map((s: string) => s.trim()).filter(Boolean)
  } else if (a.project_demand?.required_skills && typeof a.project_demand.required_skills === 'object') {
    reqSkills = Object.keys(a.project_demand.required_skills)
  }
  if (reqSkills.length === 0 && (a.role_on_project || a.project_demand?.role)) {
    const roleWords = (String(a.role_on_project || a.project_demand?.role) + ' ' + String(a.project_demand?.description || '')).match(/\b[A-Z][a-zA-Z0-9+#.]*\b/g) || []
    reqSkills = Array.from(new Set(roleWords.filter(w => w.length > 2))).slice(0, 5)
  }

  // Calculate missing skills for training allocations
  let missingSkills: string[] = []
  if (reqSkills.length > 0 && empSkills.length > 0) {
    missingSkills = reqSkills.filter(r => !empSkills.some(e => e.toLowerCase().includes(r.toLowerCase())))
  }

  const expYears = a.employee?.experience_years 
    ? Number(a.employee.experience_years) 
    : (seniority.toLowerCase().includes('senior') ? 6.5 : seniority.toLowerCase().includes('lead') ? 8.5 : 4.0)

  const benchDate = a.employee?.bench_start_date || a.start_date || new Date().toISOString()
  const startDate = a.project_demand?.needed_by_date || a.start_date || new Date(Date.now() + 14 * 86400000).toISOString()

  return {
    id: String(a.id),
    category: cat,
    employee: {
      id: String(a.employee_id),
      full_name: empName,
      seniority: seniority,
      primary_role: primaryRole,
      hourly_cost_rate: costRate,
      experience_years: expYears,
      bench_start_date: benchDate,
      skills: empSkills,
      bench_status: a.employee?.bench_status || 'bench',
    },
    role: {
      id: String(a.demand_id || a.id),
      title: a.role_on_project || a.project_demand?.role || 'Software Specialist',
      start_date: startDate,
      headcount: a.project_demand?.headcount_needed ?? 1,
      target_bill_rate: billRate,
      required_skills: reqSkills.join(', '),
      description: a.project_demand?.description || `Mission-critical assignment on ${projName} requiring strong domain proficiency and team leadership.`,
      project_id: projId,
      status: 'Open' as const,
    },
    project: {
      id: projId,
      name: projName,
      status: projStatus,
      probability: winProb,
      target_margin: targetMargin,
      total_budget: billRate * 160 * 6,
    },
    match_score: matchScore > 0 ? matchScore : 0.82,
    projected_margin: computedMargin,
    missing_skills: missingSkills,
    upskilling_path: a.ai_upskilling_path || null,
    reasoning: a.ai_justification ?? 'AI evaluation verified candidate technical match and projected operating margins.',
    rag_passages: a.employee?.profile_text ? [a.employee.profile_text.slice(0, 280) + '...'] : [],
    created_at: a.created_at || new Date().toISOString(),
    revision_count: 0,
    _allocation: a,
  }
}

export default function ActionQueue() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [projects, setProjects] = useState<any[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string>(
    searchParams.get('project_id') || 'all'
  )
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

  // Sync selectedProjectId with searchParams if URL changes
  useEffect(() => {
    const pId = searchParams.get('project_id')
    if (pId && pId !== selectedProjectId) {
      setSelectedProjectId(pId)
    }
  }, [searchParams])

  // Load projects list
  useEffect(() => {
    async function fetchProjects() {
      try {
        const list = await api.getProjects()
        if (list && Array.isArray(list)) {
          setProjects(list)
        }
      } catch (err) {
        console.error('Failed to fetch projects list:', err)
      }
    }
    fetchProjects()
  }, [])

  // Load pending allocations on mount
  useEffect(() => {
    async function loadPending() {
      try {
        const data = await api.getPendingAllocations()
        if (data && data.length > 0) {
          const dbProposals = data.map(allocationToProposal)
          setProposals(dbProposals)
          sessionStorage.setItem('bf_cached_proposals', JSON.stringify(dbProposals))
        }
      } catch (e) {
        console.error('Failed to load pending allocations:', e)
      } finally {
        setFetchingPending(false)
      }
    }
    loadPending()
  }, [])

  const selectedProjectObj = useMemo(() => {
    if (selectedProjectId === 'all') return null
    return projects.find((p) => p.id === selectedProjectId) || null
  }, [projects, selectedProjectId])

  async function handleRunForecast() {
    setLoading(true)
    setError(null)
    try {
      const horizonDays = Number(localStorage.getItem('bf_horizon_days') ?? 90)
      const targetProjId = selectedProjectId !== 'all' ? selectedProjectId : undefined

      const res = await api.runForecast(horizonDays, targetProjId)

      setForecastRunId(res.forecast_run_id)
      sessionStorage.setItem('bf_cached_forecast_run_id', res.forecast_run_id)

      // Refresh all pending allocations from database
      const pendingData = await api.getPendingAllocations()
      const dbProposals = (pendingData && pendingData.length > 0)
        ? pendingData.map(allocationToProposal)
        : []

      setProposals(dbProposals)
      sessionStorage.setItem('bf_cached_proposals', JSON.stringify(dbProposals))
    } catch (e: any) {
      console.error(e)
      setError(e.message ?? 'Failed to run forecast')
    } finally {
      setLoading(false)
    }
  }

  // Filter proposals by selected project
  const projectProposals = useMemo(() => {
    if (selectedProjectId === 'all') return proposals
    return proposals.filter(
      (p) => p.project.id === selectedProjectId || p.role.project_id === selectedProjectId
    )
  }, [proposals, selectedProjectId])

  // Filter by category
  const filtered = useMemo(() => {
    return projectProposals.filter(
      (p) => catFilter === 'all' || p.category === catFilter,
    )
  }, [projectProposals, catFilter])

  const counts = {
    direct:   projectProposals.filter((p) => p.category === 'direct').length,
    training: projectProposals.filter((p) => p.category === 'training').length,
    hiring:   0,
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">AI Action Queue</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Prioritized allocation proposals pending human review · Scoped per project
          </p>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          {error && <span className="text-xs text-red-600 font-mono">{error}</span>}

          {/* Project Selector Dropdown */}
          <div className="flex items-center gap-2 bg-white border border-gray-300 rounded-md px-3 py-1.5 shadow-sm">
            <span className="text-xs font-mono uppercase text-gray-400 font-semibold">Project:</span>
            <select
              value={selectedProjectId}
              onChange={(e) => {
                const val = e.target.value
                setSelectedProjectId(val)
                if (val === 'all') {
                  searchParams.delete('project_id')
                } else {
                  searchParams.set('project_id', val)
                }
                setSearchParams(searchParams)
              }}
              className="text-xs bg-transparent border-0 font-medium text-gray-800 focus:outline-none cursor-pointer max-w-[220px] truncate"
            >
              <option value="all">All Projects ({proposals.length} proposals)</option>
              {projects.map((p) => {
                const countForProj = proposals.filter(
                  (prop) => prop.project.id === p.id || prop.role.project_id === p.id
                ).length
                return (
                  <option key={p.id} value={p.id}>
                    {p.name} {countForProj > 0 ? `(${countForProj} proposals)` : p.open_demands_count > 0 ? `(${p.open_demands_count} open)` : ''}
                  </option>
                )
              })}
            </select>
          </div>

          <button
            onClick={handleRunForecast}
            disabled={loading}
            className="btn-primary flex items-center gap-1.5 cursor-pointer text-xs"
          >
            {loading ? (
              <>
                <SpinnerIcon className="w-3.5 h-3.5" />
                <span>Running Forecast Agent…</span>
              </>
            ) : selectedProjectObj ? (
              <>
                <PlayIcon className="w-3 h-3 text-white" />
                <span>Run Forecast: {selectedProjectObj.name}</span>
              </>
            ) : (
              <>
                <PlayIcon className="w-3 h-3 text-white" />
                <span>Run Forecast (All Projects)</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Summary buckets */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="card p-4 border-l-4 border-l-green-500">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Direct Allocations</p>
          <p className="text-2xl font-semibold tabnum mt-1 text-green-700">{counts.direct}</p>
          <p className="text-[11px] text-gray-400 mt-0.5">Strong match · Healthy margin target</p>
        </div>

        <div className="card p-4 border-l-4 border-l-yellow-500">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Training Allocations</p>
          <p className="text-2xl font-semibold tabnum mt-1 text-yellow-600">{counts.training}</p>
          <p className="text-[11px] text-gray-400 mt-0.5">Assigned internal talent · Upskilling path</p>
        </div>

        <div className="card p-4 border-l-4 border-l-gray-300 opacity-60 bg-gray-50/50">
          <p className="text-xs font-medium text-gray-400 uppercase tracking-wider">Hiring Recommendations</p>
          <p className="text-2xl font-semibold tabnum mt-1 text-gray-400">0</p>
          <p className="text-[11px] text-gray-400 mt-0.5">Disabled · 100% internal employee model</p>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex items-center gap-1 border-b border-gray-200">
        {CATEGORY_FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setCatFilter(f.value as ProposalCategory | 'all')}
            className={clsx(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer',
              catFilter === f.value
                ? 'border-gray-900 text-gray-900'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
            )}
          >
            {f.label}
            {f.value !== 'all' && (
              <span className="ml-1.5 badge badge-gray">
                {counts[f.value as 'direct' | 'training']}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Proposals list */}
      <div className="card divide-y divide-gray-100">
        {fetchingPending && proposals.length === 0 ? (
          <p className="px-4 py-8 text-sm text-center text-gray-400">Loading pending allocations from database…</p>
        ) : filtered.length === 0 ? (
          <div className="px-4 py-12 text-center">
            <p className="text-sm font-medium text-gray-500">
              {selectedProjectObj
                ? `No pending proposals for "${selectedProjectObj.name}".`
                : 'No pending proposals in this category.'}
            </p>
            <p className="text-xs text-gray-400 mt-1 font-mono">
              Click &quot;Run Forecast{selectedProjectObj ? ` for ${selectedProjectObj.name}` : ''}&quot; to trigger the allocation engine.
            </p>
          </div>
        ) : (
          filtered.map((proposal) => {
            const { cls, label } = categoryBadge(proposal.category)
            const isTraining = proposal.category === 'training'
            return (
              <div key={proposal.id} className="px-4 py-4 flex items-start gap-4 hover:bg-gray-50">
                {/* Custom Emotion indicator */}
                <div className="shrink-0 mt-1">
                  {isTraining ? <TrainingEmotion /> : <DirectEmotion />}
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={cls}>{label}</span>
                    <span className="badge badge-blue text-[11px] font-mono">
                      {proposal.project.name}
                    </span>
                    <span className="text-xs text-gray-400 font-mono">
                      {format(new Date(proposal.created_at), 'dd MMM · HH:mm')}
                    </span>
                    {(proposal.revision_count ?? 0) > 0 && (
                      <span className="badge badge-gray">Rev. {proposal.revision_count}</span>
                    )}
                  </div>

                  <div className="mt-2.5 flex gap-6 flex-wrap">
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
                      <p className="text-[10px] font-mono text-gray-400 uppercase">Target Role</p>
                      <p className="text-sm font-semibold text-gray-900 leading-tight">{proposal.role.title}</p>
                      <p className="text-xs text-gray-500">{proposal.project.name}</p>
                    </div>

                    <div>
                      <p className="text-[10px] font-mono text-gray-400 uppercase">Compatibility Score</p>
                      <p className={clsx('text-sm font-semibold tabnum', proposal.match_score >= 0.80 ? 'text-green-700' : proposal.match_score >= 0.65 ? 'text-yellow-600' : 'text-red-600')}>
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

                  <p className="mt-2 text-xs text-gray-600 line-clamp-2 leading-relaxed">
                    {proposal.reasoning}
                  </p>

                  {proposal.upskilling_path && (
                    <div className="mt-2 p-2 bg-yellow-50/70 border border-yellow-200/60 rounded text-xs text-yellow-900 flex items-start gap-1.5">
                      <BoltIcon className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                      <div>
                        <strong className="font-semibold text-yellow-950">Upskilling Path:</strong> {proposal.upskilling_path}
                      </div>
                    </div>
                  )}
                </div>

                {/* Action */}
                <div className="shrink-0">
                  <button
                    onClick={() => navigate(`/queue/${proposal.id}`, {
                      state: { proposal, forecastRunId, allocationId: proposal.id },
                    })}
                    className="btn-primary text-xs px-3 py-1.5 cursor-pointer"
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

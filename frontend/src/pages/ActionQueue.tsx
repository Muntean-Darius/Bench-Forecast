import { useState } from 'react'
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

export default function ActionQueue() {
  const navigate = useNavigate()
  const [catFilter, setCatFilter] = useState<ProposalCategory | 'all'>('all')
  const [proposals, setProposals] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [recId, setRecId] = useState<string>('')

  async function handleRunForecast() {
    setLoading(true)
    try {
      const res = await api.generateForecast({
        department_id: "ALL",
        thread_id: "ui-thread",
        horizon_days: 90,
        min_win_probability: 0.75,
      })
      
      const recs = res.recommendations
      if (recs) {
        setRecId(res.recommendation_id)
        
        // Transform backend response to frontend Proposal format
        const uiProposals = []
        for (const alloc of (recs.reallocations || [])) {
          uiProposals.push({
            id: alloc.employee_id + alloc.target_project_id,
            category: 'direct',
            employee: { full_name: `Employee ${alloc.employee_id.substring(0,8)}`, seniority: '', primary_role: '' },
            role: { title: alloc.role },
            project: { name: `Project ${alloc.target_project_id.substring(0,8)}` },
            match_score: alloc.match_score,
            projected_margin: 50.0,
            missing_skills: [],
            reasoning: recs.reasoning,
            created_at: new Date().toISOString(),
            revision_count: 0
          })
        }
        for (const tr of (recs.trainings || [])) {
          uiProposals.push({
            id: tr.employee_id,
            category: 'training',
            employee: { full_name: `Employee ${tr.employee_id.substring(0,8)}`, seniority: '', primary_role: '' },
            role: { title: 'Needs Training' },
            project: { name: 'N/A' },
            match_score: 0.6,
            projected_margin: 0,
            missing_skills: tr.target_skills,
            reasoning: recs.reasoning,
            created_at: new Date().toISOString(),
            revision_count: 0
          })
        }
        setProposals(uiProposals)
      }
    } catch (e) {
      console.error(e)
      alert("Failed to run forecast")
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
        <button 
          onClick={handleRunForecast} 
          disabled={loading}
          className="btn-primary"
        >
          {loading ? 'Running LLM...' : 'Run Forecast'}
        </button>
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
        {filtered.length === 0 ? (
          <p className="px-4 py-8 text-sm text-center text-gray-400">No proposals in this category.</p>
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
                    onClick={() => navigate(`/queue/${proposal.id}`, { state: { proposal, recId } })}
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

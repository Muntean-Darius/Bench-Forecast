import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import type { CvReviewSummary, CvStatusState, TeamMember } from '../types'
import CapacityBar from '../components/shared/CapacityBar'
import { CvStatusBadge } from '../components/shared/Badges'
import { AlertIcon, SpinnerIcon, UserIcon } from '../components/icons/Icons'

function rolloffDays(value?: string | null): number | null {
  if (!value) return null
  const end = new Date(value)
  if (Number.isNaN(end.getTime())) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  end.setHours(0, 0, 0, 0)
  return Math.round((end.getTime() - today.getTime()) / 86_400_000)
}

function shortDate(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function Team() {
  const { user } = useAuth()
  const [team, setTeam] = useState<TeamMember[]>([])
  const [reviews, setReviews] = useState<CvReviewSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    Promise.all([api.getTeam(), api.getCvReviews().catch(() => [])])
      .then(([members, reviewRows]) => {
        setTeam(members)
        setReviews(reviewRows)
      })
      .catch((err) => setError(err?.message || 'Failed to load team workload'))
      .finally(() => setLoading(false))
  }, [])

  const cvStateByEmployee = useMemo(() => {
    const map = new Map<string, CvStatusState>()
    const sorted = [...reviews].sort(
      (a, b) => new Date(b.requested_at).getTime() - new Date(a.requested_at).getTime(),
    )
    for (const review of sorted) {
      if (map.has(review.employee_id)) continue
      if (review.status === 'pending_approval') {
        map.set(review.employee_id, 'pending_approval')
      } else if (review.status === 'rejected') {
        map.set(review.employee_id, 'changes_requested')
      } else {
        map.set(review.employee_id, 'live')
      }
    }
    return map
  }, [reviews])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return team
    return team.filter((member) => {
      const allocations = member.allocations
        .map((a) => `${a.project_name || ''} ${a.role_on_project}`)
        .join(' ')
      return (
        member.employee_name.toLowerCase().includes(q) ||
        member.job_title.toLowerCase().includes(q) ||
        allocations.toLowerCase().includes(q)
      )
    })
  }, [team, query])

  const summary = useMemo(() => {
    const full = team.filter((m) => m.remaining_available_hours <= 0.001).length
    const rolloff = team.filter((m) => {
      const days = rolloffDays(m.next_rolloff_date)
      return days !== null && days >= 0 && days <= 14
    }).length
    const pending = team.filter(
      (m) => cvStateByEmployee.get(m.employee_id) === 'pending_approval',
    ).length
    return { full, rolloff, pending }
  }, [team, cvStateByEmployee])

  const heading = user?.role === 'admin' ? 'Team Workload' : 'My Team'

  return (
    <div className="p-6 flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">{heading}</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            {user?.role === 'admin'
              ? 'Company-wide capacity, allocations and CV state'
              : 'Direct reports, capacity and roll-off risk'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="badge badge-gray">{team.length} people</span>
          <span className="badge badge-yellow">{summary.full} fully allocated</span>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="kpi-card">
          <p className="kpi-label">Team size</p>
          <p className="kpi-value">{team.length}</p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">Fully allocated</p>
          <p className="kpi-value text-yellow-700">{summary.full}</p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">Roll-off ≤ 14 days</p>
          <p className="kpi-value text-red-600">{summary.rolloff}</p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">CVs pending</p>
          <p className="kpi-value text-blue-700">{summary.pending}</p>
        </div>
      </div>

      <div className="card">
        <div className="section-header flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <UserIcon className="w-4 h-4 text-gray-400" />
            <span className="section-title">Capacity &amp; allocations</span>
          </div>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search name, role or project…"
            className="w-64 px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
            <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading team…
          </div>
        ) : error ? (
          <div className="m-4 border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2 flex items-center gap-1.5">
            <AlertIcon className="w-3.5 h-3.5" /> {error}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Employee</th>
                  <th>Capacity</th>
                  <th>Remaining</th>
                  <th>Active projects</th>
                  <th>Roll-off</th>
                  <th>CV status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((member) => {
                  const days = rolloffDays(member.next_rolloff_date)
                  const rolloffRisk = days !== null && days >= 0 && days <= 14
                  const cvState = cvStateByEmployee.get(member.employee_id) || 'live'
                  const activeAllocations = member.allocations.filter(
                    (allocation) =>
                      allocation.status === 'active' ||
                      allocation.status === 'approved_by_manager' ||
                      allocation.status === 'pending_manager_approval',
                  )
                  return (
                    <tr key={member.employee_id}>
                      <td>
                        <p className="text-sm font-semibold text-gray-900">
                          {member.employee_name}
                        </p>
                        <p className="text-[10px] text-gray-500">
                          {member.job_title}
                          {member.manager_name ? ` · ${member.manager_name}` : ''}
                        </p>
                      </td>
                      <td className="min-w-[180px]">
                        <CapacityBar
                          allocated={member.committed_hours_per_day + member.reserved_hours_per_day}
                          capacity={member.daily_capacity_hours}
                          remaining={member.remaining_available_hours}
                        />
                        <p className="text-[10px] text-gray-400 font-mono mt-1">
                          {member.employment_type.replace('_', ' ')}
                        </p>
                      </td>
                      <td className="tabnum font-mono text-sm">
                        {member.remaining_available_hours.toFixed(1)}h
                      </td>
                      <td className="max-w-[300px]">
                        {activeAllocations.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {activeAllocations.map((allocation) => (
                              <span
                                key={allocation.allocation_id}
                                className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium text-gray-700 bg-gray-50 rounded border border-gray-200"
                                title={`${allocation.allocated_hours_per_day.toFixed(1)}h/day`}
                              >
                                {allocation.project_name || allocation.role_on_project}
                                <span className="text-gray-400 font-mono">
                                  {allocation.allocated_hours_per_day.toFixed(1)}h
                                </span>
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-gray-400">On bench</span>
                        )}
                      </td>
                      <td>
                        {rolloffRisk ? (
                          <span className="badge badge-red">
                            {days === 0 ? 'Today' : `${days}d`}
                          </span>
                        ) : member.next_rolloff_date ? (
                          <span className="text-[11px] font-mono text-gray-500">
                            {shortDate(member.next_rolloff_date)}
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">—</span>
                        )}
                      </td>
                      <td><CvStatusBadge state={cvState} /></td>
                    </tr>
                  )
                })}
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={6} className="text-center text-sm text-gray-400 py-10">
                      No team members match the current search.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

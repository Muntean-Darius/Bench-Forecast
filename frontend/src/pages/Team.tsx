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
  const [showAddEmployee, setShowAddEmployee] = useState(false)
  const [empForm, setEmpForm] = useState({
    first_name: '', last_name: '', email: '', job_title: '',
    experience_level: 'mid', employment_type: 'full_time',
    bench_status: 'bench', hourly_cost_rate: 65, skills: '', location: 'Bucharest',
  })
  const [empSubmitting, setEmpSubmitting] = useState(false)
  const [empSuccess, setEmpSuccess] = useState<string | null>(null)
  const [empError, setEmpError] = useState<string | null>(null)
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
          <button onClick={() => setShowAddEmployee(true)} className="btn-primary text-xs">
            + Add Employee
          </button>
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
      {/* Add Employee Modal */}
      {showAddEmployee && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white border border-gray-200 shadow-xl w-full max-w-lg mx-4 p-6 flex flex-col gap-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900">Add Employee</h2>
              <button onClick={() => { setShowAddEmployee(false); setEmpSuccess(null); setEmpError(null) }} className="text-gray-400 hover:text-gray-600 text-lg">&times;</button>
            </div>
            {empSuccess ? (
              <div className="border border-green-200 bg-green-50 text-green-800 text-xs px-3 py-3">
                <p className="font-semibold mb-1">&#x2713; Employee created</p>
                <p>{empSuccess}</p>
                <button onClick={() => { setShowAddEmployee(false); setEmpSuccess(null) }} className="mt-2 btn-primary text-xs">Close</button>
              </div>
            ) : (
              <form onSubmit={async (e) => {
                e.preventDefault()
                setEmpSubmitting(true)
                setEmpError(null)
                try {
                  const res = await api.createEmployee({
                    first_name: empForm.first_name,
                    last_name: empForm.last_name,
                    email: empForm.email,
                    job_title: empForm.job_title,
                    experience_level: empForm.experience_level,
                    employment_type: empForm.employment_type,
                    bench_status: empForm.bench_status,
                    hourly_cost_rate: empForm.hourly_cost_rate,
                    skills: empForm.skills ? empForm.skills.split(',').map(s => s.trim()).filter(Boolean) : [],
                    location: empForm.location,
                  })
                  setEmpSuccess(res.message || `Employee created. Login: "${res.username}"`)
                  const updated = await api.getTeam()
                  setTeam(updated)
                } catch (err: any) {
                  setEmpError(err?.message || 'Failed to create employee')
                } finally {
                  setEmpSubmitting(false)
                }
              }} className="flex flex-col gap-3">
                {empError && <div className="text-xs text-red-600 border border-red-200 bg-red-50 px-3 py-2">{empError}</div>}
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">First Name *</label>
                    <input required value={empForm.first_name} onChange={e => setEmpForm(f => ({ ...f, first_name: e.target.value }))} placeholder="Ion" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Last Name *</label>
                    <input required value={empForm.last_name} onChange={e => setEmpForm(f => ({ ...f, last_name: e.target.value }))} placeholder="Popescu" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-700">Corporate Email *</label>
                  <input required type="email" value={empForm.email} onChange={e => setEmpForm(f => ({ ...f, email: e.target.value }))} placeholder="i.popescu@company.com" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-700">Job Title *</label>
                  <input required value={empForm.job_title} onChange={e => setEmpForm(f => ({ ...f, job_title: e.target.value }))} placeholder="Senior Backend Engineer" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Seniority</label>
                    <select value={empForm.experience_level} onChange={e => setEmpForm(f => ({ ...f, experience_level: e.target.value }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-white focus:outline-none focus:ring-1 focus:ring-gray-400">
                      <option value="junior">Junior</option>
                      <option value="mid">Mid</option>
                      <option value="senior">Senior</option>
                      <option value="lead">Lead</option>
                      <option value="principal">Principal</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Employment</label>
                    <select value={empForm.employment_type} onChange={e => setEmpForm(f => ({ ...f, employment_type: e.target.value }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-white focus:outline-none focus:ring-1 focus:ring-gray-400">
                      <option value="full_time">Full Time</option>
                      <option value="part_time">Part Time</option>
                      <option value="contractor">Contractor</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Bench Status</label>
                    <select value={empForm.bench_status} onChange={e => setEmpForm(f => ({ ...f, bench_status: e.target.value }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-white focus:outline-none focus:ring-1 focus:ring-gray-400">
                      <option value="bench">Bench</option>
                      <option value="on_project">On Project</option>
                      <option value="upcoming_bench">Upcoming Bench</option>
                      <option value="internal">Internal</option>
                    </select>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Hourly Cost (&#8364;/h)</label>
                    <input type="number" min={0} value={empForm.hourly_cost_rate} onChange={e => setEmpForm(f => ({ ...f, hourly_cost_rate: Number(e.target.value) }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Location</label>
                    <input value={empForm.location} onChange={e => setEmpForm(f => ({ ...f, location: e.target.value }))} placeholder="Bucharest" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-700">Skills (comma-separated)</label>
                  <input value={empForm.skills} onChange={e => setEmpForm(f => ({ ...f, skills: e.target.value }))} placeholder="Python, FastAPI, PostgreSQL, Docker" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                </div>
                <div className="flex items-center gap-2 pt-2">
                  <button type="submit" disabled={empSubmitting} className="btn-primary text-xs flex-1">
                    {empSubmitting ? 'Creating\u2026' : 'Add Employee'}
                  </button>
                  <button type="button" onClick={() => setShowAddEmployee(false)} className="btn-ghost text-xs flex-1">Cancel</button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
      </div>
    </div>
  )
}

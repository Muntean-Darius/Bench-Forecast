import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ProjectSummary } from '../types'
import { ProjectStatusBadge } from '../components/shared/Badges'
import { SpinnerIcon, AlertIcon, BriefcaseIcon } from '../components/icons/Icons'

function money(value: number): string {
  return new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(value || 0)
}

export default function Projects() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [showNewProject, setShowNewProject] = useState(false)
  const [newProj, setNewProj] = useState({
    name: '', client_name: '', status: 'Pipeline',
    probability: 75, target_margin: 35, total_budget: 0,
    demand_role: '', demand_skills: '', demand_bill_rate: '',
  })
  const [projSubmitting, setProjSubmitting] = useState(false)
  const [projSuccess, setProjSuccess] = useState<string | null>(null)
  const [projError, setProjError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getProjects()
      .then((rows) => setProjects(rows))
      .catch((err) => setError(err?.message || 'Failed to load projects'))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return projects.filter((project) => {
      const textMatch =
        !q ||
        project.name.toLowerCase().includes(q) ||
        (project.client_name || '').toLowerCase().includes(q)
      const statusMatch =
        statusFilter === 'all' ||
        project.status.toLowerCase() === statusFilter.toLowerCase()
      return textMatch && statusMatch
    })
  }, [projects, query, statusFilter])

  const totals = useMemo(() => {
    const open = projects.reduce((sum, p) => sum + p.open_seats, 0)
    const budget = projects.reduce((sum, p) => sum + p.total_budget, 0)
    return { open, budget }
  }, [projects])

  return (
    <div className="p-6 flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Projects</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            All company projects · open seats, client and staffing health
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="badge badge-gray">{projects.length} projects</span>
          <span className="badge badge-yellow">{totals.open} open seats</span>
          <button onClick={() => setShowNewProject(true)} className="btn-primary text-xs">
            + New Project
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="kpi-card">
          <p className="kpi-label">Portfolio budget</p>
          <p className="kpi-value">{money(totals.budget)}</p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">Active projects</p>
          <p className="kpi-value">
            {projects.filter((p) => p.status.toLowerCase() === 'active').length}
          </p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">Open seats</p>
          <p className="kpi-value text-yellow-700">{totals.open}</p>
        </div>
      </div>

      <div className="card">
        <div className="section-header flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <BriefcaseIcon className="w-4 h-4 text-gray-400" />
            <span className="section-title">Project directory</span>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search project or client…"
              className="w-56 px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
            />
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="text-xs border border-gray-200 bg-white px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-gray-400"
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="pipeline">Pipeline</option>
              <option value="completed">Completed</option>
              <option value="closed">Closed</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
            <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading projects…
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
                  <th>Project</th>
                  <th>Client</th>
                  <th>Status</th>
                  <th>Win %</th>
                  <th>Budget</th>
                  <th>Margin</th>
                  <th>Roles</th>
                  <th>Open seats</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((project) => (
                  <tr key={project.id}>
                    <td>
                      <Link
                        to={`/projects/${project.id}`}
                        className="text-sm font-semibold text-gray-900 hover:underline"
                      >
                        {project.name}
                      </Link>
                    </td>
                    <td className="text-gray-600">{project.client_name || '—'}</td>
                    <td><ProjectStatusBadge status={project.status} /></td>
                    <td className="tabnum">{project.probability}%</td>
                    <td className="tabnum">{money(project.total_budget)}</td>
                    <td className="tabnum">{project.target_margin.toFixed(1)}%</td>
                    <td className="text-xs text-gray-500">
                      {project.filled_roles} filled
                      {project.partially_filled_roles > 0
                        ? ` · ${project.partially_filled_roles} partial`
                        : ''}
                      {project.open_roles > 0 ? ` · ${project.open_roles} open` : ''}
                    </td>
                    <td>
                      <span
                        className={`badge ${
                          project.open_seats > 0 ? 'badge-yellow' : 'badge-green'
                        }`}
                      >
                        {project.open_seats}
                      </span>
                    </td>
                    <td className="text-right">
                      <Link
                        to={`/projects/${project.id}`}
                        className="text-xs font-medium text-gray-700 hover:text-gray-900"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={9} className="text-center text-sm text-gray-400 py-10">
                      No projects match the current filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      {/* New Project Modal */}
      {showNewProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white border border-gray-200 shadow-xl w-full max-w-lg mx-4 p-6 flex flex-col gap-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900">New Project</h2>
              <button onClick={() => { setShowNewProject(false); setProjSuccess(null); setProjError(null) }} className="text-gray-400 hover:text-gray-600 text-lg">&times;</button>
            </div>
            {projSuccess ? (
              <div className="border border-green-200 bg-green-50 text-green-800 text-xs px-3 py-3">
                <p className="font-semibold">&#x2713; {projSuccess}</p>
                <button onClick={() => { setShowNewProject(false); setProjSuccess(null) }} className="mt-2 btn-primary text-xs">Close</button>
              </div>
            ) : (
              <form onSubmit={async (e) => {
                e.preventDefault()
                setProjSubmitting(true)
                setProjError(null)
                try {
                  const initial_demands = newProj.demand_role ? [{
                    role: newProj.demand_role,
                    required_skills: newProj.demand_skills ? newProj.demand_skills.split(',').map(s => s.trim()).filter(Boolean) : [],
                    target_bill_rate: newProj.demand_bill_rate ? Number(newProj.demand_bill_rate) : undefined,
                  }] : []
                  await api.createProject({
                    name: newProj.name,
                    client_name: newProj.client_name || undefined,
                    status: newProj.status,
                    probability: newProj.probability,
                    target_margin: newProj.target_margin,
                    total_budget: newProj.total_budget,
                    initial_demands,
                  })
                  setProjSuccess(`Project "${newProj.name}" created successfully!`)
                  const updated = await api.getProjects()
                  setProjects(updated)
                } catch (err: any) {
                  setProjError(err?.message || 'Failed to create project')
                } finally {
                  setProjSubmitting(false)
                }
              }} className="flex flex-col gap-3">
                {projError && <div className="text-xs text-red-600 border border-red-200 bg-red-50 px-3 py-2">{projError}</div>}
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-700">Project Name *</label>
                  <input required value={newProj.name} onChange={e => setNewProj(p => ({ ...p, name: e.target.value }))} placeholder="e.g. FinTech Core Banking" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-700">Client Name</label>
                  <input value={newProj.client_name} onChange={e => setNewProj(p => ({ ...p, client_name: e.target.value }))} placeholder="e.g. Acme Corp" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Status</label>
                    <select value={newProj.status} onChange={e => setNewProj(p => ({ ...p, status: e.target.value }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-white focus:outline-none focus:ring-1 focus:ring-gray-400">
                      <option>Pipeline</option>
                      <option>Active</option>
                      <option>Closed</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Win Probability (%)</label>
                    <input type="number" min={0} max={100} value={newProj.probability} onChange={e => setNewProj(p => ({ ...p, probability: Number(e.target.value) }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Target Margin (%)</label>
                    <input type="number" min={0} max={100} value={newProj.target_margin} onChange={e => setNewProj(p => ({ ...p, target_margin: Number(e.target.value) }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">Total Budget (&#8364;)</label>
                    <input type="number" min={0} value={newProj.total_budget} onChange={e => setNewProj(p => ({ ...p, total_budget: Number(e.target.value) }))} className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                </div>
                <div className="border-t border-gray-100 pt-3">
                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Initial Role (optional)</p>
                  <div className="flex flex-col gap-2">
                    <input value={newProj.demand_role} onChange={e => setNewProj(p => ({ ...p, demand_role: e.target.value }))} placeholder="Role title (e.g. Senior React Developer)" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                    <input value={newProj.demand_skills} onChange={e => setNewProj(p => ({ ...p, demand_skills: e.target.value }))} placeholder="Skills (comma-separated: React, TypeScript, Tailwind)" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                    <input type="number" value={newProj.demand_bill_rate} onChange={e => setNewProj(p => ({ ...p, demand_bill_rate: e.target.value }))} placeholder="Bill rate (&#8364;/h, optional)" className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400" />
                  </div>
                </div>
                <div className="flex items-center gap-2 pt-2">
                  <button type="submit" disabled={projSubmitting} className="btn-primary text-xs flex-1">
                    {projSubmitting ? 'Creating\u2026' : 'Create Project'}
                  </button>
                  <button type="button" onClick={() => setShowNewProject(false)} className="btn-ghost text-xs flex-1">Cancel</button>
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

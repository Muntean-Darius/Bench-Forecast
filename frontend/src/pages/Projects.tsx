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
      </div>
    </div>
  )
}

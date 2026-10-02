import { useState, useMemo } from 'react'
import clsx from 'clsx'

import { type Employee, type ProjectRole } from '../types'
import { format } from 'date-fns'

// ─── Shared sort hook ─────────────────────────────────────────────────────────

type SortDir = 'asc' | 'desc'

function useSort<T>(data: T[], initial: keyof T) {
  const [col, setCol]   = useState<keyof T>(initial)
  const [dir, setDir]   = useState<SortDir>('asc')

  const sorted = useMemo(() => {
    return [...data].sort((a, b) => {
      const av = a[col], bv = b[col]
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return dir === 'asc' ? cmp : -cmp
    })
  }, [data, col, dir])

  function toggle(c: keyof T) {
    if (c === col) setDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setCol(c); setDir('asc') }
  }

  return { sorted, col, dir, toggle }
}

function SortTh<T>({
  col, active, dir, onClick, children,
}: {
  col: keyof T; active: keyof T; dir: SortDir; onClick: () => void; children: React.ReactNode
}) {
  const isActive = col === active
  return (
    <th
      onClick={onClick}
      className="cursor-pointer select-none whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 bg-gray-50 hover:bg-gray-100"
    >
      <span className="flex items-center gap-1">
        {children}
        <span className={clsx('font-mono text-[10px]', isActive ? 'text-gray-700' : 'text-gray-300')}>
          {isActive ? (dir === 'asc' ? '▲' : '▼') : '⇅'}
        </span>
      </span>
    </th>
  )
}

// ─── Bench table ──────────────────────────────────────────────────────────────
  
  
  function BenchTable({ filter, employees }: { filter: string, employees: Employee[] }) {
    const filtered = employees.filter(
      (e) =>
        !filter ||
        e.full_name.toLowerCase().includes(filter.toLowerCase()) ||
        e.primary_role.toLowerCase().includes(filter.toLowerCase()),
    )
  
    const { sorted, col, dir, toggle } = useSort(filtered, 'bench_start_date')
  
    return (
      <div className="card flex flex-col">
        <div className="section-header">
          <p className="section-title">
            Bench (Supply)
            <span className="ml-2 badge badge-red">{filtered.length} people</span>
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="full_name"        active={col as keyof Employee} dir={dir} onClick={() => toggle('full_name')}>Name</SortTh>
                <SortTh col="seniority"        active={col as keyof Employee} dir={dir} onClick={() => toggle('seniority')}>Seniority</SortTh>
                <SortTh col="primary_role"     active={col as keyof Employee} dir={dir} onClick={() => toggle('primary_role')}>Role</SortTh>
                <SortTh col="hourly_cost_rate" active={col as keyof Employee} dir={dir} onClick={() => toggle('hourly_cost_rate')}>Cost Rate / hr</SortTh>
                <SortTh col="bench_start_date" active={col as keyof Employee} dir={dir} onClick={() => toggle('bench_start_date')}>Bench Since</SortTh>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 bg-gray-50">CV</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((e) => {
                const seniority = e.seniority
                const senColor = {
                  Junior: 'badge-gray',
                  Mid: 'badge-blue',
                  Senior: 'badge-green',
                  Principal: 'badge badge-yellow',
                }[seniority] || 'badge-gray'
  
                return (
                  <tr key={e.id}>
                    <td className="font-medium">{e.full_name}</td>
                    <td>
                      <span className={clsx('badge', senColor)}>{seniority}</span>
                    </td>
                    <td className="text-gray-600">{e.primary_role}</td>
                    <td className="tabnum font-mono text-red-700">
                      €{e.hourly_cost_rate.toFixed(2)}/h
                    </td>
                    <td className="tabnum font-mono text-gray-500">
                      {format(new Date(e.bench_start_date), 'dd MMM yyyy')}
                    </td>
                    <td>
                      {e.cv_document_uri ? (
                        <a
                          href={`/cvs/${e.cv_document_uri}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-blue-600 hover:text-blue-700 font-mono underline"
                        >
                          CV ↗
                        </a>
                      ) : (
                        <span className="text-gray-300 text-xs">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    )
  }
  
  // ─── Projects table ───────────────────────────────────────────────────────────
  
  const STATUS_BADGE: Record<string, string> = {
    Open: 'badge badge-blue',
    Filled: 'badge badge-green',
    'Requires Training': 'badge badge-yellow',
  }
  
  function ProjectsTable({ filter, roles }: { filter: string, roles: ProjectRole[] }) {
    const [statusFilter, setStatusFilter] = useState<string>('all')
  
    const filtered = roles.filter((r) => {
    const matchText =
      !filter ||
      r.title.toLowerCase().includes(filter.toLowerCase()) ||
      (r.project_name ?? '').toLowerCase().includes(filter.toLowerCase())
    const matchStatus = statusFilter === 'all' || r.status === statusFilter
    return matchText && matchStatus
  })

  const { sorted, col, dir, toggle } = useSort(filtered, 'start_date')

  return (
    <div className="card flex flex-col">
      <div className="section-header">
        <p className="section-title">
          Projects (Demand)
          <span className="ml-2 badge badge-blue">{filtered.length} roles</span>
        </p>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500">Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-xs border border-gray-200 bg-white px-2 py-1 focus:outline-none focus:ring-1 focus:ring-gray-400"
          >
            <option value="all">All</option>
            <option value="Open">Open</option>
            <option value="Filled">Filled</option>
            <option value="Requires Training">Requires Training</option>
          </select>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <SortTh col="project_name"    active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('project_name')}>Project</SortTh>
              <SortTh col="title"           active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('title')}>Role Title</SortTh>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 bg-gray-50">Required Skills</th>
              <SortTh col="target_bill_rate" active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('target_bill_rate')}>Bill Rate / hr</SortTh>
              <SortTh col="start_date"      active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('start_date')}>Start Date</SortTh>
              <SortTh col="status"          active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('status')}>Status</SortTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.id}>
                <td>
                  <div className="font-medium text-gray-800">{r.project_name}</div>
                  <div className={clsx('text-[10px] font-mono mt-0.5', {
                    'text-green-600': r.project_status === 'Active',
                    'text-blue-500': r.project_status === 'Pipeline',
                    'text-gray-400': r.project_status === 'Closed',
                  })}>
                    {r.project_status}
                  </div>
                </td>
                <td className="font-medium max-w-[180px] truncate">{r.title}</td>
                <td className="max-w-[220px]">
                  <div className="flex flex-wrap gap-1">
                    {(r.required_skills ?? '').split(',').slice(0, 3).map((s) => (
                      <span key={s} className="badge badge-gray">{s.trim()}</span>
                    ))}
                    {(r.required_skills ?? '').split(',').length > 3 && (
                      <span className="badge badge-gray">+{(r.required_skills ?? '').split(',').length - 3}</span>
                    )}
                  </div>
                </td>
                <td className="tabnum font-mono text-green-700">€{r.target_bill_rate.toFixed(2)}/h</td>
                <td className="tabnum font-mono text-gray-500">
                  {format(new Date(r.start_date), 'dd MMM yyyy')}
                </td>
                <td>
                  <span className={STATUS_BADGE[r.status] || 'badge badge-gray'}>{r.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Bench & Pipeline page ────────────────────────────────────────────────────

import { useEffect } from 'react'
import { api } from '../api/client'

export default function BenchPipeline() {
  const [filter, setFilter] = useState('')
  const [employees, setEmployees] = useState<any[]>([])
  const [demands, setDemands] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function loadData() {
      try {
        const [empData, demData] = await Promise.all([
          api.getEmployees(90),
          api.getDemands(0.75),
        ])
        setEmployees(empData.map((e: any) => ({
          id: e.id,
          full_name: e.name,
          primary_role: e.skills[0] || 'Unknown',
          seniority: e.skills[1] || 'Unknown',
          hourly_cost_rate: e.cost_rate,
          bench_start_date: e.available_from,
          cv_document_uri: e.profile_text ? 'text' : '', // Mock for now
        })))
        setDemands(demData.map((d: any) => ({
          id: d.id,
          title: d.role,
          project_name: 'Project ' + d.project_id.substring(0, 4),
          project_status: 'Active',
          required_skills: (d.required_skills || []).join(', '),
          target_bill_rate: 100, // mock
          start_date: d.start_date,
          status: 'Open',
        })))
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [])

  if (loading) {
    return <div className="p-6 text-gray-500">Loading data from PostgreSQL...</div>
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Bench &amp; Pipeline</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Supply vs. Demand · {employees.length} bench · {demands.filter((r) => r.status === 'Open').length} open roles
          </p>
        </div>
        <div className="relative">
          <input
            type="text"
            placeholder="Filter by name, role, project…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-sm border border-gray-200 bg-white px-3 py-1.5 w-64 focus:outline-none focus:ring-1 focus:ring-gray-400 placeholder:text-gray-400"
          />
        </div>
      </div>

      <BenchTable filter={filter} employees={employees} />
      <ProjectsTable filter={filter} roles={demands} />
    </div>
  )
}

import { useState, useMemo, useEffect } from 'react'
import clsx from 'clsx'
import { type Employee, type ProjectRole } from '../types'
import { format } from 'date-fns'
import { api } from '../api/client'

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

// ─── Normalize employee from API response ─────────────────────────────────────

function normalizeEmployee(e: any): Employee {
  const skills: string[] = Array.isArray(e.skills)
    ? e.skills
    : typeof e.skills === 'string'
    ? e.skills.split(',').map((s: string) => s.trim()).filter(Boolean)
    : []

  const seniority = e.experience_level
    ? e.experience_level.charAt(0).toUpperCase() + e.experience_level.slice(1).toLowerCase()
    : detectSeniority(e.experience_years)

  const role = e.job_title || (skills.length > 0 ? skills[0] : 'Engineer')

  return {
    id: e.id,
    name: e.name || e.full_name,
    full_name: e.full_name || e.name,
    primary_role: role,
    seniority: seniority,
    hourly_cost_rate: e.cost_rate ?? 0,
    bench_start_date: e.available_from ?? '',
    available_from: e.available_from,
    cv_document_uri: e.cv_document_uri || (e.profile_text ? 'profile' : null),
    profile_text: e.profile_text,
    skills: skills,
    experience_years: e.experience_years,
    current_project: e.current_project,
  }
}

function detectSeniority(years?: number): string {
  if (!years) return 'Mid'
  if (years >= 8) return 'Principal'
  if (years >= 5) return 'Senior'
  if (years >= 2) return 'Mid'
  return 'Junior'
}

// ─── Normalize demand/role from API response ──────────────────────────────────

function normalizeDemand(d: any): ProjectRole {
  const skills: string[] = Array.isArray(d.required_skills) ? d.required_skills : []
  return {
    id: d.id,
    project_id: d.project_id,
    title: d.role,
    project_name: `Project ${String(d.project_id).substring(0, 8)}`,
    project_status: 'Active',
    required_skills: skills.join(', '),
    target_bill_rate: 100, // not provided by API; shown as placeholder
    start_date: d.start_date ?? new Date().toISOString().split('T')[0],
    status: 'Open',
    headcount: d.headcount ?? 1,
    win_probability: d.win_probability,
    description: d.description,
  }
}

// ─── Bench table ──────────────────────────────────────────────────────────────

function BenchTable({
  filter,
  employees,
  onOpenCV,
}: {
  filter: string
  employees: Employee[]
  onOpenCV: (emp: Employee) => void
}) {
  const q = filter.trim().toLowerCase()
  const filtered = employees.filter((e) => {
    if (!q) return true
    const nameMatch = (e.full_name ?? e.name ?? '').toLowerCase().includes(q)
    const roleMatch = (e.primary_role ?? '').toLowerCase().includes(q)
    const senMatch = (e.seniority ?? '').toLowerCase().includes(q)
    const skillsList = Array.isArray(e.skills)
      ? e.skills
      : typeof e.skills === 'string'
      ? e.skills.split(',').map((s) => s.trim())
      : []
    const skillsMatch = skillsList.some((s) => s.toLowerCase().includes(q))
    return nameMatch || roleMatch || senMatch || skillsMatch
  })

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
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 bg-gray-50">Skills</th>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200 bg-gray-50">CV</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((e) => {
              const seniority = e.seniority ?? 'Mid'
              const senColor = ({
                Junior: 'badge-gray',
                Mid: 'badge-blue',
                Senior: 'badge-green',
                Principal: 'badge badge-yellow',
              } as Record<string, string>)[seniority] || 'badge-gray'

              const skillsArr: string[] = Array.isArray(e.skills)
                ? e.skills
                : typeof e.skills === 'string'
                ? e.skills.split(',').map((s: string) => s.trim())
                : []

              const benchDate = e.bench_start_date || e.available_from
              let formattedDate = '—'
              try {
                if (benchDate) formattedDate = format(new Date(benchDate), 'dd MMM yyyy')
              } catch {}

              return (
                <tr key={e.id}>
                  <td className="font-medium">{e.full_name ?? e.name}</td>
                  <td>
                    <span className={clsx('badge', senColor)}>{seniority}</span>
                  </td>
                  <td className="text-gray-600">{e.primary_role ?? 'Engineer'}</td>
                  <td className="tabnum font-mono text-red-700">
                    €{Number(e.hourly_cost_rate ?? 0).toFixed(2)}/h
                  </td>
                  <td className="tabnum font-mono text-gray-500">{formattedDate}</td>
                  <td className="max-w-[200px]">
                    <div className="flex flex-wrap gap-1">
                      {skillsArr.slice(0, 3).map((s, i) => (
                        <span key={i} className="badge badge-gray">{s}</span>
                      ))}
                      {skillsArr.length > 3 && (
                        <span className="badge badge-gray">+{skillsArr.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <button
                      onClick={() => onOpenCV(e)}
                      className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 transition-colors cursor-pointer"
                      title="View CV & Profile"
                    >
                      <span>📄</span> View CV
                    </button>
                  </td>
                </tr>
              )
            })}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-gray-400 text-sm py-6">
                  No bench employees found.
                </td>
              </tr>
            )}
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

function ProjectsTable({ filter, roles }: { filter: string; roles: ProjectRole[] }) {
  const [statusFilter, setStatusFilter] = useState<string>('all')

  const filtered = roles.filter((r) => {
    const matchText =
      !filter ||
      r.title.toLowerCase().includes(filter.toLowerCase()) ||
      (r.project_name ?? '').toLowerCase().includes(filter.toLowerCase()) ||
      (r.required_skills ?? '').toLowerCase().includes(filter.toLowerCase())
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
              <SortTh col="win_probability" active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('win_probability')}>Win Prob.</SortTh>
              <SortTh col="start_date"      active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('start_date')}>Start Date</SortTh>
              <SortTh col="status"          active={col as keyof ProjectRole} dir={dir} onClick={() => toggle('status')}>Status</SortTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const skillsArr = (r.required_skills ?? '').split(',').map((s) => s.trim()).filter(Boolean)
              let formattedDate = '—'
              try { if (r.start_date) formattedDate = format(new Date(r.start_date), 'dd MMM yyyy') } catch {}

              return (
                <tr key={r.id}>
                  <td>
                    <div className="font-medium text-gray-800">{r.project_name}</div>
                    <div className={clsx('text-[10px] font-mono mt-0.5', {
                      'text-green-600': r.project_status === 'Active',
                      'text-blue-500': r.project_status === 'Pipeline',
                      'text-gray-400': r.project_status === 'Closed',
                    })}>
                      {r.project_status ?? 'Active'}
                    </div>
                  </td>
                  <td className="font-medium max-w-[180px] truncate">{r.title}</td>
                  <td className="max-w-[220px]">
                    <div className="flex flex-wrap gap-1">
                      {skillsArr.slice(0, 3).map((s) => (
                        <span key={s} className="badge badge-gray">{s}</span>
                      ))}
                      {skillsArr.length > 3 && (
                        <span className="badge badge-gray">+{skillsArr.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td className="tabnum font-mono">
                    {r.win_probability !== undefined
                      ? <span className={clsx(
                          'font-semibold',
                          r.win_probability >= 0.85 ? 'text-green-700' :
                          r.win_probability >= 0.7  ? 'text-yellow-600' : 'text-red-600'
                        )}>{Math.round((r.win_probability ?? 0) * 100)}%</span>
                      : '—'
                    }
                  </td>
                  <td className="tabnum font-mono text-gray-500">{formattedDate}</td>
                  <td>
                    <span className={STATUS_BADGE[r.status] || 'badge badge-gray'}>{r.status}</span>
                  </td>
                </tr>
              )
            })}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center text-gray-400 text-sm py-6">
                  No open project roles found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Bench & Pipeline page ────────────────────────────────────────────────────

// ─── CV Viewer Modal ──────────────────────────────────────────────────────────

function CVModal({
  employee,
  onClose,
}: {
  employee: Employee | null
  onClose: () => void
}) {
  if (!employee) return null

  const [activeTab, setActiveTab] = useState<'pdf' | 'text'>('pdf')
  const [profileText, setProfileText] = useState<string>(employee.profile_text || '')

  useEffect(() => {
    if (employee?.id) {
      setProfileText(employee.profile_text || '')
      // Fetch latest profile text in case a new CV was recently uploaded
      api.getEmployeePortal(employee.id)
        .then((res: any) => {
          if (res?.employee?.profile_text) {
            setProfileText(res.employee.profile_text)
          }
        })
        .catch(() => {})
    }
  }, [employee?.id])

  const skillsArr = Array.isArray(employee.skills)
    ? employee.skills
    : typeof employee.skills === 'string'
    ? employee.skills.split(',').map((s) => s.trim()).filter(Boolean)
    : []

  const downloadUrl = `/api/v1/documents/employee/${employee.id}/download`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-white rounded-lg shadow-2xl w-full max-w-4xl h-[90vh] flex flex-col overflow-hidden animate-fadeIn">
        {/* Header */}
        <div className="px-6 py-3 border-b border-gray-200 flex items-center justify-between bg-gray-50 shrink-0">
          <div>
            <h2 className="text-base font-semibold text-gray-900">
              {employee.full_name ?? employee.name}
            </h2>
            <p className="text-xs text-gray-500 font-mono mt-0.5">
              {employee.seniority} · {employee.primary_role} · €{Number(employee.hourly_cost_rate ?? 0).toFixed(2)}/h
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={downloadUrl}
              target="_blank"
              rel="noopener noreferrer"
              download={`${employee.full_name || 'employee'}_CV.pdf`}
              className="btn-primary text-xs px-3 py-1.5 flex items-center gap-1.5"
            >
              <span>⬇</span> Download PDF
            </a>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 p-1 text-lg font-bold leading-none cursor-pointer"
            >
              ✕
            </button>
          </div>
        </div>

        {/* View Tabs & Skills Bar */}
        <div className="px-6 py-2 bg-gray-100 border-b border-gray-200 flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setActiveTab('pdf')}
              className={clsx(
                'px-3 py-1 text-xs font-medium rounded transition-colors',
                activeTab === 'pdf'
                  ? 'bg-white text-gray-900 shadow-sm border border-gray-200'
                  : 'text-gray-600 hover:text-gray-900'
              )}
            >
              📄 CV Document (PDF)
            </button>
            <button
              onClick={() => setActiveTab('text')}
              className={clsx(
                'px-3 py-1 text-xs font-medium rounded transition-colors',
                activeTab === 'text'
                  ? 'bg-white text-gray-900 shadow-sm border border-gray-200'
                  : 'text-gray-600 hover:text-gray-900'
              )}
            >
              📝 Extracted Profile Text
            </button>
          </div>

          <div className="flex items-center gap-1.5 overflow-hidden">
            <span className="text-[10px] font-mono text-gray-400 uppercase">Skills:</span>
            {skillsArr.slice(0, 5).map((s, idx) => (
              <span key={idx} className="badge badge-blue text-[10px] font-mono">{s}</span>
            ))}
            {skillsArr.length > 5 && (
              <span className="text-[10px] text-gray-400">+{skillsArr.length - 5}</span>
            )}
          </div>
        </div>

        {/* Body / CV Content */}
        <div className="flex-1 overflow-hidden flex flex-col bg-gray-50">
          {activeTab === 'pdf' ? (
            <div className="flex-1 w-full h-full relative">
              <iframe
                src={downloadUrl}
                className="w-full h-full border-0"
                title={`${employee.full_name || 'employee'} CV`}
              />
            </div>
          ) : (
            <div className="p-6 overflow-y-auto flex-1 font-sans text-sm text-gray-800 leading-relaxed">
              {profileText ? (
                <div className="bg-white p-5 rounded border border-gray-200 font-mono text-xs text-gray-700 leading-relaxed whitespace-pre-wrap shadow-sm">
                  {profileText}
                </div>
              ) : (
                <div className="py-16 text-center text-gray-400">
                  <p className="text-sm">No text profile content currently extracted.</p>
                  <p className="text-xs mt-1 text-gray-400">You can download or view the PDF document using the PDF tab above.</p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-2.5 border-t border-gray-200 bg-white flex justify-end shrink-0">
          <button onClick={onClose} className="btn-ghost text-xs px-4 py-1.5">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Bench & Pipeline page ────────────────────────────────────────────────────

export default function BenchPipeline() {
  const [filter, setFilter] = useState('')
  const [employees, setEmployees] = useState<Employee[]>([])
  const [demands, setDemands] = useState<ProjectRole[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedEmpCV, setSelectedEmpCV] = useState<Employee | null>(null)

  useEffect(() => {
    async function loadData() {
      try {
        const horizonDays = Number(localStorage.getItem('bf_horizon_days') ?? 90)
        const minWinProb = Number(localStorage.getItem('bf_min_win_prob') ?? 0.5)

        const [empData, demData] = await Promise.all([
          api.getEmployees(horizonDays),
          api.getDemands(minWinProb),
        ])

        setEmployees((empData as any[]).map(normalizeEmployee))
        setDemands((demData as any[]).map(normalizeDemand))
      } catch (e: any) {
        console.error(e)
        setError(e.message ?? 'Failed to load data')
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [])

  if (loading) {
    return <div className="p-6 text-gray-500">Loading data from database…</div>
  }

  if (error) {
    return <div className="p-6 text-red-600 text-sm">Error: {error}</div>
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
            placeholder="Filter by name, role, skills, project…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-sm border border-gray-200 bg-white px-3 py-1.5 w-72 focus:outline-none focus:ring-1 focus:ring-gray-400 placeholder:text-gray-400"
          />
        </div>
      </div>

      <BenchTable filter={filter} employees={employees} onOpenCV={(emp) => setSelectedEmpCV(emp)} />
      <ProjectsTable filter={filter} roles={demands} />

      <CVModal employee={selectedEmpCV} onClose={() => setSelectedEmpCV(null)} />
    </div>
  )
}

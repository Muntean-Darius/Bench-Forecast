import type { ProjectSummary, ProjectUtilization } from '../../types'
import CapacityBar from '../shared/CapacityBar'
import { ProjectStatusBadge } from '../shared/Badges'

function money(value: number): string {
  return new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(value || 0)
}

function shortDate(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function ProjectSummaryCard({
  project,
  utilization,
}: {
  project: ProjectSummary
  utilization: ProjectUtilization
}) {
  const seatsPct =
    utilization.required_seats > 0
      ? Math.min(100, (utilization.filled_seats / utilization.required_seats) * 100)
      : 0

  return (
    <div className="card p-5 flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-lg font-semibold text-gray-900 tracking-tight">
              {project.name}
            </h1>
            <ProjectStatusBadge status={project.status} />
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Client: <span className="text-gray-700 font-medium">{project.client_name || '—'}</span>
            <span className="mx-2 text-gray-300">·</span>
            {shortDate(project.start_date)} → {shortDate(project.end_date)}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Win probability</p>
          <p className="text-2xl font-semibold tabnum text-gray-900">{project.probability}%</p>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-0 border-t border-gray-100 pt-4">
        <div className="pr-4 border-r border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Total budget</p>
          <p className="text-base font-semibold tabnum mt-1">{money(project.total_budget)}</p>
        </div>
        <div className="px-4 border-r border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Target margin</p>
          <p className="text-base font-semibold tabnum mt-1">{project.target_margin.toFixed(1)}%</p>
        </div>
        <div className="px-4 border-r border-gray-100">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Daily hours</p>
          <p className="text-base font-semibold tabnum mt-1">
            {utilization.allocated_hours_per_day.toFixed(1)}h
            <span className="text-xs text-gray-400 font-normal">
              {' '}/ {utilization.demanded_hours_per_day.toFixed(1)}h
            </span>
          </p>
        </div>
        <div className="pl-4">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">Seats filled</p>
          <p className="text-base font-semibold tabnum mt-1">
            {utilization.filled_seats}
            <span className="text-xs text-gray-400 font-normal"> / {utilization.required_seats}</span>
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div>
          <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
            <span>Daily staffing</span>
            <span className="tabnum text-gray-600">{utilization.utilization_pct.toFixed(0)}%</span>
          </div>
          <CapacityBar
            allocated={utilization.allocated_hours_per_day}
            capacity={utilization.demanded_hours_per_day || 8}
            remaining={Math.max(
              0,
              utilization.demanded_hours_per_day - utilization.allocated_hours_per_day,
            )}
            showLabels={false}
          />
        </div>
        <div>
          <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
            <span>Seats filled</span>
            <span className="tabnum text-gray-600">{seatsPct.toFixed(0)}%</span>
          </div>
          <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-gray-900 rounded-full transition-all"
              style={{ width: `${seatsPct}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

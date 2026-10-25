import clsx from 'clsx'
import type { ProjectAssignment, RosterDemand } from '../../types'
import { RosterStatusBadge } from '../shared/Badges'

function seniorityLabel(value?: string | null): string {
  if (!value) return 'Any seniority'
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function AssignmentCard({ assignment }: { assignment: ProjectAssignment }) {
  const score =
    assignment.ai_match_score !== null && assignment.ai_match_score !== undefined
      ? Math.round(assignment.ai_match_score * 100)
      : null

  return (
    <div
      className="border border-gray-200 bg-white px-2.5 py-2 min-w-[190px]"
      title={assignment.ai_justification || undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-gray-900 truncate">
            {assignment.employee_name}
          </p>
          <p className="text-[10px] text-gray-500 truncate">
            {assignment.job_title || assignment.status}
          </p>
        </div>
        {score !== null && (
          <span
            className={clsx(
              'badge shrink-0',
              score >= 80 ? 'badge-green' : score >= 60 ? 'badge-yellow' : 'badge-gray',
            )}
          >
            {score}%
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 mt-1.5 text-[10px] font-mono">
        <span className="text-gray-700 tabnum">
          {assignment.allocated_hours_per_day.toFixed(1)}h/day ·{' '}
          {assignment.allocation_pct.toFixed(0)}%
        </span>
        <span className="text-green-700 tabnum">
          {assignment.remaining_available_hours.toFixed(1)}h free
        </span>
      </div>
    </div>
  )
}

export default function RoleRosterTable({
  roster,
  onAssign,
  onRecommendations,
}: {
  roster: RosterDemand[]
  onAssign: (demand: RosterDemand) => void
  onRecommendations: (demand: RosterDemand) => void
}) {
  return (
    <div className="card overflow-hidden">
      <div className="section-header">
        <div>
          <h2 className="section-title">Role Roster</h2>
          <p className="text-[10px] text-gray-400 mt-0.5">
            Demands, staffing state and assigned talent
          </p>
        </div>
        <span className="badge badge-gray">{roster.length} roles</span>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Role &amp; requirements</th>
              <th>Status</th>
              <th>Hours/day</th>
              <th>Seats</th>
              <th>Assigned talent</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {roster.map((demand) => {
              const openSeats = Math.max(
                demand.headcount_required - demand.headcount_filled,
                0,
              )
              return (
                <tr key={demand.demand_id} className="align-top">
                  <td className="min-w-[260px]">
                    <p className="text-sm font-semibold text-gray-900">{demand.role}</p>
                    <p className="text-[10px] text-gray-500 mt-0.5">
                      {seniorityLabel(demand.seniority_level)}
                      {demand.target_bill_rate
                        ? ` · €${demand.target_bill_rate.toFixed(0)}/h`
                        : ''}
                    </p>
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {demand.required_skills.slice(0, 5).map((skill) => (
                        <span
                          key={skill}
                          className="px-1.5 py-0.5 text-[10px] font-mono bg-gray-100 text-gray-600 rounded"
                        >
                          {skill}
                        </span>
                      ))}
                      {demand.required_skills.length > 5 && (
                        <span className="px-1.5 py-0.5 text-[10px] font-mono bg-gray-100 text-gray-500 rounded">
                          +{demand.required_skills.length - 5}
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
                    <RosterStatusBadge status={demand.status} />
                    {demand.needed_by_date && (
                      <p className="text-[10px] text-gray-400 font-mono mt-1">
                        needed {demand.needed_by_date}
                      </p>
                    )}
                  </td>
                  <td className="tabnum font-mono text-gray-800 whitespace-nowrap">
                    {demand.hours_per_day.toFixed(1)}h
                  </td>
                  <td>
                    <span className="text-sm font-semibold tabnum">
                      {demand.headcount_filled}
                    </span>
                    <span className="text-xs text-gray-400"> / {demand.headcount_required}</span>
                    {openSeats > 0 && (
                      <p className="text-[10px] text-yellow-700 font-mono mt-0.5">
                        {openSeats} open
                      </p>
                    )}
                  </td>
                  <td className="min-w-[260px]">
                    {demand.assignments.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {demand.assignments.map((assignment) => (
                          <AssignmentCard
                            key={assignment.allocation_id}
                            assignment={assignment}
                          />
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-gray-400">No one assigned yet.</p>
                    )}
                  </td>
                  <td>
                    <div className="flex flex-col items-end gap-1.5 min-w-[150px]">
                      <button
                        onClick={() => onAssign(demand)}
                        className="btn-primary text-[11px] px-2.5 py-1 w-full"
                      >
                        Assign Talent
                      </button>
                      <button
                        onClick={() => onRecommendations(demand)}
                        className="btn-ghost text-[11px] px-2.5 py-1 w-full"
                      >
                        AI Recommendations
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {roster.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center text-gray-400 text-sm py-10">
                  No roles have been configured for this project yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

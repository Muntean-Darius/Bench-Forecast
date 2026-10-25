import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { Candidate, RosterDemand } from '../../types'
import CapacityBar from '../shared/CapacityBar'
import { CloseIcon, SpinnerIcon } from '../icons/Icons'

interface ConflictDetail {
  message?: string
  remaining_available_hours?: number
  daily_capacity_hours?: number
  requested_hours_per_day?: number
  committed_hours_per_day?: number
  reserved_hours_per_day?: number
}

export default function AssignTalentDrawer({
  projectId,
  demand,
  open,
  onClose,
  onAssigned,
}: {
  projectId: string
  demand: RosterDemand | null
  open: boolean
  onClose: () => void
  onAssigned: () => void
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hours, setHours] = useState<number>(8)
  const [selected, setSelected] = useState<Candidate | null>(null)
  const [assigning, setAssigning] = useState(false)
  const [conflict, setConflict] = useState<ConflictDetail | null>(null)

  useEffect(() => {
    if (!open || !demand) return
    setHours(demand.hours_per_day)
    setSelected(null)
    setConflict(null)
    setError(null)
    setLoading(true)
    api
      .getTeamCandidates(demand.demand_id, demand.hours_per_day)
      .then((rows) => setCandidates(rows))
      .catch((err) => setError(err?.message || 'Failed to load candidates'))
      .finally(() => setLoading(false))
  }, [open, demand])

  if (!open || !demand) return null
  const currentDemand = demand

  async function assign() {
    if (!selected) return
    if (hours <= 0 || hours > 8) {
      setError('Hours must be between 0 and 8 per day.')
      return
    }
    if (hours > selected.remaining_available_hours + 0.001) {
      setError(
        `This candidate only has ${selected.remaining_available_hours.toFixed(1)}h/day free.`,
      )
      return
    }
    setAssigning(true)
    setError(null)
    setConflict(null)
    try {
      await api.assignToDemand(projectId, currentDemand.demand_id, {
        employee_id: selected.employee_id,
        allocated_hours_per_day: hours,
        ai_justification: 'Manual assignment from the project detail drawer.',
      })
      onAssigned()
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConflict(
          typeof err.detail === 'object' ? (err.detail as ConflictDetail) : { message: String(err.detail) },
        )
      } else {
        setError(err instanceof Error ? err.message : 'Assignment failed')
      }
    } finally {
      setAssigning(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50">
      <button
        aria-label="Close assign talent drawer"
        className="absolute inset-0 bg-black/20"
        onClick={onClose}
      />
      <aside className="absolute right-0 top-0 h-full w-full max-w-xl bg-gray-50 border-l border-gray-200 shadow-xl flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-4 bg-white border-b border-gray-200">
          <div>
            <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
              Assign Talent
            </p>
            <h2 className="text-base font-semibold text-gray-900 mt-0.5">{demand.role}</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {demand.hours_per_day.toFixed(1)}h/day · {demand.headcount_needed} open seat
              {demand.headcount_needed === 1 ? '' : 's'}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 p-1">
            <CloseIcon className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-3 bg-white border-b border-gray-200 flex items-end gap-3">
          <div>
            <label className="block text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1">
              Allocate hours/day
            </label>
            <input
              type="number"
              min={0.5}
              max={Math.min(8, demand.hours_per_day)}
              step={0.5}
              value={hours}
              onChange={(event) => setHours(Number(event.target.value))}
              className="w-24 px-2 py-1.5 text-sm border border-gray-200 bg-gray-50 tabnum focus:outline-none focus:ring-1 focus:ring-gray-400"
            />
          </div>
          <p className="text-[11px] text-gray-400 font-mono pb-1.5">
            Hard cap: 8h/day across all projects
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-10 justify-center">
              <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading candidates…
            </div>
          )}
          {error && (
            <div className="border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2">
              {error}
            </div>
          )}
          {conflict && (
            <div className="border border-red-300 bg-red-50 text-red-800 text-xs p-3">
              <p className="font-semibold">Capacity conflict</p>
              <p className="mt-1 leading-relaxed">
                {conflict.message || 'The requested hours exceed this employee’s remaining capacity.'}
              </p>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px]">
                {conflict.requested_hours_per_day !== undefined && (
                  <span>Requested: {Number(conflict.requested_hours_per_day).toFixed(1)}h</span>
                )}
                {conflict.remaining_available_hours !== undefined && (
                  <span>Remaining: {Number(conflict.remaining_available_hours).toFixed(1)}h</span>
                )}
                {conflict.daily_capacity_hours !== undefined && (
                  <span>Capacity: {Number(conflict.daily_capacity_hours).toFixed(1)}h</span>
                )}
                {conflict.committed_hours_per_day !== undefined && (
                  <span>Committed: {Number(conflict.committed_hours_per_day).toFixed(1)}h</span>
                )}
              </div>
            </div>
          )}

          {!loading &&
            candidates.map((candidate) => {
              const committed = Math.max(
                0,
                candidate.daily_capacity_hours - candidate.remaining_available_hours,
              )
              const active = selected?.employee_id === candidate.employee_id
              const disabled =
                candidate.remaining_available_hours + 0.001 < Number(demand.hours_per_day)
              return (
                <button
                  key={candidate.employee_id}
                  disabled={disabled}
                  onClick={() => {
                    setSelected(candidate)
                    setConflict(null)
                    setError(null)
                    setHours(
                      Math.min(
                        demand.hours_per_day,
                        candidate.remaining_available_hours,
                      ),
                    )
                  }}
                  className={`text-left bg-white border p-3 transition-colors ${
                    active
                      ? 'border-gray-900 ring-1 ring-gray-900'
                      : disabled
                      ? 'border-gray-100 opacity-50 cursor-not-allowed'
                      : 'border-gray-200 hover:border-gray-400'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-gray-900 truncate">
                        {candidate.employee_name}
                      </p>
                      <p className="text-[11px] text-gray-500 truncate">
                        {candidate.job_title}
                      </p>
                    </div>
                    <span
                      className={`badge shrink-0 ${
                        candidate.match_score >= 0.8
                          ? 'badge-green'
                          : candidate.match_score >= 0.6
                          ? 'badge-yellow'
                          : 'badge-gray'
                      }`}
                    >
                      {Math.round(candidate.match_score * 100)}% match
                    </span>
                  </div>

                  <div className="mt-2.5">
                    <CapacityBar
                      allocated={committed}
                      capacity={candidate.daily_capacity_hours}
                      remaining={candidate.remaining_available_hours}
                    />
                  </div>

                  <div className="flex flex-wrap gap-1 mt-2">
                    {candidate.matched_skills.slice(0, 5).map((skill) => (
                      <span
                        key={skill}
                        className="px-1.5 py-0.5 text-[10px] font-mono bg-green-50 text-green-700 rounded"
                      >
                        {skill}
                      </span>
                    ))}
                    {candidate.missing_skills.slice(0, 3).map((skill) => (
                      <span
                        key={skill}
                        className="px-1.5 py-0.5 text-[10px] font-mono bg-gray-100 text-gray-500 rounded"
                      >
                        missing {skill}
                      </span>
                    ))}
                  </div>
                </button>
              )
            })}

          {!loading && candidates.length === 0 && !error && (
            <div className="text-center py-10">
              <p className="text-sm text-gray-500">No eligible candidates.</p>
              <p className="text-xs text-gray-400 mt-1">
                Managers only see direct reports; candidates must also have enough remaining hours.
              </p>
            </div>
          )}
        </div>

        <div className="px-5 py-4 bg-white border-t border-gray-200 flex items-center justify-between gap-3">
          <p className="text-[11px] text-gray-400 font-mono">
            {selected ? selected.employee_name : 'Select a candidate'}
          </p>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="btn-ghost text-xs px-3 py-1.5">
              Cancel
            </button>
            <button
              onClick={assign}
              disabled={!selected || assigning || hours <= 0}
              className="btn-primary text-xs px-4 py-1.5 flex items-center gap-1.5"
            >
              {assigning && <SpinnerIcon className="w-3.5 h-3.5 animate-spin" />}
              Assign {hours.toFixed(1)}h/day
            </button>
          </div>
        </div>
      </aside>
    </div>
  )
}

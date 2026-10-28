import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { Candidate, RosterDemand } from '../../types'
import CapacityBar from '../shared/CapacityBar'
import { CloseIcon, SpinnerIcon, BrainIcon } from '../icons/Icons'

export default function AIMatchRecommendationsPanel({
  projectId,
  demand,
  open,
  onClose,
  onUseInDrawer,
}: {
  projectId: string
  demand: RosterDemand | null
  open: boolean
  onClose: () => void
  onUseInDrawer: (demand: RosterDemand) => void
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !demand) return
    setLoading(true)
    setError(null)
    api
      .getProjectRecommendations(projectId, demand.demand_id)
      .then((rows) => setCandidates(rows))
      .catch((err) => setError(err?.message || 'Failed to load recommendations'))
      .finally(() => setLoading(false))
  }, [open, projectId, demand])

  if (!open || !demand) return null

  return (
    <div className="fixed inset-0 z-50">
      <button
        aria-label="Close AI recommendations"
        className="absolute inset-0 bg-black/20"
        onClick={onClose}
      />
      <aside className="absolute right-0 top-0 h-full w-full max-w-xl bg-gray-50 border-l border-gray-200 shadow-xl flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-4 bg-white border-b border-gray-200">
          <div>
            <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400 flex items-center gap-1.5">
              <BrainIcon className="w-3.5 h-3.5" /> AI Match Recommendations
            </p>
            <h2 className="text-base font-semibold text-gray-900 mt-0.5">{demand.role}</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Ranked by skill coverage, remaining capacity and financial fit
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 p-1">
            <CloseIcon className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-10 justify-center">
              <SpinnerIcon className="w-4 h-4 animate-spin" /> Ranking candidates…
            </div>
          )}
          {error && (
            <div className="border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2">
              {error}
            </div>
          )}
          {!loading &&
            candidates.map((candidate, index) => {
              const committed = Math.max(
                0,
                candidate.daily_capacity_hours - candidate.remaining_available_hours,
              )
              return (
                <div key={candidate.employee_id} className="bg-white border border-gray-200 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-2 min-w-0">
                      <span className="w-6 h-6 rounded-full bg-gray-900 text-white text-[10px] font-mono flex items-center justify-center shrink-0">
                        {index + 1}
                      </span>
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-gray-900 truncate">
                          {candidate.employee_name}
                        </p>
                        <p className="text-[11px] text-gray-500 truncate">
                          {candidate.job_title}
                        </p>
                      </div>
                    </div>
                    <span className="badge badge-green shrink-0">
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
                    {candidate.matched_skills.map((skill) => (
                      <span
                        key={skill}
                        className="px-1.5 py-0.5 text-[10px] font-mono bg-green-50 text-green-700 rounded"
                      >
                        {skill}
                      </span>
                    ))}
                    {candidate.missing_skills.map((skill) => (
                      <span
                        key={skill}
                        className="px-1.5 py-0.5 text-[10px] font-mono bg-amber-50 text-amber-700 rounded"
                      >
                        training: {skill}
                      </span>
                    ))}
                  </div>

                  <button
                    onClick={() => {
                      onClose()
                      onUseInDrawer(demand)
                    }}
                    className="btn-ghost text-[11px] px-2.5 py-1 mt-3 w-full"
                  >
                    Open assign drawer
                  </button>
                </div>
              )
            })}
          {!loading && candidates.length === 0 && !error && (
            <p className="text-center text-sm text-gray-500 py-10">
              No eligible candidates found for this role.
            </p>
          )}
        </div>
      </aside>
    </div>
  )
}

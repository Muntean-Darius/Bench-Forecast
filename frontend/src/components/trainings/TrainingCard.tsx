import type { TrainingAssignment } from '../../types'
import { TrainingStatusBadge } from '../shared/Badges'
import { CheckIcon, ClockIcon } from '../icons/Icons'

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function TrainingCard({
  training,
  onComplete,
  busy = false,
}: {
  training: TrainingAssignment
  onComplete?: (training: TrainingAssignment) => void
  busy?: boolean
}) {
  const isDone = training.status === 'completed'
  return (
    <div className="card p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">{training.title}</p>
          <p className="text-[11px] text-gray-500 mt-0.5">
            {training.employee_name || 'Employee'}
            {training.project_name ? ` · ${training.project_name}` : ''}
          </p>
        </div>
        <TrainingStatusBadge status={training.status} />
      </div>

      <div className="flex flex-wrap gap-1">
        {training.targeted_skills.map((skill) => (
          <span key={skill} className="badge badge-blue">
            {skill}
          </span>
        ))}
      </div>

      {training.ai_upskilling_path && (
        <p className="text-xs text-gray-600 leading-relaxed line-clamp-3">
          {training.ai_upskilling_path}
        </p>
      )}

      <div className="flex items-center justify-between gap-2 pt-2 border-t border-gray-100">
        <span className="text-[10px] font-mono text-gray-400 flex items-center gap-1">
          <ClockIcon className="w-3 h-3" />
          Target {formatDate(training.target_completion_date)}
        </span>
        {!isDone && onComplete && (
          <button
            onClick={() => onComplete(training)}
            disabled={busy}
            className="text-[11px] inline-flex items-center gap-1 px-2 py-1 border border-green-200 bg-green-50 text-green-700 hover:bg-green-100"
          >
            <CheckIcon className="w-3 h-3" /> Mark complete
          </button>
        )}
      </div>
    </div>
  )
}

import clsx from 'clsx'
import type { CvStatusState, RosterStatus, TrainingState } from '../../types'

const ROSTER: Record<RosterStatus, { label: string; cls: string }> = {
  open: { label: 'Open', cls: 'badge badge-blue' },
  partially_filled: { label: 'Partially Filled', cls: 'badge badge-yellow' },
  filled: { label: 'Filled', cls: 'badge badge-green' },
  requires_training: { label: 'Requires Training', cls: 'badge badge-red' },
}

export function RosterStatusBadge({ status }: { status: RosterStatus }) {
  const meta = ROSTER[status] ?? ROSTER.open
  return <span className={meta.cls}>{meta.label}</span>
}

export function ProjectStatusBadge({ status }: { status: string }) {
  const value = (status || '').toLowerCase()
  const cls =
    value === 'active'
      ? 'badge badge-green'
      : value === 'pipeline'
      ? 'badge badge-blue'
      : value === 'closed' || value === 'completed'
      ? 'badge badge-gray'
      : 'badge badge-gray'
  const label = value
    ? value.charAt(0).toUpperCase() + value.slice(1)
    : 'Unknown'
  return <span className={cls}>{label}</span>
}

export function CvStatusBadge({ state }: { state: CvStatusState }) {
  if (state === 'pending_approval') {
    return <span className="badge badge-yellow">Pending Approval</span>
  }
  if (state === 'changes_requested') {
    return <span className="badge badge-red">Changes Requested</span>
  }
  return <span className="badge badge-green">Live</span>
}

const TRAINING: Record<TrainingState, { label: string; cls: string }> = {
  assigned: { label: 'Assigned', cls: 'badge badge-blue' },
  in_progress: { label: 'In Progress', cls: 'badge badge-yellow' },
  completed: { label: 'Completed', cls: 'badge badge-green' },
  cancelled: { label: 'Cancelled', cls: 'badge badge-gray' },
}

export function TrainingStatusBadge({ status }: { status: TrainingState }) {
  const meta = TRAINING[status] ?? TRAINING.assigned
  return <span className={meta.cls}>{meta.label}</span>
}

export function LockIcon({ className = 'w-3 h-3' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={clsx(className)}
      aria-hidden="true"
    >
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  )
}

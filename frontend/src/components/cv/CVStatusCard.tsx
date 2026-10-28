import type { CvStatus } from '../../types'
import { CheckIcon, ClockIcon, AlertIcon, DocumentIcon } from '../icons/Icons'

export default function CVStatusCard({
  status,
  downloadUrl,
}: {
  status: CvStatus
  downloadUrl: string
}) {
  const isLive = status.state === 'live'
  const isPending = status.state === 'pending_approval'
  const isChanges = status.state === 'changes_requested'

  return (
    <div
      className={`border p-4 ${
        isLive
          ? 'border-green-200 bg-green-50/40'
          : isPending
          ? 'border-yellow-200 bg-yellow-50/40'
          : 'border-red-200 bg-red-50/40'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <div
            className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${
              isLive
                ? 'bg-green-600 text-white'
                : isPending
                ? 'bg-yellow-500 text-white'
                : 'bg-red-600 text-white'
            }`}
          >
            {isLive ? (
              <CheckIcon className="w-3.5 h-3.5" />
            ) : isPending ? (
              <ClockIcon className="w-3.5 h-3.5" />
            ) : (
              <AlertIcon className="w-3.5 h-3.5" />
            )}
          </div>
          <div>
            <p className="text-sm font-semibold text-gray-900">CV Status: {status.label}</p>
            <p className="text-[11px] text-gray-600 mt-0.5">
              {isLive && 'Your approved CV is live in the talent matching profile.'}
              {isPending &&
                `Submitted${status.manager_name ? ` to ${status.manager_name}` : ''} — your manager has been notified.`}
              {isChanges &&
                'Your manager requested changes. Update the CV and resubmit when ready.'}
            </p>
            {status.version && (
              <p className="text-[10px] font-mono text-gray-400 mt-1">
                Document version v{status.version}
                {status.submitted_at
                  ? ` · submitted ${new Date(status.submitted_at).toLocaleDateString('en-GB')}`
                  : ''}
              </p>
            )}
          </div>
        </div>
        <a
          href={downloadUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="btn-ghost text-[11px] px-2.5 py-1 flex items-center gap-1.5"
        >
          <DocumentIcon className="w-3.5 h-3.5" /> View current CV
        </a>
      </div>

      {status.proposed_skills.length > 0 && (isPending || isChanges) && (
        <div className="mt-3 pt-3 border-t border-black/5">
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-500 mb-1.5">
            Proposed skills
          </p>
          <div className="flex flex-wrap gap-1">
            {status.proposed_skills.map((skill) => (
              <span key={skill} className="badge badge-blue">
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {isChanges && status.manager_comment && (
        <div className="mt-3 border border-red-200 bg-white/70 p-3">
          <p className="text-[10px] font-mono uppercase tracking-wider text-red-500 mb-1">
            Manager feedback
          </p>
          <p className="text-xs text-gray-700 leading-relaxed">{status.manager_comment}</p>
        </div>
      )}
    </div>
  )
}

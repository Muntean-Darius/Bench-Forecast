import { useState } from 'react'
import type { CvDiff } from '../../types'
import { CloseIcon, DocumentIcon, SpinnerIcon, CheckIcon, AlertIcon } from '../icons/Icons'
import { LockIcon } from '../shared/Badges'

export default function CVDiffView({
  diff,
  busy,
  error,
  onClose,
  onApprove,
  onReject,
}: {
  diff: CvDiff
  busy: boolean
  error?: string | null
  onClose: () => void
  onApprove: (comment: string) => void
  onReject: (comment: string) => void
}) {
  const [comment, setComment] = useState('')
  const canReject = comment.trim().length > 0 && !busy
  const projectKeys = new Set(
    diff.project_earned_skills.map((skill) => skill.toLowerCase()),
  )
  const retainedNonProject = diff.retained_skills.filter(
    (skill) => !projectKeys.has(skill.toLowerCase()),
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        aria-label="Close CV diff"
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
      />
      <div className="relative bg-white border border-gray-200 shadow-2xl w-full max-w-5xl max-h-[92vh] flex flex-col">
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-gray-200">
          <div>
            <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
              CV Approval · v{diff.document.version}
            </p>
            <h2 className="text-lg font-semibold text-gray-900 mt-0.5">
              {diff.employee_name}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">{diff.document.file_name}</p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={diff.document.download_url || `/api/v1/documents/employee/${diff.employee_id}/download`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-primary text-xs px-3 py-1.5 flex items-center gap-1.5"
            >
              <DocumentIcon className="w-3.5 h-3.5" /> View Original PDF
            </a>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-700 p-1">
              <CloseIcon className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-5">
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
              New skills detected
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {diff.new_skills.length > 0 ? (
                diff.new_skills.map((skill) => (
                  <span key={skill} className="badge badge-green">
                    <CheckIcon className="w-3 h-3" /> {skill}
                  </span>
                ))
              ) : (
                <span className="text-xs text-gray-400">No new skills in this CV.</span>
              )}
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="border border-gray-200">
              <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 flex items-center gap-1.5">
                <LockIcon className="w-3.5 h-3.5 text-gray-500" />
                <p className="text-xs font-semibold text-gray-700">
                  Retained / project-earned skills
                </p>
              </div>
              <div className="p-3 flex flex-wrap gap-1.5">
                {retainedNonProject.map((skill) => (
                  <span key={skill} className="badge badge-gray">
                    {skill}
                  </span>
                ))}
                {diff.project_earned_skills.map((skill) => (
                  <span key={`project-${skill}`} className="badge badge-blue">
                    <LockIcon className="w-3 h-3" /> {skill}
                  </span>
                ))}
                {retainedNonProject.length === 0 &&
                  diff.project_earned_skills.length === 0 && (
                    <span className="text-xs text-gray-400">No retained skills.</span>
                  )}
              </div>
            </div>

            <div className="border border-gray-200">
              <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
                <p className="text-xs font-semibold text-gray-700">Proposed skills</p>
              </div>
              <div className="p-3 flex flex-wrap gap-1.5">
                {diff.proposed_skills.map((skill) => (
                  <span key={skill} className="badge badge-yellow">
                    {skill}
                  </span>
                ))}
              </div>
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
                Summary before
              </p>
              <pre className="text-xs leading-relaxed whitespace-pre-wrap bg-gray-50 border border-gray-200 p-3 max-h-56 overflow-y-auto font-sans text-gray-700">
                {diff.summary_before || 'No active profile summary.'}
              </pre>
            </div>
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
                Summary after
              </p>
              <pre className="text-xs leading-relaxed whitespace-pre-wrap bg-blue-50/40 border border-blue-100 p-3 max-h-56 overflow-y-auto font-sans text-gray-700">
                {diff.summary_after || 'No proposed summary.'}
              </pre>
            </div>
          </section>

          <section>
            <label className="block text-xs font-semibold text-gray-700 mb-1.5">
              Manager comment <span className="text-gray-400 font-normal">(required for rejection)</span>
            </label>
            <textarea
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              rows={3}
              placeholder="Add feedback for the employee…"
              className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400 resize-y"
            />
          </section>

          {error && (
            <div className="border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2 flex items-center gap-1.5">
              <AlertIcon className="w-3.5 h-3.5" /> {error}
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-between gap-3">
          <p className="text-[10px] text-gray-400 font-mono">
            Approval updates the active profile and ChromaDB embeddings.
          </p>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="btn-ghost text-xs px-3 py-1.5" disabled={busy}>
              Cancel
            </button>
            <button
              onClick={() => onReject(comment.trim())}
              disabled={!canReject}
              className="btn-danger text-xs px-3 py-1.5 flex items-center gap-1.5"
            >
              {busy && <SpinnerIcon className="w-3.5 h-3.5 animate-spin" />}
              Reject
            </button>
            <button
              onClick={() => onApprove(comment.trim())}
              disabled={busy}
              className="btn-success text-xs px-3 py-1.5 flex items-center gap-1.5"
            >
              {busy && <SpinnerIcon className="w-3.5 h-3.5 animate-spin" />}
              Approve &amp; Update Profile
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CvDiff, CvReviewSummary } from '../types'
import CVDiffView from '../components/cv/CVDiffView'
import { AlertIcon, ClipboardIcon, SpinnerIcon } from '../components/icons/Icons'

function formatTimestamp(value: string): string {
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleString('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
}

export default function CVApprovals() {
  const [reviews, setReviews] = useState<CvReviewSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [diff, setDiff] = useState<CvDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await api.getCvReviews('pending_approval')
      setReviews(rows)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load CV reviews')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function openReview(reviewId: string) {
    setSelectedId(reviewId)
    setDiff(null)
    setDiffLoading(true)
    setActionError(null)
    try {
      const detail = await api.getCvReview(reviewId)
      setDiff(detail)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to load CV diff')
    } finally {
      setDiffLoading(false)
    }
  }

  function refreshBadge() {
    window.dispatchEvent(new Event('cv-reviews-changed'))
  }

  async function approve(comment: string) {
    if (!selectedId) return
    setBusy(true)
    setActionError(null)
    try {
      await api.approveCvReview(selectedId, comment || undefined)
      setSelectedId(null)
      setDiff(null)
      await load()
      refreshBadge()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Approval failed')
    } finally {
      setBusy(false)
    }
  }

  async function reject(comment: string) {
    if (!selectedId) return
    setBusy(true)
    setActionError(null)
    try {
      await api.rejectCvReview(selectedId, comment)
      setSelectedId(null)
      setDiff(null)
      await load()
      refreshBadge()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Rejection failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">CV Approvals</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Staged employee CV updates awaiting manager review
          </p>
        </div>
        <span className="badge badge-yellow">{reviews.length} pending</span>
      </div>

      {actionError && !diff && (
        <div className="border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2 flex items-center gap-1.5">
          <AlertIcon className="w-3.5 h-3.5" /> {actionError}
        </div>
      )}

      <div className="card">
        <div className="section-header">
          <div className="flex items-center gap-2">
            <ClipboardIcon className="w-4 h-4 text-gray-400" />
            <span className="section-title">Pending reviews</span>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
            <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading reviews…
          </div>
        ) : error ? (
          <div className="m-4 border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2 flex items-center gap-1.5">
            <AlertIcon className="w-3.5 h-3.5" /> {error}
          </div>
        ) : reviews.length === 0 ? (
          <div className="text-center py-14">
            <p className="text-sm text-gray-500">No CV reviews are waiting for approval.</p>
            <p className="text-xs text-gray-400 mt-1">
              Employee uploads will appear here with their proposed skill changes.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-gray-100">
            {reviews.map((review) => (
              <button
                key={review.review_id}
                onClick={() => openReview(review.review_id)}
                className="w-full text-left px-4 py-3.5 hover:bg-gray-50 transition-colors"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">
                      {review.employee_name}
                    </p>
                    <p className="text-[11px] text-gray-500 font-mono mt-0.5">
                      Submitted {formatTimestamp(review.requested_at)}
                      {review.document ? ` · v${review.document.version}` : ''}
                      {review.manager_name ? ` · manager ${review.manager_name}` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="badge badge-yellow">
                      {review.proposed_skills.length} proposed skills
                    </span>
                    <span className="text-xs font-medium text-gray-700">Review →</span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1 mt-2">
                  {review.proposed_skills.slice(0, 8).map((skill) => (
                    <span key={skill} className="badge badge-gray">
                      {skill}
                    </span>
                  ))}
                  {review.proposed_skills.length > 8 && (
                    <span className="badge badge-gray">
                      +{review.proposed_skills.length - 8}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {diffLoading && (
        <div className="fixed inset-0 z-40 bg-black/20 flex items-center justify-center">
          <div className="bg-white border border-gray-200 px-4 py-3 text-sm text-gray-600 flex items-center gap-2">
            <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading CV diff…
          </div>
        </div>
      )}

      {diff && (
        <CVDiffView
          diff={diff}
          busy={busy}
          error={actionError}
          onClose={() => {
            setSelectedId(null)
            setDiff(null)
            setActionError(null)
          }}
          onApprove={approve}
          onReject={reject}
        />
      )}
    </div>
  )
}

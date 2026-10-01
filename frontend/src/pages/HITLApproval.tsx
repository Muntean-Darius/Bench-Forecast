import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { MOCK_PROPOSALS } from '../data/mockData'
import { categoryBadge, marginColor } from '../types'
import { format } from 'date-fns'

// ─── Margin delta block ───────────────────────────────────────────────────────

function MarginBlock({
  billRate,
  costRate,
  margin,
  targetMargin,
}: {
  billRate: number
  costRate: number
  margin: number
  targetMargin: number
}) {
  const delta = margin - targetMargin
  const isHealthy = delta >= 0

  return (
    <div className="card p-6">
      <p className="text-xs font-mono uppercase tracking-wider text-gray-400 mb-4">
        Financial Projection
      </p>
      <div className="grid grid-cols-3 gap-4 mb-5">
        <div className="text-center">
          <p className="text-[10px] font-mono text-gray-400 uppercase mb-1">Bill Rate</p>
          <p className="text-xl font-semibold tabnum text-green-700">€{billRate}/h</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] font-mono text-gray-400 uppercase mb-1">Cost Rate</p>
          <p className="text-xl font-semibold tabnum text-red-600">€{costRate}/h</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] font-mono text-gray-400 uppercase mb-1">Margin / hr</p>
          <p className="text-xl font-semibold tabnum text-gray-900">€{(billRate - costRate).toFixed(2)}/h</p>
        </div>
      </div>

      {/* Projected margin vs target */}
      <div className={clsx(
        'rounded p-4 flex items-center justify-between',
        isHealthy ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200',
      )}>
        <div>
          <p className="text-xs font-mono text-gray-500">Projected Margin</p>
          <p className={clsx('text-3xl font-bold tabnum mt-0.5', marginColor(margin))}>
            {margin.toFixed(2)}%
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs font-mono text-gray-500">vs Target ({targetMargin}%)</p>
          <p className={clsx('text-2xl font-bold tabnum mt-0.5', isHealthy ? 'text-green-700' : 'text-red-600')}>
            {isHealthy ? '+' : ''}{delta.toFixed(2)}%
          </p>
          <p className={clsx('text-xs font-mono mt-1', isHealthy ? 'text-green-600' : 'text-red-500')}>
            {isHealthy ? '✓ Above target' : '✗ Below target margin'}
          </p>
        </div>
      </div>
    </div>
  )
}

// ─── HITL Approval page ───────────────────────────────────────────────────────

import { useLocation } from 'react-router-dom'
import { api } from '../api/client'

export default function HITLApproval() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { state } = useLocation()
  
  const [decision, setDecision] = useState<'idle' | 'approved' | 'training' | 'rejected'>('idle')
  const [feedback, setFeedback] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Use the proposal from router state if available, else mock data fallback
  const proposal = state?.proposal || MOCK_PROPOSALS.find((p) => p.id === id)
  const recId = state?.recId || 'mock-rec-id'

  if (!proposal) {
    return (
      <div className="p-6">
        <p className="text-sm text-red-600">Proposal not found: {id}</p>
        <button onClick={() => navigate('/queue')} className="btn-ghost mt-4">← Back to Queue</button>
      </div>
    )
  }

  const { employee: emp, role, project } = proposal
  const { cls, label } = categoryBadge(proposal.category)
  const margin = proposal.projected_margin

  async function handleAction(action: 'approved' | 'training' | 'rejected') {
    setSubmitting(true)
    try {
      await api.executeForecast({
        recommendation_id: recId,
        thread_id: "ui-thread",
        approved: action === 'approved',
        approver_name: "Darius Muntean",
        rejection_feedback: feedback || "No feedback provided"
      })
      setDecision(action)
    } catch (e) {
      console.error(e)
      alert("Failed to submit decision")
    } finally {
      setSubmitting(false)
    }
  }

  if (decision !== 'idle') {
    return (
      <div className="p-6 flex flex-col items-center justify-center gap-6 min-h-[60vh]">
        <div className={clsx('w-16 h-16 rounded-full flex items-center justify-center text-2xl', {
          'bg-green-100': decision === 'approved',
          'bg-yellow-100': decision === 'training',
          'bg-red-100': decision === 'rejected',
        })}>
          {decision === 'approved' ? '✓' : decision === 'training' ? '⚡' : '✗'}
        </div>
        <div className="text-center">
          <p className="text-lg font-semibold text-gray-900">
            {decision === 'approved'  ? 'Allocation Approved'      :
             decision === 'training'  ? 'Upskilling Triggered'     :
                                        'Rejected · Re-running AI' }
          </p>
          <p className="text-sm text-gray-500 mt-1">
            {decision === 'approved'  ? `${emp.full_name} is now allocated to ${project.name}.` :
             decision === 'training'  ? `Training plan created for ${emp.full_name}.` :
                                        'AI will generate a revised allocation proposal.' }
          </p>
        </div>
        <button onClick={() => navigate('/queue')} className="btn-ghost">
          ← Back to Queue
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      {/* Sub-header */}
      <div className="border-b border-gray-200 bg-white px-6 py-3 flex items-center gap-3 sticky top-0 z-10">
        <button
          onClick={() => navigate('/queue')}
          className="btn-ghost text-xs px-2 py-1"
        >
          ← Queue
        </button>
        <span className="text-gray-300 select-none">/</span>
        <span className="text-sm font-medium text-gray-700">Review AI Allocation Proposal</span>
        <span className={cls}>{label}</span>
        {proposal.revision_count && proposal.revision_count > 0 && (
          <span className="badge badge-gray">Revision {proposal.revision_count}</span>
        )}
        <div className="ml-auto text-xs text-gray-400 font-mono">
          {format(new Date(proposal.created_at), 'dd MMM yyyy · HH:mm')}
        </div>
      </div>

      <div className="flex-1 p-6 grid grid-cols-[1fr_1fr] gap-6">
        {/* ── LEFT: Demand (Project Role) ──────────────────────────────────── */}
        <div className="flex flex-col gap-4">
          <div className="card p-5">
            <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-3">
              Demand · Project Role
            </p>
            <h2 className="text-base font-semibold text-gray-900">{role.title}</h2>
            <p className="text-sm text-gray-500 mt-0.5">{project.name}</p>

            <div className="mt-4 grid grid-cols-2 gap-y-3 text-sm">
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Start Date</p>
                <p className="font-mono">{format(new Date(role.start_date), 'dd MMM yyyy')}</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Headcount</p>
                <p className="font-mono">{role.headcount}</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Target Bill Rate</p>
                <p className="font-mono font-semibold text-green-700">€{role.target_bill_rate}/h</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Target Margin</p>
                <p className={clsx('font-mono font-semibold', marginColor(project.target_margin))}>
                  {project.target_margin}%
                </p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Project Status</p>
                <p className="font-mono">{project.status}</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Win Probability</p>
                <p className="font-mono">{project.probability}%</p>
              </div>
            </div>

            <div className="mt-4">
              <p className="text-[10px] font-mono text-gray-400 uppercase mb-2">Required Skills</p>
              <div className="flex flex-wrap gap-1.5">
                {(role.required_skills ?? '').split(',').map((s: string) => (
                  <span key={s} className="badge badge-blue">{s.trim()}</span>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── RIGHT: Supply (Employee) ─────────────────────────────────────── */}
        <div className="flex flex-col gap-4">
          <div className="card p-5">
            <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-3">
              Supply · Proposed Employee
            </p>
            <h2 className="text-base font-semibold text-gray-900">{emp.full_name}</h2>
            <p className="text-sm text-gray-500 mt-0.5">
              {emp.seniority} {emp.primary_role}
            </p>

            <div className="mt-4 grid grid-cols-2 gap-y-3 text-sm">
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Cost Rate</p>
                <p className="font-mono font-semibold text-red-700">€{emp.hourly_cost_rate}/h</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Bench Since</p>
                <p className="font-mono">
                  {emp.bench_start_date ? format(new Date(emp.bench_start_date), 'dd MMM yyyy') : '—'}
                </p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Experience</p>
                <p className="font-mono">{emp.experience_years ?? '—'} yrs</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-gray-400 uppercase">Match Score</p>
                <p className={clsx('font-mono font-bold', proposal.match_score >= 0.85 ? 'text-green-700' : 'text-yellow-600')}>
                  {Math.round(proposal.match_score * 100)}%
                </p>
              </div>
            </div>

            {emp.skills && (
              <div className="mt-4">
                <p className="text-[10px] font-mono text-gray-400 uppercase mb-2">Skills</p>
                <div className="flex flex-wrap gap-1.5">
                  {emp.skills.split(',').map((s: string) => (
                    <span key={s} className="badge badge-gray">{s.trim()}</span>
                  ))}
                </div>
              </div>
            )}

            {proposal.missing_skills.length > 0 && (
              <div className="mt-3">
                <p className="text-[10px] font-mono text-yellow-600 uppercase mb-1">Skill Gaps</p>
                <div className="flex flex-wrap gap-1.5">
                  {proposal.missing_skills.map((s: string) => (
                    <span key={s} className="badge badge-red">{s}</span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* RAG justification */}
          <div className="card p-5">
            <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-3">
              AI · Semantic RAG Justification
            </p>
            <p className="text-sm text-gray-700 leading-relaxed mb-3">{proposal.reasoning}</p>
            {proposal.rag_passages.length > 0 && (
              <div className="border-l-2 border-gray-200 pl-3 space-y-2 mt-3">
                <p className="text-[10px] font-mono text-gray-400 uppercase">Retrieved CV Passages</p>
                {proposal.rag_passages.map((p: string, i: number) => (
                  <p key={i} className="text-xs text-gray-500 italic">&ldquo;{p}&rdquo;</p>
                ))}
              </div>
            )}
            {proposal.training_recommendation && (
              <div className="mt-3 p-3 bg-yellow-50 border border-yellow-200 rounded text-xs text-yellow-800">
                <span className="font-semibold">Training Recommendation: </span>
                {proposal.training_recommendation}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── BOTTOM: Financials + Actions ─────────────────────────────────────── */}
      <div className="border-t border-gray-200 bg-white px-6 py-5">
        <div className="max-w-4xl mx-auto">
          <MarginBlock
            billRate={role.target_bill_rate}
            costRate={emp.hourly_cost_rate}
            margin={margin}
            targetMargin={project.target_margin}
          />

          {/* Feedback (for rejection) */}
          <div className="mt-4">
            <label className="text-xs font-mono text-gray-500 block mb-1">
              Rejection feedback / revision guidance <span className="text-gray-400">(required to trigger re-run)</span>
            </label>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={2}
              placeholder="e.g. Need a more senior candidate. Look for Principal-level with FinTech background."
              className="w-full text-sm border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-gray-400 resize-none placeholder:text-gray-300"
            />
          </div>

          {/* Action buttons */}
          <div className="mt-4 flex items-center gap-3">
            <button
              disabled={submitting}
              onClick={() => handleAction('approved')}
              className="btn-success flex-1"
            >
              ✓ Approve Allocation
            </button>
            <button
              disabled={submitting}
              onClick={() => handleAction('training')}
              className="btn-warning flex-1"
            >
              ⚡ Trigger Upskilling
            </button>
            <button
              disabled={submitting || (proposal.category !== 'hiring' && !feedback.trim())}
              onClick={() => handleAction('rejected')}
              className="btn-danger flex-1"
              title={!feedback.trim() ? 'Add feedback to trigger AI re-run' : ''}
            >
              ✗ Reject &amp; Re-run AI
            </button>
          </div>
          <p className="text-xs text-gray-400 font-mono mt-2 text-center">
            Approvals are written to PostgreSQL · Rejections with feedback trigger LangGraph revision
          </p>
        </div>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { TeamMember, TrainingAssignment } from '../types'
import TrainingCard from '../components/trainings/TrainingCard'
import { AlertIcon, PlusIcon, SpinnerIcon } from '../components/icons/Icons'

const EMPTY_FORM = {
  employeeId: '',
  title: '',
  skills: '',
  path: '',
  targetDate: '',
}

export default function Trainings() {
  const [trainings, setTrainings] = useState<TrainingAssignment[]>([])
  const [team, setTeam] = useState<TeamMember[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('active')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [trainingRows, teamRows] = await Promise.all([
        api.getTrainings(),
        api.getTeam().catch(() => []),
      ])
      setTrainings(trainingRows)
      setTeam(teamRows)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load trainings')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const filtered = useMemo(() => {
    if (statusFilter === 'all') return trainings
    if (statusFilter === 'active') {
      return trainings.filter(
        (training) =>
          training.status === 'assigned' || training.status === 'in_progress',
      )
    }
    return trainings.filter((training) => training.status === statusFilter)
  }, [trainings, statusFilter])

  const summary = useMemo(() => {
    return {
      active: trainings.filter(
        (t) => t.status === 'assigned' || t.status === 'in_progress',
      ).length,
      completed: trainings.filter((t) => t.status === 'completed').length,
      people: new Set(trainings.map((t) => t.employee_id)).size,
    }
  }, [trainings])

  async function createTraining() {
    if (!form.employeeId || !form.title.trim() || !form.skills.trim()) {
      setError('Choose an employee and provide a title and at least one target skill.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await api.createTraining({
        employee_id: form.employeeId,
        title: form.title.trim(),
        targeted_skills: form.skills
          .split(',')
          .map((skill) => skill.trim())
          .filter(Boolean),
        ai_upskilling_path: form.path.trim() || undefined,
        target_completion_date: form.targetDate || undefined,
      })
      setForm(EMPTY_FORM)
      setFormOpen(false)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create training')
    } finally {
      setSaving(false)
    }
  }

  async function completeTraining(training: TrainingAssignment) {
    setBusyId(training.id)
    setError(null)
    try {
      await api.completeTraining(training.id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to complete training')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Trainings &amp; Upskilling</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            Active training paths, targeted skills and completion tracking
          </p>
        </div>
        <button
          onClick={() => setFormOpen((open) => !open)}
          className="btn-primary text-xs px-3 py-1.5 flex items-center gap-1.5"
        >
          <PlusIcon className="w-3.5 h-3.5" />
          {formOpen ? 'Close form' : 'Assign Training'}
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="kpi-card">
          <p className="kpi-label">Active paths</p>
          <p className="kpi-value">{summary.active}</p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">Completed</p>
          <p className="kpi-value text-green-700">{summary.completed}</p>
        </div>
        <div className="kpi-card">
          <p className="kpi-label">People in training</p>
          <p className="kpi-value">{summary.people}</p>
        </div>
      </div>

      {error && (
        <div className="border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2 flex items-center gap-1.5">
          <AlertIcon className="w-3.5 h-3.5" /> {error}
        </div>
      )}

      {formOpen && (
        <div className="card p-4 flex flex-col gap-3">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div>
              <label className="block text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1">
                Employee
              </label>
              <select
                value={form.employeeId}
                onChange={(event) =>
                  setForm((current) => ({ ...current, employeeId: event.target.value }))
                }
                className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
              >
                <option value="">Select employee…</option>
                {team.map((member) => (
                  <option key={member.employee_id} value={member.employee_id}>
                    {member.employee_name} · {member.job_title}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1">
                Target skills (comma separated)
              </label>
              <input
                value={form.skills}
                onChange={(event) =>
                  setForm((current) => ({ ...current, skills: event.target.value }))
                }
                placeholder="Kafka, Docker, Terraform"
                className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
              />
            </div>
            <div>
              <label className="block text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1">
                Path title
              </label>
              <input
                value={form.title}
                onChange={(event) =>
                  setForm((current) => ({ ...current, title: event.target.value }))
                }
                placeholder="Backend upskilling path"
                className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
              />
            </div>
            <div>
              <label className="block text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1">
                Target completion
              </label>
              <input
                type="date"
                value={form.targetDate}
                onChange={(event) =>
                  setForm((current) => ({ ...current, targetDate: event.target.value }))
                }
                className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
              />
            </div>
          </div>
          <div>
            <label className="block text-[10px] font-mono uppercase tracking-wider text-gray-400 mb-1">
              Upskilling path / notes
            </label>
            <textarea
              value={form.path}
              onChange={(event) =>
                setForm((current) => ({ ...current, path: event.target.value }))
              }
              rows={3}
              className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400 resize-y"
            />
          </div>
          <div className="flex justify-end">
            <button
              onClick={createTraining}
              disabled={saving}
              className="btn-primary text-xs px-4 py-1.5 flex items-center gap-1.5"
            >
              {saving && <SpinnerIcon className="w-3.5 h-3.5 animate-spin" />}
              Assign path
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        {[
          ['active', 'Active'],
          ['completed', 'Completed'],
          ['all', 'All'],
        ].map(([value, label]) => (
          <button
            key={value}
            onClick={() => setStatusFilter(value)}
            className={`px-3 py-1.5 text-xs border transition-colors ${
              statusFilter === value
                ? 'bg-gray-900 text-white border-gray-900'
                : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
          <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading trainings…
        </div>
      ) : filtered.length === 0 ? (
        <div className="card text-center py-14">
          <p className="text-sm text-gray-500">No trainings in this view.</p>
          <p className="text-xs text-gray-400 mt-1">
            Assign an upskilling path to close a skill gap.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filtered.map((training) => (
            <TrainingCard
              key={training.id}
              training={training}
              busy={busyId === training.id}
              onComplete={completeTraining}
            />
          ))}
        </div>
      )}
    </div>
  )
}

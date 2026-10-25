import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { api } from '../api/client'
import type {
  CvStatus,
  DocumentMetadata,
  TrainingAssignment,
  TrainingState,
} from '../types'
import CapacityBar from '../components/shared/CapacityBar'
import TrainingCard from '../components/trainings/TrainingCard'
import CVStatusCard from '../components/cv/CVStatusCard'
import {
  CheckIcon,
  CloseIcon,
  UploadIcon,
  SpinnerIcon,
  DocumentIcon,
  BenchEmotion,
  OnProjectEmotion,
} from '../components/icons/Icons'

interface PortalAllocation {
  id: string
  project_name: string
  project_id?: string | null
  role: string
  role_on_project: string
  allocation_pct: number
  allocated_hours_per_day: number
  start_date?: string | null
  end_date?: string | null
  status: string
  raw_status: string
  ai_justification?: string | null
}

interface PortalTraining {
  id: string
  title: string
  targeted_skills: string[]
  ai_upskilling_path?: string | null
  status: TrainingState
  assigned_at?: string | null
  target_completion_date?: string | null
  completed_at?: string | null
  notes?: string | null
}

interface OpenDemand {
  id: string
  role: string
  required_skills: string[]
  project_id: string
  hours_per_day: number
  seniority_level?: string | null
  start_date: string
  win_probability: number
  description?: string
}

interface EmployeePortalData {
  employee: {
    id: string
    name: string
    skills: string[]
    current_project: string | null
    available_from: string
    experience_years: number
    cost_rate: number | null
    profile_text?: string
    employment_type?: string
    daily_capacity_hours: number
    committed_hours_per_day: number
    remaining_available_hours: number
    utilization_pct: number
    manager_name?: string | null
  }
  capacity: {
    daily_capacity_hours: number
    committed_hours_per_day: number
    reserved_hours_per_day: number
    remaining_available_hours: number
    utilization_pct: number
  }
  allocations: PortalAllocation[]
  open_demands: OpenDemand[]
  trainings: PortalTraining[]
  cv_status: CvStatus
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function contractLabel(employmentType?: string, capacity?: number): string {
  if (employmentType === 'part_time') return 'Part-time 4h'
  if (employmentType === 'contractor') return 'Contractor 8h'
  return `Full-time ${capacity && capacity > 0 ? capacity.toFixed(0) : 8}h`
}

// ─── Capacity header ─────────────────────────────────────────────────────────

function CapacityHeader({ data }: { data: EmployeePortalData }) {
  const capacity = data.capacity
  const employee = data.employee
  return (
    <div className="card p-5 flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
            Daily capacity &amp; work schedule
          </p>
          <p className="text-xl font-semibold text-gray-900 mt-1 tabnum">
            Allocated: {capacity.committed_hours_per_day.toFixed(1)}h /{' '}
            {capacity.daily_capacity_hours.toFixed(1)}h daily capacity
            <span className="text-sm font-normal text-gray-500">
              {' '}({capacity.utilization_pct.toFixed(0)}% utilized)
            </span>
          </p>
          <p className="text-xs text-gray-500 mt-1">
            {contractLabel(employee.employment_type, capacity.daily_capacity_hours)}
            <span className="mx-2 text-gray-300">·</span>
            {capacity.remaining_available_hours > 0
              ? `${capacity.remaining_available_hours.toFixed(1)}h bench hours available`
              : 'Fully allocated'}
            {employee.manager_name ? (
              <>
                <span className="mx-2 text-gray-300">·</span>
                Manager: {employee.manager_name}
              </>
            ) : null}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`badge ${
              employee.current_project ? 'badge-green' : 'badge-yellow'
            } flex items-center gap-1.5`}
          >
            {employee.current_project ? (
              <OnProjectEmotion className="w-2 h-2" />
            ) : (
              <BenchEmotion className="w-2 h-2" />
            )}
            {employee.current_project ? 'Allocated' : 'On Bench'}
          </span>
        </div>
      </div>
      <CapacityBar
        allocated={capacity.committed_hours_per_day}
        capacity={capacity.daily_capacity_hours}
        remaining={capacity.remaining_available_hours}
      />
    </div>
  )
}

// ─── CV / Document upload section ─────────────────────────────────────────────

function CVUploadSection({
  employeeId,
  onUploadSuccess,
}: {
  employeeId: string
  onUploadSuccess?: () => void
}) {
  const [documents, setDocuments] = useState<DocumentMetadata[]>([])
  const [loadingDocs, setLoadingDocs] = useState(true)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [uploadSummary, setUploadSummary] = useState<string | null>(null)
  const [uploadSkills, setUploadSkills] = useState<string[]>([])

  useEffect(() => {
    api.getEmployeeDocuments(employeeId)
      .then((docs) => setDocuments(docs))
      .catch(() => setDocuments([]))
      .finally(() => setLoadingDocs(false))
  }, [employeeId])

  async function handleUpload() {
    if (!selectedFile) return
    setUploading(true)
    setUploadMsg('')
    setUploadError('')
    setUploadSummary(null)
    setUploadSkills([])
    try {
      const result = await api.uploadDocument(selectedFile, employeeId, 'cv')
      setUploadMsg(
        'CV uploaded successfully and submitted for manager approval. Your manager has been notified and your profile will update once approved.',
      )
      setUploadSummary(result.summary ?? result.profile_summary ?? null)
      setUploadSkills(Array.isArray(result.extracted_skills) ? result.extracted_skills : [])
      setSelectedFile(null)
      const docs = await api.getEmployeeDocuments(employeeId)
      setDocuments(docs)
      onUploadSuccess?.()
    } catch (e) {
      setUploadError(
        `Upload failed: ${e instanceof Error ? e.message : 'Unknown error'}`,
      )
    } finally {
      setUploading(false)
    }
  }

  const approvalBadge = (status?: string) => {
    if (status === 'approved') return 'badge badge-green'
    if (status === 'rejected') return 'badge badge-red'
    if (status === 'pending_approval') return 'badge badge-yellow'
    return 'badge badge-gray'
  }

  const ingestionBadge = (status: string) => {
    if (status === 'ingested') return 'badge badge-green'
    if (status === 'failed') return 'badge badge-red'
    return 'badge badge-yellow'
  }

  return (
    <div className="bg-white border border-gray-200">
      <div className="section-header">
        <div>
          <h3 className="section-title">CV Upload &amp; Manager Approval</h3>
          <p className="text-[10px] text-gray-400 mt-0.5">
            A new CV is staged first; your live profile and AI embeddings update only after approval.
          </p>
        </div>
      </div>
      <div className="p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".pdf"
            onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
            className="text-sm text-gray-600 file:mr-3 file:py-1 file:px-3 file:text-xs file:border file:border-gray-300 file:bg-gray-50 file:text-gray-700 hover:file:bg-gray-100"
          />
          <button
            onClick={handleUpload}
            disabled={!selectedFile || uploading}
            className="btn-primary text-xs px-4 py-1.5 flex items-center gap-1.5"
          >
            {uploading ? (
              <>
                <SpinnerIcon className="w-3.5 h-3.5 animate-spin" /> Submitting…
              </>
            ) : (
              <>
                <UploadIcon className="w-3.5 h-3.5" /> Upload &amp; Submit
              </>
            )}
          </button>
        </div>
        {uploadMsg && (
          <p className="text-xs text-green-700 leading-relaxed flex items-start gap-1.5">
            <CheckIcon className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {uploadMsg}
          </p>
        )}
        {uploadError && <p className="text-xs text-red-600 font-mono">{uploadError}</p>}
        {uploadSkills.length > 0 && (
          <div className="border border-green-200 bg-green-50 p-3">
            <p className="text-[10px] font-mono text-green-700 uppercase tracking-wider mb-1.5">
              Skills detected in this CV ({uploadSkills.length})
            </p>
            <div className="flex flex-wrap gap-1">
              {uploadSkills.map((skill) => (
                <span key={skill} className="badge badge-green text-[11px]">{skill}</span>
              ))}
            </div>
          </div>
        )}
        {uploadSummary && (
          <div className="border border-blue-200 bg-blue-50 p-3">
            <p className="text-[10px] font-mono text-blue-500 uppercase tracking-wider mb-1">
              AI anonymised CV summary (personal data removed)
            </p>
            <p className="text-xs text-gray-700 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto">
              {uploadSummary}
            </p>
          </div>
        )}
        <p className="text-[10px] text-gray-400 font-mono">
          PDF is extracted and prepared for embedding after manager approval.
        </p>

        {loadingDocs ? (
          <p className="text-xs text-gray-400">Loading documents…</p>
        ) : documents.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Version</th>
                  <th>Approval Status</th>
                  <th>Ingestion</th>
                  <th>Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => (
                  <tr key={d.document_id}>
                    <td className="font-mono text-xs">{d.file_name}</td>
                    <td className="tabnum text-xs">v{d.version}</td>
                    <td>
                      <span className={approvalBadge(d.approval_status)}>
                        {d.approval_status || 'pending_approval'}
                      </span>
                      {d.approval_status === 'rejected' && d.review_comment && (
                        <p className="text-[10px] text-red-600 mt-1 max-w-xs">
                          {d.review_comment}
                        </p>
                      )}
                    </td>
                    <td>
                      <span className={ingestionBadge(d.ingestion_status)}>
                        {d.ingestion_status}
                      </span>
                    </td>
                    <td className="font-mono text-xs text-gray-500">
                      {formatDate(d.uploaded_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-400">No documents uploaded yet.</p>
        )}
      </div>
    </div>
  )
}

// ─── Projects & workload tab ─────────────────────────────────────────────────

function WorkloadView({ data }: { data: EmployeePortalData }) {
  const active = data.allocations.filter(
    (allocation) => String(allocation.raw_status).toLowerCase() !== 'completed',
  )
  const completed = data.allocations.filter(
    (allocation) => String(allocation.raw_status).toLowerCase() === 'completed',
  )

  return (
    <div className="space-y-6">
      <div className="bg-white border border-gray-200">
        <div className="section-header">
          <div>
            <h3 className="section-title">Active Project Allocations</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">
              Fractional assignments across concurrent projects
            </p>
          </div>
          <span className="badge badge-gray">{active.length} active</span>
        </div>
        <div className="p-5">
          {active.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Role</th>
                    <th>Daily hours</th>
                    <th>Allocation</th>
                    <th>Period</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {active.map((allocation) => (
                    <tr key={allocation.id}>
                      <td className="font-medium text-gray-900">
                        {allocation.project_name}
                      </td>
                      <td>{allocation.role || allocation.role_on_project}</td>
                      <td className="tabnum font-mono">
                        {allocation.allocated_hours_per_day.toFixed(1)}h/day
                      </td>
                      <td>
                        <span className="badge badge-blue">
                          {allocation.allocation_pct.toFixed(0)}%
                        </span>
                      </td>
                      <td className="font-mono text-[11px] text-gray-600">
                        {formatDate(allocation.start_date)} →{' '}
                        {allocation.end_date ? formatDate(allocation.end_date) : 'Ongoing'}
                      </td>
                      <td>
                        <span className="badge badge-green">Assigned</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-8">
              <p className="text-sm text-gray-500">No active project allocations.</p>
              <p className="text-xs text-gray-400 mt-1">
                You are currently on bench — awaiting your next assignment.
              </p>
            </div>
          )}
        </div>
      </div>

      {completed.length > 0 && (
        <div className="bg-white border border-gray-200">
          <div className="section-header">
            <h3 className="section-title">Completed Assignments</h3>
          </div>
          <div className="p-5">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Role</th>
                    <th>Daily hours</th>
                    <th>Period</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {completed.map((allocation) => (
                    <tr key={allocation.id}>
                      <td className="font-medium text-gray-900">
                        {allocation.project_name}
                      </td>
                      <td>{allocation.role || allocation.role_on_project}</td>
                      <td className="tabnum font-mono">
                        {allocation.allocated_hours_per_day.toFixed(1)}h/day
                      </td>
                      <td className="font-mono text-[11px] text-gray-600">
                        {formatDate(allocation.start_date)} → {formatDate(allocation.end_date)}
                      </td>
                      <td><span className="badge badge-gray">Completed</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      <div className="bg-white border border-gray-200">
        <div className="section-header">
          <div>
            <h3 className="section-title">Open Opportunities</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">
              Roles you may be matched to by the AI engine
            </p>
          </div>
        </div>
        <div className="divide-y divide-gray-100">
          {data.open_demands.length > 0 ? (
            data.open_demands.slice(0, 6).map((demand) => (
              <div key={demand.id} className="px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-gray-900">{demand.role}</p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {demand.hours_per_day.toFixed(1)}h/day
                      {demand.seniority_level ? ` · ${demand.seniority_level}` : ''}
                    </p>
                  </div>
                  <span
                    className={`badge ${
                      demand.win_probability >= 0.85
                        ? 'badge-green'
                        : demand.win_probability >= 0.7
                        ? 'badge-yellow'
                        : 'badge-gray'
                    }`}
                  >
                    {Math.round((demand.win_probability ?? 0) * 100)}%
                  </span>
                </div>
                {demand.required_skills.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {demand.required_skills.map((skill) => (
                      <span
                        key={skill}
                        className="px-2 py-0.5 text-[10px] font-mono bg-gray-100 text-gray-600 rounded"
                      >
                        {skill}
                      </span>
                    ))}
                  </div>
                )}
                {demand.description && (
                  <p className="text-xs text-gray-400 mt-2 line-clamp-2">
                    {demand.description}
                  </p>
                )}
              </div>
            ))
          ) : (
            <div className="text-center py-6">
              <p className="text-sm text-gray-400">No open opportunities available.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Trainings tab ───────────────────────────────────────────────────────────

function TrainingsView({ data }: { data: EmployeePortalData }) {
  const trainings: TrainingAssignment[] = data.trainings.map((training) => ({
    id: training.id,
    employee_id: data.employee.id,
    employee_name: data.employee.name,
    title: training.title,
    targeted_skills: training.targeted_skills || [],
    ai_upskilling_path: training.ai_upskilling_path,
    status: training.status,
    assigned_at: training.assigned_at || new Date().toISOString(),
    target_completion_date: training.target_completion_date,
    completed_at: training.completed_at,
    notes: training.notes,
  }))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Trainings &amp; Upskilling</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Pathways assigned to close skill gaps and prepare you for new roles.
          </p>
        </div>
        <span className="badge badge-blue">{trainings.length} pathways</span>
      </div>

      {trainings.length === 0 ? (
        <div className="bg-white border border-gray-200 text-center py-14">
          <p className="text-sm text-gray-500">No active training pathways assigned.</p>
          <p className="text-xs text-gray-400 mt-1">
            Your manager can assign an upskilling path when a role requires new skills.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {trainings.map((training) => (
            <TrainingCard key={training.id} training={training} />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Profile & CV tab ────────────────────────────────────────────────────────

function ProfileView({
  data,
  onUploadSuccess,
}: {
  data: EmployeePortalData
  onUploadSuccess?: () => void
}) {
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [pwMsg, setPwMsg] = useState('')
  const [pwMsgType, setPwMsgType] = useState<'success' | 'error'>('success')
  const [changingPw, setChangingPw] = useState(false)

  const changePw = async () => {
    if (!oldPw || !newPw) return
    setChangingPw(true)
    setPwMsg('')
    try {
      await api.changePassword(oldPw, newPw)
      setPwMsg('Password changed successfully!')
      setPwMsgType('success')
      setOldPw('')
      setNewPw('')
    } catch (e) {
      setPwMsg(
        e instanceof Error && e.message.includes('400')
          ? 'Wrong current password.'
          : e instanceof Error
          ? e.message
          : 'Could not change password',
      )
      setPwMsgType('error')
    } finally {
      setChangingPw(false)
    }
  }

  const emp = data.employee
  return (
    <div className="space-y-6">
      <CVStatusCard
        status={data.cv_status}
        downloadUrl={`/api/v1/documents/employee/${emp.id}/download`}
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-0 border border-gray-200 bg-white">
        <div className="p-5 border-b md:border-b-0 md:border-r border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Experience</p>
          <p className="text-2xl font-semibold tabnum tracking-tight mt-1">
            {emp.experience_years}<span className="text-sm text-gray-400 ml-1">years</span>
          </p>
        </div>
        <div className="p-5 border-b md:border-b-0 md:border-r border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Available From</p>
          <p className="text-2xl font-semibold tabnum tracking-tight mt-1">
            {formatDate(emp.available_from)}
          </p>
        </div>
        <div className="p-5">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Work Schedule</p>
          <p className="text-2xl font-semibold tabnum tracking-tight mt-1">
            {data.capacity.daily_capacity_hours.toFixed(0)}h
            <span className="text-sm text-gray-400 ml-1">/ day</span>
          </p>
        </div>
      </div>

      <div className="bg-white border border-gray-200">
        <div className="section-header"><h3 className="section-title">Skills</h3></div>
        <div className="p-5 flex flex-wrap gap-2">
          {emp.skills?.length > 0 ? (
            emp.skills.map((skill) => (
              <span key={skill} className="badge badge-blue font-mono">{skill}</span>
            ))
          ) : (
            <p className="text-sm text-gray-400">No skills listed.</p>
          )}
        </div>
      </div>

      <div className="bg-white border border-gray-200">
        <div className="section-header flex items-center justify-between">
          <div>
            <h3 className="section-title">Current CV</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">Open the CV currently on file as a PDF</p>
          </div>
          <a
            href={`/api/v1/documents/employee/${emp.id}/download`}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-primary text-xs px-4 py-1.5 flex items-center gap-1.5"
          >
            <DocumentIcon className="w-3.5 h-3.5" /> View CV (PDF)
          </a>
        </div>
      </div>

      <CVUploadSection employeeId={emp.id} onUploadSuccess={onUploadSuccess} />

      <div className="bg-white border border-gray-200">
        <div className="section-header"><h3 className="section-title">Change Password</h3></div>
        <div className="p-5 space-y-3 max-w-sm">
          <input
            type="password"
            value={oldPw}
            onChange={(e) => setOldPw(e.target.value)}
            placeholder="Current password"
            className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-900"
          />
          <input
            type="password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            placeholder="New password"
            className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-900"
          />
          <button
            onClick={changePw}
            disabled={!oldPw || !newPw || changingPw}
            className="btn-primary text-xs px-4 py-1.5"
          >
            {changingPw ? 'Changing…' : 'Change Password'}
          </button>
          {pwMsg && (
            <p
              className={`text-xs flex items-center gap-1.5 ${
                pwMsgType === 'success' ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {pwMsgType === 'success' ? (
                <CheckIcon className="w-3.5 h-3.5" />
              ) : (
                <CloseIcon className="w-3.5 h-3.5" />
              )}
              {pwMsg}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Employee Portal page ─────────────────────────────────────────────────────

type PortalTab = 'workload' | 'trainings' | 'profile'

const NAV: { key: PortalTab; label: string; icon: string }[] = [
  {
    key: 'workload',
    label: 'Projects & Workload',
    icon: 'M3 4a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm0 6a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6zm10 0a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z',
  },
  {
    key: 'trainings',
    label: 'Trainings & Upskilling',
    icon: 'M10 2L1 6l9 4 7-3.1V14h2V6L10 2zM5 10.6V14c0 1.7 2.2 3 5 3s5-1.3 5-3v-3.4l-5 2.2-5-2.2z',
  },
  {
    key: 'profile',
    label: 'Profile & CV',
    icon: 'M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z',
  },
]

export default function EmployeePortal() {
  const { user, token, logout } = useAuth()
  const [data, setData] = useState<EmployeePortalData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<PortalTab>('workload')

  useEffect(() => {
    if (!user?.employee_id || !token) {
      setLoading(false)
      return
    }
    api.getEmployeePortal(user.employee_id)
      .then((d) => setData(d as EmployeePortalData))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load portal'))
      .finally(() => setLoading(false))
  }, [user, token])

  const refreshPortal = () => {
    if (user?.employee_id) {
      api.getEmployeePortal(user.employee_id)
        .then((d) => setData(d as EmployeePortalData))
        .catch((e) => console.error('Failed to refresh employee portal:', e))
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-sm text-gray-500">Loading employee portal…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-sm text-red-600">Error: {error}</p>
      </div>
    )
  }

  const initials = user?.full_name?.split(' ').map((n) => n[0]).join('') || '?'

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="fixed inset-y-0 left-0 z-30 w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="flex items-center gap-2.5 px-4 h-14 border-b border-gray-200 shrink-0">
          <div className="w-6 h-6 bg-gray-900 rounded flex items-center justify-center">
            <svg viewBox="0 0 16 16" fill="white" className="w-3.5 h-3.5">
              <path d="M8 1L1 5v6l7 4 7-4V5L8 1zm0 2.18L13.09 6 8 8.82 2.91 6 8 3.18zM3 7.27l4.5 2.57v3.9L3 11.17V7.27zm5.5 6.47v-3.9L13 7.27v3.9l-4.5 2.57z" />
            </svg>
          </div>
          <div>
            <p className="text-xs font-semibold text-gray-900 leading-none">Bench Forecast</p>
            <p className="text-[10px] text-gray-400 font-mono mt-0.5">Employee Portal</p>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
          {NAV.map((item) => (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              className={`nav-item w-full text-left ${tab === item.key ? 'active' : ''}`}
            >
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
                <path d={item.icon} />
              </svg>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-gray-200">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
                <span className="text-xs font-semibold text-gray-600">{initials}</span>
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium text-gray-800 truncate">{user?.full_name}</p>
                <p className="text-[10px] text-gray-400 font-mono">Employee</p>
              </div>
            </div>
            <button onClick={logout} className="text-gray-400 hover:text-gray-600" title="Sign out">
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                <path
                  fillRule="evenodd"
                  d="M3 3a1 1 0 011-1h12a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V3zm9 7a1 1 0 10-2 0v3.586l-1.293-1.293a1 1 0 10-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L12 13.586V10z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 ml-56 overflow-y-auto bg-gray-50 p-6">
        {data ? (
          <div className="flex flex-col gap-6">
            <CapacityHeader data={data} />
            {tab === 'workload' ? (
              <WorkloadView data={data} />
            ) : tab === 'trainings' ? (
              <TrainingsView data={data} />
            ) : (
              <ProfileView data={data} onUploadSuccess={refreshPortal} />
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            No employee data available. Ensure your account has an associated employee record.
          </p>
        )}
      </main>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ProjectDetail as ProjectDetailData, RosterDemand } from '../types'
import ProjectSummaryCard from '../components/projects/ProjectSummaryCard'
import RoleRosterTable from '../components/projects/RoleRosterTable'
import AssignTalentDrawer from '../components/projects/AssignTalentDrawer'
import AIMatchRecommendationsPanel from '../components/projects/AIMatchRecommendationsPanel'
import { AlertIcon, CheckIcon, SpinnerIcon } from '../components/icons/Icons'

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<ProjectDetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [assignDemand, setAssignDemand] = useState<RosterDemand | null>(null)
  const [recommendDemand, setRecommendDemand] = useState<RosterDemand | null>(null)

  const load = useCallback(async () => {
    if (!id) return
    setLoading(true)
    setError(null)
    try {
      const detail = await api.getProject(id)
      setData(detail)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load project')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  function handleAssigned() {
    setNotice('Talent assigned successfully.')
    load()
    window.setTimeout(() => setNotice(null), 3500)
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-gray-500">
        <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading project…
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="p-6 flex flex-col gap-3">
        <div className="border border-red-200 bg-red-50 text-red-700 text-sm px-4 py-3 flex items-center gap-2">
          <AlertIcon className="w-4 h-4" /> {error || 'Project not found.'}
        </div>
        <button onClick={() => navigate('/projects')} className="btn-ghost text-xs w-fit">
          ← Back to projects
        </button>
      </div>
    )
  }

  return (
    <div className="p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <Link to="/projects" className="text-xs font-medium text-gray-500 hover:text-gray-900">
          ← All projects
        </Link>
        <span className="text-[10px] font-mono text-gray-400">
          {data.project.id.slice(0, 8).toUpperCase()}
        </span>
      </div>

      {notice && (
        <div className="border border-green-200 bg-green-50 text-green-700 text-xs px-3 py-2 flex items-center gap-1.5">
          <CheckIcon className="w-3.5 h-3.5" /> {notice}
        </div>
      )}

      <ProjectSummaryCard project={data.project} utilization={data.utilization} />

      <RoleRosterTable
        roster={data.roster}
        onAssign={(demand) => setAssignDemand(demand)}
        onRecommendations={(demand) => setRecommendDemand(demand)}
      />

      <AssignTalentDrawer
        projectId={data.project.id}
        demand={assignDemand}
        open={assignDemand !== null}
        onClose={() => setAssignDemand(null)}
        onAssigned={handleAssigned}
      />

      <AIMatchRecommendationsPanel
        projectId={data.project.id}
        demand={recommendDemand}
        open={recommendDemand !== null}
        onClose={() => setRecommendDemand(null)}
        onUseInDrawer={(demand) => {
          setRecommendDemand(null)
          setAssignDemand(demand)
        }}
      />
    </div>
  )
}

import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'

interface EmployeeData {
  employee: {
    id: string; name: string; skills: string[]; current_project: string | null
    available_from: string; experience_years: number; cost_rate: number | null
    profile_text?: string
  }
  allocations: any[]
  open_demands: any[]
}

function PersonalDetails({ data, token }: { data: EmployeeData; token: string }) {
  const [editing, setEditing] = useState(false)
  const [profileText, setProfileText] = useState(data.employee.profile_text || '')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [pwMsg, setPwMsg] = useState('')

  const saveProfile = async () => {
    setSaving(true); setMsg('')
    const res = await fetch(`/api/v1/employee/profile/${data.employee.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ profile_text: profileText }),
    })
    setMsg(res.ok ? 'Profile saved' : 'Save failed')
    setSaving(false); setEditing(false)
  }

  const changePw = async () => {
    setPwMsg('')
    const res = await fetch('/api/v1/employee/change-password', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
    })
    setPwMsg(res.ok ? 'Password changed!' : 'Wrong current password')
    if (res.ok) { setOldPw(''); setNewPw('') }
  }

  const emp = data.employee
  return (
    <div className="space-y-6">
      {/* KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-0 border border-gray-200 bg-white">
        <div className="p-5 border-b md:border-b-0 md:border-r border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Experience</p>
          <p className="text-2xl font-semibold tabnum tracking-tight mt-1">{emp.experience_years}<span className="text-sm text-gray-400 ml-1">years</span></p>
        </div>
        <div className="p-5 border-b md:border-b-0 md:border-r border-gray-200">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Available From</p>
          <p className="text-2xl font-semibold tabnum tracking-tight mt-1">{emp.available_from ? new Date(emp.available_from).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}</p>
        </div>
        <div className="p-5">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Status</p>
          <p className="mt-1">{emp.current_project
            ? <span className="badge badge-green">● Allocated</span>
            : <span className="badge badge-yellow">● On Bench</span>}
          </p>
        </div>
      </div>

      {/* Skills */}
      <div className="bg-white border border-gray-200">
        <div className="section-header"><h3 className="section-title">Skills</h3></div>
        <div className="p-5 flex flex-wrap gap-2">
          {emp.skills?.map((s, i) => <span key={i} className="badge badge-blue font-mono">{s}</span>) || <p className="text-sm text-gray-400">No skills</p>}
        </div>
      </div>

      {/* CV / Profile Text */}
      <div className="bg-white border border-gray-200">
        <div className="section-header">
          <h3 className="section-title">CV / Profile</h3>
          <button onClick={() => setEditing(!editing)} className="btn-ghost text-xs px-3 py-1">{editing ? 'Cancel' : 'Edit'}</button>
        </div>
        <div className="p-5">
          {editing ? (
            <div className="space-y-3">
              <textarea value={profileText} onChange={e => setProfileText(e.target.value)}
                className="w-full h-40 px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-900" placeholder="Paste your CV or profile text..." />
              <button onClick={saveProfile} disabled={saving} className="btn-primary text-xs px-4 py-1.5">{saving ? 'Saving...' : 'Save Profile'}</button>
            </div>
          ) : (
            <p className="text-sm text-gray-700 whitespace-pre-wrap">{profileText || 'No profile text — click Edit to add your CV content.'}</p>
          )}
          {msg && <p className="text-xs text-green-600 mt-2">{msg}</p>}
        </div>
      </div>

      {/* Change Password */}
      <div className="bg-white border border-gray-200">
        <div className="section-header"><h3 className="section-title">Change Password</h3></div>
        <div className="p-5 space-y-3 max-w-sm">
          <input type="password" value={oldPw} onChange={e => setOldPw(e.target.value)} placeholder="Current password"
            className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-900" />
          <input type="password" value={newPw} onChange={e => setNewPw(e.target.value)} placeholder="New password"
            className="w-full px-3 py-2 text-sm border border-gray-200 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-900" />
          <button onClick={changePw} disabled={!oldPw || !newPw} className="btn-primary text-xs px-4 py-1.5">Change Password</button>
          {pwMsg && <p className={`text-xs ${pwMsg.includes('changed') ? 'text-green-600' : 'text-red-600'}`}>{pwMsg}</p>}
        </div>
      </div>
    </div>
  )
}

function ProjectsView({ data }: { data: EmployeeData }) {
  return (
    <div className="space-y-6">
      {/* Current Project */}
      <div className="bg-white border border-gray-200">
        <div className="section-header"><h3 className="section-title">Current Project</h3></div>
        <div className="p-5">
          {data.employee.current_project ? (
            <div><p className="text-lg font-semibold text-gray-900">{data.employee.current_project}</p>
              <p className="text-xs text-gray-500 mt-1">Active assignment</p></div>
          ) : (
            <div className="text-center py-6"><p className="text-sm text-gray-500">No active project</p>
              <p className="text-xs text-gray-400 mt-1">You are currently on bench — awaiting next allocation</p></div>
          )}
        </div>
      </div>

      {/* Allocation History */}
      <div className="bg-white border border-gray-200">
        <div className="section-header"><h3 className="section-title">Allocation History</h3></div>
        <div className="p-5">
          {data.allocations?.length > 0 ? (
            <table className="data-table">
              <thead><tr>
                <th>Project</th><th>Role</th><th>Status</th><th>Date</th>
              </tr></thead>
              <tbody>{data.allocations.map((a: any, i: number) => (
                <tr key={i}>
                  <td>{a.target_project_id}</td>
                  <td>{a.role}</td>
                  <td><span className="badge badge-green">{a.status}</span></td>
                  <td className="font-mono text-xs">{a.created_at?.split('T')[0] || '—'}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : (
            <div className="text-center py-6"><p className="text-sm text-gray-500">No allocations yet</p>
              <p className="text-xs text-gray-400 mt-1">Next assignment appears here after AI allocation and manager approval</p></div>
          )}
        </div>
      </div>

      {/* Open Opportunities */}
      <div className="bg-white border border-gray-200">
        <div className="section-header">
          <div><h3 className="section-title">Open Opportunities</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">Roles you may be matched to by the AI engine</p></div>
        </div>
        <div className="divide-y divide-gray-100">
          {data.open_demands?.length > 0 ? data.open_demands.slice(0, 6).map((d: any, i: number) => (
            <div key={i} className="px-5 py-4">
              <div className="flex items-start justify-between">
                <div><p className="text-sm font-medium text-gray-900">{d.role}</p>
                  <p className="text-xs text-gray-500 mt-0.5">Project: {d.project_id}</p></div>
                <span className={`badge ${d.win_probability >= 0.85 ? 'badge-green' : d.win_probability >= 0.7 ? 'badge-yellow' : 'badge-gray'}`}>
                  {Math.round(d.win_probability * 100)}%
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {d.required_skills?.map((s: string, j: number) => <span key={j} className="px-2 py-0.5 text-[10px] font-mono bg-gray-100 text-gray-600 rounded">{s}</span>)}
              </div>
            </div>
          )) : <div className="text-center py-6"><p className="text-sm text-gray-400">No open opportunities</p></div>}
        </div>
      </div>
    </div>
  )
}

export default function EmployeePortal() {
  const { user, token, logout } = useAuth()
  const [data, setData] = useState<EmployeeData | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'projects' | 'personal'>('projects')

  useEffect(() => {
    if (!user?.employee_id || !token) return
    fetch(`/api/v1/employee/portal/${user.employee_id}`, {
      headers: { Authorization: `Bearer ${token}` },
    }).then(r => r.json()).then(setData).catch(() => {}).finally(() => setLoading(false))
  }, [user, token])

  if (loading) return <div className="min-h-screen bg-gray-50 flex items-center justify-center"><p className="text-sm text-gray-500">Loading...</p></div>

  const initials = user?.full_name?.split(' ').map(n => n[0]).join('') || '?'

  const NAV = [
    { key: 'projects' as const, label: 'Projects', icon: 'M3 4a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm0 6a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6zm10 0a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z' },
    { key: 'personal' as const, label: 'Personal Details', icon: 'M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z' },
  ]

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="flex items-center gap-2.5 px-4 h-14 border-b border-gray-200 shrink-0">
          <div className="w-6 h-6 bg-gray-900 rounded flex items-center justify-center">
            <svg viewBox="0 0 16 16" fill="white" className="w-3.5 h-3.5"><path d="M8 1L1 5v6l7 4 7-4V5L8 1zm0 2.18L13.09 6 8 8.82 2.91 6 8 3.18zM3 7.27l4.5 2.57v3.9L3 11.17V7.27zm5.5 6.47v-3.9L13 7.27v3.9l-4.5 2.57z"/></svg>
          </div>
          <div><p className="text-xs font-semibold text-gray-900 leading-none">Bench Forecast</p>
            <p className="text-[10px] text-gray-400 font-mono mt-0.5">Employee Portal</p></div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
          {NAV.map(n => (
            <button key={n.key} onClick={() => setTab(n.key)}
              className={`nav-item w-full text-left ${tab === n.key ? 'active' : ''}`}>
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0"><path d={n.icon}/></svg>
              {n.label}
            </button>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-gray-200">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
                <span className="text-xs font-semibold text-gray-600">{initials}</span>
              </div>
              <div className="min-w-0"><p className="text-xs font-medium text-gray-800 truncate">{user?.full_name}</p>
                <p className="text-[10px] text-gray-400 font-mono">Employee</p></div>
            </div>
            <button onClick={logout} className="text-gray-400 hover:text-gray-600" title="Sign out">
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M3 3a1 1 0 011-1h12a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V3zm9 7a1 1 0 10-2 0v3.586l-1.293-1.293a1 1 0 10-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L12 13.586V10z" clipRule="evenodd"/></svg>
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 ml-56 overflow-y-auto bg-gray-50 p-6">
        {data ? (
          tab === 'personal' ? <PersonalDetails data={data} token={token!} /> : <ProjectsView data={data} />
        ) : <p className="text-sm text-gray-500">No data available</p>}
      </main>
    </div>
  )
}

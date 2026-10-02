import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Login from './pages/Login'
import EmployeePortal from './pages/EmployeePortal'
import Dashboard from './pages/Dashboard'
import BenchPipeline from './pages/BenchPipeline'
import ActionQueue from './pages/ActionQueue'
import HITLApproval from './pages/HITLApproval'
import Settings from './pages/Settings'

// ─── Icons (inline SVG, no external dep) ─────────────────────────────────────

function Icon({ d, className }: { d: string; className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="currentColor"
      className={clsx('w-4 h-4 shrink-0', className)}
    >
      <path d={d} />
    </svg>
  )
}

const ICONS = {
  dashboard: 'M3 4a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm0 6a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6zm10 0a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z',
  bench: 'M9 6a3 3 0 11-6 0 3 3 0 016 0zm8 0a3 3 0 11-6 0 3 3 0 016 0zM5 14a5 5 0 0110 0v1H5v-1z',
  queue: 'M9 2a1 1 0 000 2h2a1 1 0 100-2H9z M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z',
  settings: 'M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947z M10 13a3 3 0 100-6 3 3 0 000 6z',
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { to: '/',        label: 'Dashboard',   icon: ICONS.dashboard },
  { to: '/bench',   label: 'Bench & Pipeline', icon: ICONS.bench },
  { to: '/queue',   label: 'AI Action Queue',  icon: ICONS.queue },
  { to: '/settings',label: 'Settings',    icon: ICONS.settings },
]

function Sidebar() {
  const { pathname } = useLocation()
  const { user, logout } = useAuth()

  const initials = user?.full_name?.split(' ').map(n => n[0]).join('') || '??'

  return (
    <aside className="fixed inset-y-0 left-0 z-30 w-56 bg-white border-r border-gray-200 flex flex-col">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 h-14 border-b border-gray-200 shrink-0">
        <div className="w-6 h-6 bg-gray-900 rounded flex items-center justify-center">
          <svg viewBox="0 0 16 16" fill="white" className="w-3.5 h-3.5">
            <path d="M8 1L1 5v6l7 4 7-4V5L8 1zm0 2.18L13.09 6 8 8.82 2.91 6 8 3.18zM3 7.27l4.5 2.57v3.9L3 11.17V7.27zm5.5 6.47v-3.9L13 7.27v3.9l-4.5 2.57z"/>
          </svg>
        </div>
        <div>
          <p className="text-xs font-semibold text-gray-900 leading-none">Bench Forecast</p>
          <p className="text-[10px] text-gray-400 font-mono mt-0.5">AI · v2.0</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map(({ to, label, icon }) => {
          const isActive = to === '/' ? pathname === '/' : pathname.startsWith(to)
          return (
            <NavLink
              key={to}
              to={to}
              className={clsx('nav-item', { active: isActive })}
            >
              <Icon d={icon} />
              {label}
            </NavLink>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-gray-200">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
              <span className="text-xs font-semibold text-gray-600">{initials}</span>
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-gray-800 truncate">{user?.full_name}</p>
              <p className="text-[10px] text-gray-400 font-mono capitalize">{user?.role}</p>
            </div>
          </div>
          <button
            onClick={logout}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            title="Sign out"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
              <path fillRule="evenodd" d="M3 3a1 1 0 00-1 1v12a1 1 0 001 1h12a1 1 0 001-1V4a1 1 0 00-1-1H3zm11 4.414l-4.293 4.293a1 1 0 01-1.414-1.414L11.586 7H7a1 1 0 110-2h4.586L8.293 1.707a1 1 0 011.414-1.414L14 4.586V3a1 1 0 112 0v4a1 1 0 01-1 1h-1z" clipRule="evenodd" />
            </svg>
          </button>
        </div>
      </div>
    </aside>
  )
}

// ─── Manager Layout ───────────────────────────────────────────────────────────

function ManagerLayout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 ml-56 overflow-y-auto bg-gray-50">
        <Routes>
          <Route path="/"          element={<Dashboard />} />
          <Route path="/bench"     element={<BenchPipeline />} />
          <Route path="/queue"     element={<ActionQueue />} />
          <Route path="/queue/:id" element={<HITLApproval />} />
          <Route path="/settings"  element={<Settings />} />
        </Routes>
      </main>
    </div>
  )
}

// ─── App shell ────────────────────────────────────────────────────────────────

function AppContent() {
  const { user, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 bg-gray-900 rounded flex items-center justify-center animate-pulse">
            <svg viewBox="0 0 16 16" fill="white" className="w-3.5 h-3.5">
              <path d="M8 1L1 5v6l7 4 7-4V5L8 1zm0 2.18L13.09 6 8 8.82 2.91 6 8 3.18zM3 7.27l4.5 2.57v3.9L3 11.17V7.27zm5.5 6.47v-3.9L13 7.27v3.9l-4.5 2.57z"/>
            </svg>
          </div>
          <p className="text-sm text-gray-500">Loading...</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return <Login />
  }

  if (user.role === 'employee') {
    return <EmployeePortal />
  }

  return <ManagerLayout />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </BrowserRouter>
  )
}

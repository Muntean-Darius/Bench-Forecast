import { useState, useEffect } from 'react'
import { api } from '../api/client'

interface SettingRowProps {
  label: string
  description?: string
  children: React.ReactNode
}

function SettingRow({ label, description, children }: SettingRowProps) {
  return (
    <div className="flex items-start justify-between py-4 border-b border-gray-100 last:border-0 gap-6">
      <div className="flex-1">
        <p className="text-sm font-medium text-gray-800">{label}</p>
        {description && <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{description}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

export default function Settings() {
  const [horizonDays,  setHorizonDays]  = useState(Number(localStorage.getItem('bf_horizon_days') ?? 90))
  const [minWinProb,   setMinWinProb]   = useState(Number(localStorage.getItem('bf_min_win_prob') ?? 0.75))
  const [saved,        setSaved]        = useState(false)

  // Health status
  const [healthStatus,  setHealthStatus]  = useState<'loading' | 'healthy' | 'error'>('loading')
  const [llmProvider,   setLlmProvider]   = useState<string>('—')
  const [apiVersion,    setApiVersion]    = useState<string>('—')
  const [phoenixEnabled, setPhoenixEnabled] = useState<boolean | null>(null)

  useEffect(() => {
    api.health()
      .then((h) => {
        setHealthStatus('healthy')
        setLlmProvider(h.llm_provider ?? '—')
        setApiVersion(h.version ?? '—')
        setPhoenixEnabled(h.phoenix_enabled ?? false)
      })
      .catch(() => setHealthStatus('error'))
  }, [])

  function save(e: React.FormEvent) {
    e.preventDefault()
    localStorage.setItem('bf_horizon_days', String(horizonDays))
    localStorage.setItem('bf_min_win_prob', String(minWinProb))
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  return (
    <div className="p-6 max-w-2xl flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-900">Settings</h1>
        <p className="text-xs text-gray-500 font-mono mt-0.5">Forecast engine and backend configuration</p>
      </div>

      {/* Backend Status */}
      <div className="card p-5">
        <p className="section-title mb-1">Backend Status</p>
        <p className="text-xs text-gray-400 mb-4">Live health check from GET /api/v1/health</p>

        <SettingRow label="API Health">
          {healthStatus === 'loading' ? (
            <span className="badge badge-gray">Checking…</span>
          ) : healthStatus === 'healthy' ? (
            <span className="badge badge-green">● Healthy</span>
          ) : (
            <span className="badge badge-red">● Unreachable</span>
          )}
        </SettingRow>

        <SettingRow label="API Version">
          <span className="text-sm font-mono text-gray-700">{apiVersion}</span>
        </SettingRow>

        <SettingRow label="LLM Provider">
          <span className="badge badge-blue">{llmProvider}</span>
        </SettingRow>

        <SettingRow label="Phoenix Tracing">
          {phoenixEnabled === null ? (
            <span className="badge badge-gray">—</span>
          ) : phoenixEnabled ? (
            <span className="badge badge-green">Enabled</span>
          ) : (
            <span className="badge badge-gray">Disabled</span>
          )}
        </SettingRow>
      </div>

      <form onSubmit={save} className="flex flex-col gap-6">
        {/* Forecast engine */}
        <div className="card p-5">
          <p className="section-title mb-1">Forecast Engine</p>
          <p className="text-xs text-gray-400 mb-4">
            Controls used by Bench &amp; Pipeline and AI Action Queue. Saved to localStorage.
          </p>

          <SettingRow
            label="Forecast Horizon"
            description="Employees finishing projects within this window are included in the next forecast run."
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={30}
                max={365}
                value={horizonDays}
                onChange={(e) => setHorizonDays(Number(e.target.value))}
                className="w-20 text-sm border border-gray-200 px-2 py-1 tabnum text-right focus:outline-none focus:ring-1 focus:ring-gray-400"
              />
              <span className="text-xs text-gray-400">days</span>
            </div>
          </SettingRow>

          <SettingRow
            label="Min Win Probability"
            description="Pipeline deals below this probability are excluded from demand matching."
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={minWinProb}
                onChange={(e) => setMinWinProb(Number(e.target.value))}
                className="w-20 text-sm border border-gray-200 px-2 py-1 tabnum text-right focus:outline-none focus:ring-1 focus:ring-gray-400"
              />
              <span className="text-xs text-gray-400">[0–1]</span>
            </div>
          </SettingRow>
        </div>

        {/* API connection info (read-only) */}
        <div className="card p-5">
          <p className="section-title mb-1">API Connection</p>
          <p className="text-xs text-gray-400 mb-4">Vite proxy forwards /api → http://localhost:8000 in development.</p>

          <SettingRow label="API Base URL">
            <span className="text-sm font-mono text-gray-600">http://localhost:8000</span>
          </SettingRow>
          <SettingRow label="Proxy Path">
            <span className="text-sm font-mono text-gray-600">/api → backend</span>
          </SettingRow>
        </div>

        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary">
            Save Settings
          </button>
          {saved && (
            <span className="text-xs font-mono text-green-600">✓ Saved to localStorage</span>
          )}
        </div>
      </form>
    </div>
  )
}

import { useState } from 'react'

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
  const [horizonDays,  setHorizonDays]  = useState(90)
  const [minWinProb,   setMinWinProb]   = useState(0.75)
  const [apiBase,      setApiBase]      = useState('http://localhost:8000')
  const [llmProvider,  setLlmProvider]  = useState<'groq' | 'ollama'>('groq')
  const [saved,        setSaved]        = useState(false)

  function save(e: React.FormEvent) {
    e.preventDefault()
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="p-6 max-w-2xl flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-900">Settings</h1>
        <p className="text-xs text-gray-500 font-mono mt-0.5">Forecast engine and API configuration</p>
      </div>

      <form onSubmit={save} className="flex flex-col gap-6">
        {/* Forecast engine */}
        <div className="card p-5">
          <p className="section-title mb-1">Forecast Engine</p>
          <p className="text-xs text-gray-400 mb-4">Controls sent to POST /api/v1/forecast/generate</p>

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

        {/* API connection */}
        <div className="card p-5">
          <p className="section-title mb-1">Backend API</p>
          <p className="text-xs text-gray-400 mb-4">FastAPI server URL for live data</p>

          <SettingRow label="API Base URL" description="Vite proxy forwards /api → this address in development.">
            <input
              type="url"
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              className="w-56 text-sm border border-gray-200 px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-gray-400"
            />
          </SettingRow>

          <SettingRow label="LLM Provider" description="Groq (faster, cloud) or Ollama (local, private).">
            <select
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value as 'groq' | 'ollama')}
              className="text-sm border border-gray-200 bg-white px-2 py-1 focus:outline-none focus:ring-1 focus:ring-gray-400"
            >
              <option value="groq">Groq (Cloud)</option>
              <option value="ollama">Ollama (Local)</option>
            </select>
          </SettingRow>
        </div>

        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary">
            Save Settings
          </button>
          {saved && (
            <span className="text-xs font-mono text-green-600">✓ Saved</span>
          )}
        </div>
      </form>
    </div>
  )
}

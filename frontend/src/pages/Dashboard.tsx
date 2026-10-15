import { useEffect, useState } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import clsx from 'clsx'
import { format } from 'date-fns'
import { api } from '../api/client'

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label, value, delta, deltaPositiveGood = true, prefix = '', suffix = '',
}: {
  label: string
  value: number | string
  delta?: number
  deltaPositiveGood?: boolean
  prefix?: string
  suffix?: string
}) {
  const isPositive = (delta ?? 0) >= 0
  const isGood = deltaPositiveGood ? isPositive : !isPositive
  return (
    <div className="kpi-card">
      <p className="kpi-label">{label}</p>
      <p className="kpi-value tabnum">
        {prefix}{typeof value === 'number' ? value.toLocaleString() : value}{suffix}
      </p>
      {delta !== undefined && (
        <p className={clsx('kpi-delta', isGood ? 'text-green-600' : 'text-red-600')}>
          {isPositive ? '▲' : '▼'} {Math.abs(delta)}{suffix} vs last period
        </p>
      )}
    </div>
  )
}

// ─── Bench utilization chart ──────────────────────────────────────────────────

function BenchUtilizationChart({ benchCount }: { benchCount: number }) {
  const chartData = Array.from({ length: 6 }, (_, i) => {
    const allocated = Math.round(benchCount * (i / 5))
    return {
      month: `Wk ${i + 1}`,
      allocated,
      bench: Math.max(benchCount - allocated, 0),
    }
  })

  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Bench Utilization — 6-Week Projection</p>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} barCategoryGap="30%">
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ fontSize: 12, border: '1px solid #e5e7eb', borderRadius: 0 }}
          />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
          <Bar dataKey="allocated" name="Allocated" stackId="a" fill="#6b7280" />
          <Bar dataKey="bench"     name="On Bench"  stackId="a" fill="#ef4444" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ─── Financial health chart ───────────────────────────────────────────────────

function FinancialHealthChart({ benchBurn }: { benchBurn: number | null }) {
  const months = ['Oct', 'Nov', 'Dec']
  const chartData = months.map((month, i) => {
    const cost = benchBurn ? Math.round(benchBurn * (1 + i * 0.05)) : 0
    const revenue = benchBurn ? Math.round(benchBurn * 1.55 * (1 + i * 0.08)) : 0
    return { month, cost, revenue }
  })

  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Financial Health — Cost vs. Projected Revenue</p>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis
            tick={{ fontSize: 11 }} tickLine={false} axisLine={false}
            tickFormatter={(v) => `€${(v / 1000).toFixed(0)}k`}
          />
          <Tooltip
            formatter={(v: number) => `€${v.toLocaleString()}`}
            contentStyle={{ fontSize: 12, border: '1px solid #e5e7eb', borderRadius: 0 }}
          />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
          <Line type="monotone" dataKey="cost"    name="Internal Cost"       stroke="#ef4444" strokeWidth={2} dot={{ r: 4 }} />
          <Line type="monotone" dataKey="revenue" name="Projected Revenue"   stroke="#16a34a" strokeWidth={2} dot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ─── Dashboard page ───────────────────────────────────────────────────────────

export default function Dashboard() {
  const [benchCount, setBenchCount] = useState<number>(0)
  const [openRoles, setOpenRoles] = useState<number>(0)
  const [benchBurn, setBenchBurn] = useState<number | null>(null)
  const [dataLoading, setDataLoading] = useState(true)

  useEffect(() => {
    async function loadData() {
      const now = new Date()
      const year = now.getFullYear()
      const month = now.getMonth() + 1

      try {
        const [empData, demData] = await Promise.all([
          api.getEmployees(90),
          api.getDemands(0.75),
        ])
        const onBench = empData.filter((e: any) => !e.current_project && e.bench_status !== 'on_project')
        setBenchCount(onBench.length)
        setOpenRoles(demData.length)
      } catch (e) {
        console.error('Failed to load supply/demand:', e)
      } finally {
        setDataLoading(false)
      }

      try {
        const burnData = await api.getMonthlyBenchBurn(year, month)
        setBenchBurn(burnData.total_bench_cost ?? burnData.total_bench_burn ?? null)
      } catch {
        setBenchBurn(null)
      }
    }
    loadData()
  }, [])

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Global Dashboard</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            {format(new Date(), 'EEEE, dd MMM yyyy')} · Forecast horizon: 90 days
          </p>
        </div>
        <span className="badge badge-blue">Live data</span>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <KpiCard
          label="Employees on Bench"
          value={dataLoading ? '…' : benchCount}
          deltaPositiveGood={false}
          suffix=" people"
        />
        <KpiCard
          label="Open Project Roles"
          value={dataLoading ? '…' : openRoles}
          deltaPositiveGood={false}
        />
        <KpiCard
          label="Monthly Bench Burn"
          value={benchBurn !== null ? (benchBurn / 1000).toFixed(1) : '—'}
          prefix="€"
          suffix="k"
          deltaPositiveGood={false}
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BenchUtilizationChart benchCount={benchCount} />
        <FinancialHealthChart benchBurn={benchBurn} />
      </div>
    </div>
  )
}

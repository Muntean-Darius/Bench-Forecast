import { useEffect, useState } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, Cell,
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

// ─── Bench utilization chart (real data from bench_history) ────────────────────

interface BenchWeek {
  week_start: string
  label: string
  on_bench: number
  allocated: number
}

function BenchUtilizationChart({ weeks }: { weeks: BenchWeek[] }) {
  const chartData = weeks.map((w) => ({
    label: w.label,
    allocated: w.allocated,
    bench: w.on_bench,
  }))

  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Bench vs. Allocated — Last 8 Weeks</p>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} barCategoryGap="30%">
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
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

// ─── Internal cost chart (real data from bench_costs + employee_costs) ─────────

interface CostMonth {
  year: number
  month: number
  label: string
  total_bench_cost: number
  total_workforce_cost: number
}

function FinancialHealthChart({ months }: { months: CostMonth[] }) {
  const chartData = months.map((m) => ({
    month: m.label,
    workforce: Math.round(m.total_workforce_cost),
    bench: Math.round(m.total_bench_cost),
  }))

  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Internal Cost — Bench Burn vs. Total Workforce (6 months)</p>
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
          <Line type="monotone" dataKey="workforce" name="Total Workforce Cost" stroke="#6b7280" strokeWidth={2} dot={{ r: 4 }} />
          <Line type="monotone" dataKey="bench"     name="Bench Burn"           stroke="#ef4444" strokeWidth={2} dot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ─── Operating KPI trend (real: kpi_snapshots) ─────────────────────────────────

interface KpiDay {
  date: string
  label: string
  bench_ratio_pct: number
  mttr_days: number | null
  human_intervention_pct: number
}

function KpiTrendChart({ data }: { data: KpiDay[] }) {
  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Operating KPIs — Last 30 Days</p>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={28} />
          <YAxis yAxisId="left" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} />
          <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}d`} />
          <Tooltip
            formatter={(v: number, n: string) =>
              n.includes('MTTR') ? [`${Number(v).toFixed(1)} d`, n] : [`${Number(v).toFixed(1)}%`, n]
            }
            contentStyle={{ fontSize: 12, border: '1px solid #e5e7eb', borderRadius: 0 }}
          />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
          <Line yAxisId="left"  type="monotone" dataKey="bench_ratio_pct"        name="Bench Ratio"        stroke="#ef4444" strokeWidth={2} dot={false} />
          <Line yAxisId="left"  type="monotone" dataKey="human_intervention_pct" name="Human Intervention" stroke="#2563eb" strokeWidth={2} dot={false} />
          <Line yAxisId="right" type="monotone" dataKey="mttr_days"              name="MTTR (days)"        stroke="#16a34a" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-gray-400 font-mono">
        Left axis: bench ratio &amp; human intervention (%). Right axis: MTTR (days).
      </p>
    </div>
  )
}

// ─── Gross margin per active project (real: allocations + costs + bill rates) ───

interface ProjectMargin {
  project_id: string
  name: string
  status: string
  allocated_fte: number
  blended_bill_rate: number
  blended_cost_rate: number
  margin_pct: number
  target_margin: number | null
  monthly_revenue: number
  monthly_margin: number
}

function ProjectMarginChart({ projects }: { projects: ProjectMargin[] }) {
  const data = projects.map((p) => ({
    name: p.name.length > 24 ? `${p.name.slice(0, 22)}…` : p.name,
    margin: p.margin_pct,
    target: p.target_margin ?? 0,
  }))
  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Gross Margin by Active Project</p>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} barCategoryGap="22%">
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} interval={0} angle={-15} textAnchor="end" height={62} />
          <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} />
          <Tooltip formatter={(v: number) => `${Number(v).toFixed(1)}%`} contentStyle={{ fontSize: 12, border: '1px solid #e5e7eb', borderRadius: 0 }} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
          <Bar dataKey="margin" name="Gross Margin %" fill="#f59e0b">
            {data.map((d, i) => (
              <Cell key={i} fill={d.target && d.margin >= d.target ? '#16a34a' : '#f59e0b'} />
            ))}
          </Bar>
          <Bar dataKey="target" name="Target %" fill="#e5e7eb" />
        </BarChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-gray-400 font-mono">
        Green = meets/exceeds the project&apos;s target margin · amber = below target
      </p>
    </div>
  )
}

// ─── Dashboard page ───────────────────────────────────────────────────────────

export default function Dashboard() {
  const [benchCount, setBenchCount] = useState<number>(0)
  const [openRoles, setOpenRoles] = useState<number>(0)
  const [benchBurn, setBenchBurn] = useState<number | null>(null)
  const [benchWeeks, setBenchWeeks] = useState<BenchWeek[]>([])
  const [costMonths, setCostMonths] = useState<CostMonth[]>([])
  const [kpiDays, setKpiDays] = useState<KpiDay[]>([])
  const [projectMargins, setProjectMargins] = useState<ProjectMargin[]>([])
  const [dataLoading, setDataLoading] = useState(true)
  const horizonDays = Number(localStorage.getItem('bf_horizon_days') ?? 90)
  const minWinProb = Number(localStorage.getItem('bf_min_win_prob') ?? 0.75)

  useEffect(() => {
    async function loadData() {
      const now = new Date()
      const year = now.getFullYear()
      const month = now.getMonth() + 1

      try {
        const [empData, demData, trend] = await Promise.all([
          api.getEmployees(horizonDays),
          api.getDemands(minWinProb, horizonDays),
          api.getBenchTrend(8),
        ])
        const onBench = empData.filter((e: any) => !e.current_project && e.bench_status !== 'on_project')
        setBenchCount(onBench.length)
        setOpenRoles(demData.length)
        setBenchWeeks(trend.weeks ?? [])
      } catch (e) {
        console.error('Failed to load supply/demand:', e)
      } finally {
        setDataLoading(false)
      }

      try {
        const [burnData, costTrend, kpis, margins] = await Promise.all([
          api.getMonthlyBenchBurn(year, month),
          api.getBenchBurnTrend(6),
          api.getKpiTrend(30),
          api.getProjectMargins(),
        ])
        setBenchBurn(burnData.total_bench_cost ?? burnData.total_bench_burn ?? null)
        setCostMonths(costTrend.months ?? [])
        setKpiDays(kpis.days ?? [])
        setProjectMargins(margins.projects ?? [])
      } catch {
        setBenchBurn(null)
      }
    }
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="p-6 flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Global Dashboard</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            {format(new Date(), 'EEEE, dd MMM yyyy')} · Forecast horizon: {horizonDays} days · Min win probability: {Math.round(minWinProb * 100)}%
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

      {/* Charts row 1 — workforce & cost */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BenchUtilizationChart weeks={benchWeeks} />
        <FinancialHealthChart months={costMonths} />
      </div>

      {/* Charts row 2 — operations & project profitability */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <KpiTrendChart data={kpiDays} />
        <ProjectMarginChart projects={projectMargins} />
      </div>
    </div>
  )
}

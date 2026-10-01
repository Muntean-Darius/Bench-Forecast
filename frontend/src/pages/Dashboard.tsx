import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import clsx from 'clsx'
import {
  MOCK_PROPOSALS,
  BENCH_TREND_DATA, FINANCIAL_HEALTH_DATA,
} from '../data/mockData'
import { marginColor, categoryBadge } from '../types'
import { useNavigate } from 'react-router-dom'
import { format } from 'date-fns'

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

function BenchUtilizationChart() {
  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Bench Utilization — Oct → Dec 2026</p>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={BENCH_TREND_DATA} barCategoryGap="30%">
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

function FinancialHealthChart() {
  return (
    <div className="card p-4 flex flex-col gap-3">
      <p className="section-title">Financial Health — Cost vs. Projected Revenue</p>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={FINANCIAL_HEALTH_DATA}>
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

// ─── Quick actions table ──────────────────────────────────────────────────────

function QuickActions() {
  const navigate = useNavigate()
  const urgent = MOCK_PROPOSALS.slice(0, 3)

  return (
    <div className="card flex flex-col">
      <div className="section-header">
        <p className="section-title">Urgent Action Queue</p>
        <button
          onClick={() => navigate('/queue')}
          className="text-xs font-medium text-blue-600 hover:text-blue-700"
        >
          View all →
        </button>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Employee</th>
            <th>Proposed Role</th>
            <th>Category</th>
            <th className="text-right">Match</th>
            <th className="text-right">Margin</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {urgent.map((p) => {
            const { cls, dot, label } = categoryBadge(p.category)
            return (
              <tr key={p.id}>
                <td className="font-medium">{p.employee.full_name}</td>
                <td className="text-gray-600 max-w-[200px] truncate">{p.role.title}</td>
                <td><span className={cls}>{dot} {label}</span></td>
                <td className="text-right tabnum">{Math.round(p.match_score * 100)}%</td>
                <td className={clsx('text-right tabnum font-mono', marginColor(p.projected_margin))}>
                  {p.projected_margin.toFixed(1)}%
                </td>
                <td className="text-right">
                  <button
                    onClick={() => navigate(`/queue/${p.id}`)}
                    className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                  >
                    Review →
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ─── Dashboard page ───────────────────────────────────────────────────────────
import { useEffect, useState } from 'react'
import { api } from '../api/client'

export default function Dashboard() {
  const [benchCount, setBenchCount] = useState<number>(0)
  const [openRoles, setOpenRoles] = useState<number>(0)

  useEffect(() => {
    async function loadData() {
      try {
        const [empData, demData] = await Promise.all([
          api.getEmployees(90),
          api.getDemands(0.75),
        ])
        setBenchCount(empData.length)
        setOpenRoles(demData.length)
      } catch (e) {
        console.error(e)
      }
    }
    loadData()
  }, [])

  const avgMargin  = MOCK_PROPOSALS
    .filter((p) => p.category !== 'hiring')
    .reduce((sum, p) => sum + p.projected_margin, 0) /
    (MOCK_PROPOSALS.filter((p) => p.category !== 'hiring').length || 1)
  const totalBudget = 4080000

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
      <div className="grid grid-cols-4 gap-4">
        <KpiCard
          label="Employees on Bench"
          value={benchCount}
          delta={-2}
          deltaPositiveGood={false}
          suffix=" people"
        />
        <KpiCard
          label="Open Project Roles"
          value={openRoles}
          delta={3}
          deltaPositiveGood={false}
        />
        <KpiCard
          label="Avg Projected Margin"
          value={avgMargin.toFixed(1)}
          delta={1.8}
          suffix="%"
        />
        <KpiCard
          label="Total Pipeline Budget"
          value={(totalBudget / 1_000_000).toFixed(2)}
          prefix="€"
          suffix="M"
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-2 gap-4">
        <BenchUtilizationChart />
        <FinancialHealthChart />
      </div>

      {/* Quick actions */}
      <QuickActions />
    </div>
  )
}

# Bench Forecast — React Frontend

Production React + TypeScript + Tailwind frontend for the Bench Forecasting AI system.

## Tech Stack

| Layer | Library |
|---|---|
| Framework | React 18 + TypeScript |
| Build | Vite 5 |
| Routing | React Router 6 |
| Styling | Tailwind CSS 3 |
| Data fetching | TanStack React Query 5 |
| Charts | Recharts 2 |

## Project Structure

```
frontend/
├── src/
│   ├── api/
│   │   └── client.ts          # fetch wrapper → FastAPI /api/v1/*
│   ├── data/
│   │   └── mockData.ts        # Seeded from bench_forecast/data/mock_data.json
│   ├── pages/
│   │   ├── Dashboard.tsx      # Page A — KPIs + charts + quick actions
│   │   ├── BenchPipeline.tsx  # Page B — Bench (supply) + Projects (demand) tables
│   │   ├── ActionQueue.tsx    # Page C — AI Action Queue (HITL list)
│   │   ├── HITLApproval.tsx   # Page D — Full-page HITL Approval view
│   │   └── Settings.tsx       # Forecast engine + API config
│   ├── types/
│   │   └── index.ts           # TypeScript types mirroring SQLAlchemy models
│   ├── App.tsx                # Shell: fixed sidebar + React Router routes
│   ├── main.tsx               # Entry point + QueryClientProvider
│   └── index.css              # Tailwind + component classes
├── tailwind.config.ts
├── vite.config.ts             # Dev server proxies /api → http://localhost:8000
└── package.json
```

## Getting Started

```bash
cd frontend
npm install
npm run dev          # → http://localhost:3000
```

The Vite dev server proxies `/api` requests to the FastAPI backend on `http://localhost:8000`.  
Start the backend first:

```bash
cd bench_forecast
uvicorn src.api.main:app --reload --port 8000
```

## Routes

| Path | Component | Description |
|---|---|---|
| `/` | `Dashboard` | Global overview — KPIs, charts, urgent queue |
| `/bench` | `BenchPipeline` | Sortable/filterable bench + pipeline tables |
| `/queue` | `ActionQueue` | AI-generated proposals by category |
| `/queue/:id` | `HITLApproval` | Full-page HITL review with approve / upskill / reject |
| `/settings` | `Settings` | Forecast horizon, win probability threshold, API base URL |

## Financial Logic

Margin is computed at display time from the SQLAlchemy formula:

```
projected_margin = (target_bill_rate - hourly_cost_rate) / target_bill_rate * 100
```

Color coding: ≥ 30% → green · 15–30% → amber · < 15% → red.

## Connecting to Live API

Replace mock data imports with TanStack Query hooks that call `src/api/client.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

const { data } = useQuery({
  queryKey: ['health'],
  queryFn: api.health,
})
```

The `GenerateForecastRequest` and `ExecuteForecastRequest` types in `src/types/index.ts`
match the FastAPI Pydantic schemas exactly.

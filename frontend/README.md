# Bench Forecast — Frontend

React + TypeScript + Vite frontend for Bench Forecast.

> **The repository root [`README.md`](../README.md) is the canonical project guide** — setup, roles, database, migrations, seeding, APIs, testing and operations all live there. This file only covers frontend-specific structure.

## Stack

- React 18
- TypeScript
- Vite 5
- React Router 6
- Tailwind CSS 3
- TanStack React Query 5
- Recharts 2

## Run

From the repository root:

```bash
cp .env.example .env
make setup
make api     # terminal 1 → http://localhost:8000
make web     # terminal 2 → http://localhost:3000
```

Vite proxies `/api` and `/health` to `VITE_API_PROXY_TARGET`
(default `http://localhost:8000`) from the single root `.env`.

## Routes

| Path | Page | Access |
|---|---|---|
| `/` | Dashboard | Admin; managers redirect to `/team` |
| `/projects` | Project directory | Admin / manager |
| `/projects/:id` | Project detail, roster, assign drawer, AI recommendations | Admin / manager |
| `/team` | Team workload / My Team | Admin / manager |
| `/approvals` | CV approval queue and diff modal | Admin / manager |
| `/trainings` | Training assignment and completion | Admin / manager |
| `/bench` | Bench & Pipeline | Admin / manager |
| `/queue` | AI Action Queue | Admin / manager |
| `/queue/:id` | HITL approval | Admin / manager |
| `/settings` | Forecast settings | Admin / manager |
| employee portal | Projects & Workload / Trainings / Profile & CV | Employee |

## Structure

```text
src/
├── api/client.ts            # typed fetch wrapper + structured ApiError
├── components/
│   ├── cv/                  # CVDiffView, CVStatusCard
│   ├── icons/               # inline SVG icon set
│   ├── projects/            # summary card, roster, assign drawer, AI panel
│   ├── shared/              # CapacityBar, badges
│   └── trainings/           # TrainingCard
├── contexts/AuthContext.tsx # admin | manager | employee session
├── hooks/                   # pending CV badge count
├── pages/                   # role-aware pages
├── types/index.ts           # API response types
├── App.tsx                  # role-aware shell and routes
├── main.tsx                 # entry point
└── index.css                # Tailwind + shared component classes
```

## Build

```bash
cd frontend
npm run build
```

Current status: TypeScript compilation and Vite production build pass. The only
advisory is Vite's non-blocking large-chunk warning.

## Demo logins

See the root README’s **Roles and Demo Accounts** section. Seeded accounts:

- `admin` / `admin`
- `manager` / `manager`
- generated employee username / `employee`

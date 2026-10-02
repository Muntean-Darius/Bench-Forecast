# Bench Forecast 2.0

An agentic workforce allocation engine with RAG matching and a Human-In-The-Loop (HITL) feedback loop.

## Quick Start Guide

### 1. Database (PostgreSQL via Docker)
You need a running PostgreSQL database. A Docker container named `bench-pg` should already be running from earlier. If you ever need to start it fresh, run:
```bash
docker run -d --name bench-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
```

### 2. Backend (FastAPI + LangGraph)
Open a terminal in the `bench_forecast/` directory, activate the virtual environment, and start the API server:
```bash
cd bench_forecast
source venv/bin/activate

# (Optional) If you need to reset the database and re-seed mock data:
# python seed.py

# Start the FastAPI server
python server.py
```
*The backend will run at http://localhost:8000. You can view the API docs at http://localhost:8000/docs.*

### 3. Frontend (React + Vite)
Open a **new terminal** in the `frontend/` directory, install dependencies (if you haven't), and start the dev server:
```bash
cd frontend
npm install
npm run dev
```
*The frontend will run at http://localhost:3000.*

---

## Testing the Main Flow
Once both servers are running, open **http://localhost:3000** in your browser.

1. **Generate Forecast:** Click **"Run Forecast"**. This triggers the LangGraph agent to extract data from PostgreSQL, use ChromaDB (RAG) to semantically match CVs against open demands, and generate a draft allocation plan.
2. **Human-in-the-Loop Review:** The workflow will pause and present the proposed allocations on your screen.
3. **Execute or Reject:**
   - **Approve:** If it looks good, click **"Approve & Execute"**. The agent will resume and write the allocations to the PostgreSQL database.
   - **Reject with Feedback:** If a match is bad, write feedback in the notes section (e.g., "Diana is too junior for this architect role, find someone else") and reject it. The LLM will revise the plan based on your feedback and present a new draft for you to review!


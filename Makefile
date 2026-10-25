# Bench Forecast — common development commands
#
# Uses the existing backend virtualenv. Override with:
#   make <target> PYTHON=/path/to/python
# Prefer a root .venv when present, otherwise use the existing backend venv.
PYTHON ?= $(shell if [ -x "$(CURDIR)/.venv/bin/python" ]; then echo "$(CURDIR)/.venv/bin/python"; else echo "$(CURDIR)/bench_forecast/venv/bin/python"; fi)
COMPOSE ?= docker compose

.PHONY: help setup db-up db-wait migrate backfill seed db-check api web test

help:
	@echo "Bench Forecast"
	@echo "  make setup      install backend + frontend dependencies"
	@echo "  make db-up      start the Docker PostgreSQL (host port 5434)"
	@echo "  make migrate    apply Alembic migrations to the Docker database"
	@echo "  make backfill   non-destructively backfill skills/manager/hours data"
	@echo "  make seed       reset + seed the mocked data (reproducible)"
	@echo "  make db-check   show the connected database and row counts"
	@echo "  make api        run the FastAPI backend on :8000"
	@echo "  make web        run the Vite frontend on :3000"
	@echo "  make test       run the backend tests"

setup:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	cd frontend && npm ci

db-up:
	$(COMPOSE) up -d postgres

db-wait:
	@echo "Waiting for PostgreSQL on 127.0.0.1:5434..."
	@until docker exec bench_postgres pg_isready -U postgres -d bench_forecast >/dev/null 2>&1; do sleep 1; done

migrate: db-up db-wait
	cd bench_forecast && $(PYTHON) -m scripts.migrate

backfill: migrate
	cd bench_forecast && $(PYTHON) -m scripts.backfill_usability

seed: migrate
	cd bench_forecast && $(PYTHON) -m scripts.seed_simulation

db-check:
	cd bench_forecast && $(PYTHON) -m scripts.check_db

api:
	cd bench_forecast && $(PYTHON) server.py

web:
	cd frontend && npm run dev

test:
	$(PYTHON) -m pytest -q

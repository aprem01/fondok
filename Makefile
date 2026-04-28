.PHONY: help install dev web worker worker-dev worker-test test lint typecheck evals db-up db-down build clean

help:
	@echo "Fondok monorepo — common commands"
	@echo ""
	@echo "  make install      Install all JS + Python deps"
	@echo "  make dev          Start Postgres + run web (3000) + worker (8001) in parallel"
	@echo "  make web          Run web only"
	@echo "  make worker       Run worker only (Postgres-backed, port 8001)"
	@echo "  make worker-dev   Run worker against local SQLite (no Docker needed)"
	@echo "  make worker-test  Run worker pytest suite"
	@echo "  make db-up        Start Postgres + Redis via docker compose"
	@echo "  make db-down      Stop local data plane"
	@echo "  make build        Build the Next.js web app"
	@echo "  make test         Run all tests"
	@echo "  make lint         Run all linters"
	@echo "  make typecheck    Run all type checkers (tsc + mypy)"
	@echo "  make evals        Run the golden-set regression suite"
	@echo "  make clean        Remove build artifacts and caches"

# ─── one-touch dev ───────────────────────────────────────────────────
install:
	npm install
	cd apps/worker && uv sync --extra dev || true

dev: db-up
	@echo "Starting web (3000) + worker (8001) in parallel…"
	@trap 'kill 0' INT; \
	(cd apps/web && npx next dev) & \
	(cd apps/worker && DATABASE_URL=postgresql+asyncpg://fondok:fondok@localhost:5432/fondok uv run uvicorn app.main:app --reload --port 8001) & \
	wait

web:
	cd apps/web && npx next dev

worker:
	cd apps/worker && DATABASE_URL=postgresql+asyncpg://fondok:fondok@localhost:5432/fondok uv run uvicorn app.main:app --reload --port 8001

worker-dev:
	cd apps/worker && DATABASE_URL=sqlite+aiosqlite:///./fondok.db uv run uvicorn app.main:app --reload --port 8001

worker-test:
	cd apps/worker && uv run pytest tests/ -v --ignore=tests/test_agents.py --ignore=tests/test_cache_hits.py

# ─── data plane ──────────────────────────────────────────────────────
db-up:
	docker compose -f infra/docker-compose.yml up -d
	@echo "Postgres: localhost:5432  user=fondok  pass=fondok  db=fondok"

db-down:
	docker compose -f infra/docker-compose.yml down

# ─── quality gates ───────────────────────────────────────────────────
build:
	cd apps/web && npx next build

lint:
	cd apps/web && npx next lint
	cd apps/worker && uv run ruff check . || true

typecheck:
	cd apps/web && npx tsc --noEmit
	cd apps/worker && uv run mypy app || true

test: worker-test
	cd apps/web && npm test 2>/dev/null || echo "(web has no test runner yet)"

evals:
	cd /Users/prem/fondok && uv run --project apps/worker python evals/run.py

clean:
	rm -rf apps/web/.next apps/web/node_modules node_modules apps/worker/__pycache__

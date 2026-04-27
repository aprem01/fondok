.PHONY: install dev build web worker worker-dev worker-test test lint evals clean

install:
	pnpm install

dev:
	pnpm --filter web dev

build:
	pnpm --filter web build

web:
	pnpm --filter web dev

worker:
	cd apps/worker && uv run uvicorn app.main:app --reload --port 8000

worker-dev:
	cd apps/worker && DATABASE_URL=sqlite+aiosqlite:///./fondok.db uv run uvicorn app.main:app --reload --port 8001

worker-test:
	cd apps/worker && uv run pytest tests/ -v

evals:
	python evals/run.py

test:
	pnpm --filter web test

lint:
	pnpm --filter web lint

clean:
	rm -rf apps/web/.next apps/web/node_modules node_modules

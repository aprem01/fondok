.PHONY: install dev build web worker test lint clean

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

test:
	pnpm --filter web test

lint:
	pnpm --filter web lint

clean:
	rm -rf apps/web/.next apps/web/node_modules node_modules

-include .env
export

.PHONY: help setup install up down restart logs db api web dev ingest ingest-date ingest-bounded replay-bounded restore-date benchmark-chunks check-benchmark-regression etl-baseline compare-etl-baselines audit-queries test test-unit test-integration lint clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# Setup

setup: install web-install ## First-time setup: venv + Python deps + Node deps
	@echo "\n✔ Setup complete. Run: make db"

install: ## Create venv and install Python dependencies
	python -m venv .venv
	.venv/bin/pip install -e ".[dev]"

web-install: ## Install frontend Node dependencies
	cd web && npm install

# Docker compose

db: ## Start PostgreSQL and apply pending migrations and seeds
	docker compose up -d postgres
	docker compose run --rm migrate

up: ## Start all services (PostgreSQL + Airflow + API + Web)
	docker compose up -d

down: ## Stop all services
	docker compose stop

restart: ## Restart all services
	docker compose restart

logs: ## Tail logs from all services
	docker compose logs -f --tail=50

# Development

api: ## Start API backend (port 8000, with hot reload)
	.venv/bin/uvicorn api.main:app --reload --port 8000

web: ## Start React frontend (port 5173, proxies /api to 8000)
	cd web && npm run dev

dev: db ## Start DB + API + frontend for local development
	@echo "Starting API and frontend in parallel..."
	@trap 'kill 0' EXIT; \
	.venv/bin/uvicorn api.main:app --reload --port 8000 & \
	cd web && npm run dev &\
	wait

# Pipeline

ingest: ## Run ETL pipeline for today's data
	.venv/bin/python -m etl.run_pipeline

ingest-date: ## Run ETL pipeline for a specific date (usage: make ingest-date DATE=2026-03-17)
	.venv/bin/python -m etl.run_pipeline --fecha $(DATE)

ingest-bounded: ## Run an isolated local partial ingestion (DATE=... and one DEV_* bound required)
	@test -n "$(DATE)" || (echo "DATE is required, for example DATE=2026-03-17"; exit 2)
	@test -n "$(DEV_CHAIN_KEYS)$(DEV_MAX_PRODUCT_ROWS)" || (echo "Set DEV_CHAIN_KEYS and/or DEV_MAX_PRODUCT_ROWS"; exit 2)
	SEPA_DEV_CHAIN_KEYS='$(DEV_CHAIN_KEYS)' SEPA_DEV_MAX_PRODUCT_ROWS='$(DEV_MAX_PRODUCT_ROWS)' \
	SEPA_FACT_CHUNK_SIZE='$(FACT_CHUNK_SIZE)' \
		.venv/bin/python -m etl.run_pipeline --fecha $(DATE)

replay-bounded: ## Replay a local ZIP without downloading (ZIP=..., DATE=..., and one DEV_* bound required)
	@test -n "$(ZIP)" || (echo "ZIP is required, for example ZIP=data/sepa.zip"; exit 2)
	@test -n "$(DATE)" || (echo "DATE is required, for example DATE=2026-09-22"; exit 2)
	@test -n "$(DEV_CHAIN_KEYS)$(DEV_MAX_PRODUCT_ROWS)" || (echo "Set DEV_CHAIN_KEYS and/or DEV_MAX_PRODUCT_ROWS"; exit 2)
	SEPA_DEV_CHAIN_KEYS='$(DEV_CHAIN_KEYS)' SEPA_DEV_MAX_PRODUCT_ROWS='$(DEV_MAX_PRODUCT_ROWS)' \
	SEPA_FACT_CHUNK_SIZE='$(FACT_CHUNK_SIZE)' \
		.venv/bin/python -m etl.run_pipeline --fecha $(DATE) --zip-path $(ZIP)

restore-date: ## Re-ingest a day from its archived source ZIP (usage: make restore-date DATE=2026-09-25)
	@test -n "$(DATE)" || (echo "DATE is required, for example DATE=2026-09-25"; exit 2)
	.venv/bin/python -m etl.run_pipeline --fecha $(DATE) --from-archive

benchmark-chunks: ## Repeat bounded local replays (ZIP= DATE= CHUNK_SIZES=... REPEATS=3 and one DEV_* bound required)
	@test -n "$(ZIP)$(DATE)$(CHUNK_SIZES)" || (echo "ZIP, DATE and CHUNK_SIZES are required"; exit 2)
	@test -n "$(DEV_CHAIN_KEYS)$(DEV_MAX_PRODUCT_ROWS)" || (echo "Set DEV_CHAIN_KEYS and/or DEV_MAX_PRODUCT_ROWS"; exit 2)
	@.venv/bin/python scripts/benchmark_fact_chunks.py --zip-path $(ZIP) --fecha $(DATE) --chain-keys '$(DEV_CHAIN_KEYS)' $(if $(DEV_MAX_PRODUCT_ROWS),--max-product-rows $(DEV_MAX_PRODUCT_ROWS)) --chunk-sizes $(CHUNK_SIZES) --repetitions $(or $(REPEATS),3)

check-benchmark-regression: ## Compare report artifacts (BASELINE=... CANDIDATE=...; limits are percentage variables)
	@test -n "$(BASELINE)$(CANDIDATE)" || (echo "BASELINE and CANDIDATE are required"; exit 2)
	.venv/bin/python scripts/check_benchmark_regression.py $(BASELINE) $(CANDIDATE) --max-elapsed-regression-pct $(or $(MAX_ELAPSED_REGRESSION_PCT),15) --max-peak-rss-regression-pct $(or $(MAX_PEAK_RSS_REGRESSION_PCT),20)

etl-baseline: ## Print persisted ETL metrics (optional: RUN_ID=...)
	.venv/bin/python scripts/report_ingestion_baseline.py $(RUN_ID)

compare-etl-baselines: ## Compare completed replays with the same source and bounds (RUN_ID=...)
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required"; exit 2)
	.venv/bin/python scripts/compare_ingestion_baselines.py $(RUN_ID)

audit-queries: ## Run EXPLAIN ANALYZE for representative published API reads (optional: FECHA_ID=...)
	.venv/bin/python scripts/benchmark_queries.py $(FECHA_ID)

# Testing

test: ## Run all tests
	.venv/bin/pytest $(PYTEST_ARGS)

test-unit: ## Run unit tests only
	.venv/bin/pytest tests/unit $(PYTEST_ARGS)

test-integration: ## Run integration tests (requires PostgreSQL)
	.venv/bin/pytest tests/integration $(PYTEST_ARGS)

lint: ## Run linter
	.venv/bin/ruff check .

clean: ## Remove venv, caches, and build artifacts
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache *.egg-info dist build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

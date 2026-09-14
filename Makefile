-include .env
export

.PHONY: help setup install up down restart logs db api web dev ingest test test-unit test-integration lint clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# Setup

setup: install web-install ## First-time setup: venv + Python deps + Node deps
	@echo "\n✔ Setup complete. Copy .env.example to .env and edit credentials, then run: make db"

install: ## Create venv and install Python dependencies
	python -m venv .venv
	.venv/bin/pip install -e ".[dev]"

web-install: ## Install frontend Node dependencies
	cd web && npm install

# Docker compose

db: ## Start PostgreSQL (runs migrations and seeds on first start)
	docker compose up -d postgres

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

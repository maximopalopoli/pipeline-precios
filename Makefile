.PHONY: test test-unit test-integration install up down restart logs dashboard venv

venv:
	python -m venv .venv

install: venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest $(PYTEST_ARGS)

test-unit:
	.venv/bin/pytest tests/unit $(PYTEST_ARGS)

test-integration:
	.venv/bin/pytest tests/integration $(PYTEST_ARGS)

up:
	docker compose up -d

down:
	docker compose stop

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=50

dashboard:
	.venv/bin/streamlit run dashboard/app.py

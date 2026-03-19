.PHONY: test test-unit test-integration install up down restart logs

install:
	pip install -e ".[dev]"

test:
	pytest $(PYTEST_ARGS)

test-unit:
	pytest tests/unit $(PYTEST_ARGS)

test-integration:
	pytest tests/integration $(PYTEST_ARGS)

up:
	docker compose up -d

down:
	docker compose stop

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=50

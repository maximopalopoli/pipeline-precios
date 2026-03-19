.PHONY: test test-unit test-integration install

install:
	pip install -e ".[dev]"

test:
	pytest $(PYTEST_ARGS)

test-unit:
	pytest tests/unit $(PYTEST_ARGS)

test-integration:
	pytest tests/integration $(PYTEST_ARGS)

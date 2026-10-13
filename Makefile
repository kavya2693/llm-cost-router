.PHONY: data models evaluate run check test lint format

data:
	uv run python scripts/generate_data.py

# Calls the local models through Ollama. Only needed to refresh the response cache in outputs/.
models:
	uv run llm-cost-router collect

# Replays every arm from the cached responses; needs no model server.
evaluate:
	uv run llm-cost-router evaluate

run: data evaluate

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest -q

check: lint test

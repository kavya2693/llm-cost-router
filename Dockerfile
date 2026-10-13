FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY scripts/ scripts/
COPY outputs/responses/ outputs/responses/
RUN uv sync --locked --no-dev && python scripts/generate_data.py

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

# Replays the cached model responses; set OLLAMA_URL and run `llm-cost-router collect`
# against a reachable Ollama to refresh them instead.
CMD ["llm-cost-router", "evaluate"]

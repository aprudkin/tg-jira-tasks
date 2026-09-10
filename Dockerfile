FROM ghcr.io/astral-sh/uv:0.12.12 AS uv
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY bot/ ./bot/

CMD ["python", "-m", "bot.main"]

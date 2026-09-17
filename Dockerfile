FROM ghcr.io/astral-sh/uv:0.12.12 AS uv
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY bot/ ./bot/

# Fixed IDs let the deploy migrate the existing named volume once and let
# rollback images keep using the same state ownership.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && chmod -R a=rX /app/bot \
    && mkdir -p /app/data \
    && chown app:app /app/data
USER 10001:10001

CMD ["python", "-m", "bot.main"]

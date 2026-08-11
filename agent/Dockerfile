FROM ghcr.io/astral-sh/uv:0.9.26-python3.12-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

FROM base AS development

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --all-groups --all-extras

COPY src/ src/
COPY migrations/ migrations/
COPY knowledge/ knowledge/
COPY tests/ tests/
COPY evals/ evals/
COPY alembic.ini langgraph.json ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-groups --all-extras

FROM base AS runtime

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /home/app/.cache/huggingface \
    && chown -R app:app /app /home/app/.cache

COPY --chown=app:app pyproject.toml uv.lock README.md ./

USER app
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=10001,gid=10001 \
    uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app src/ src/
COPY --chown=app:app migrations/ migrations/
COPY --chown=app:app knowledge/ knowledge/
COPY --chown=app:app alembic.ini langgraph.json ./
COPY --chown=app:app --chmod=0755 docker-entrypoint.sh ./docker-entrypoint.sh
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=10001,gid=10001 \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/home/app/.cache/huggingface/sentence-transformers

EXPOSE 2024
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2024/ok', timeout=3)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", "--no-browser", "--no-reload"]

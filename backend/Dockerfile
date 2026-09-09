# syntax=docker/dockerfile:1.7
# RAG Backborn backend — FastAPI + the Claude Code CLI.
# Node and the CLI are built in a separate stage so the runtime image never executes a
# curl|bash bootstrap of its own.
FROM node:22-bookworm-slim AS claude
ARG CLAUDE_CODE_VERSION=2.1.236
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force \
    && claude --version

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    HOME=/home/ragb \
    UV_CACHE_DIR=/root/.cache/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gosu tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ragb \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash ragb
COPY --from=claude /usr/local/bin/node /usr/local/bin/node
COPY --from=claude /usr/local/bin/claude /usr/local/bin/claude
COPY --from=claude /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY entrypoint.sh ./entrypoint.sh
# UV_CACHE_DIR keeps the wheel cache in a build mount rather than in $HOME, where the chown
# below would walk it on every build and ship it inside the image.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev \
    && chmod 0755 entrypoint.sh \
    && mkdir -p /data /home/ragb/.claude /home/ragb/.cache \
    && chown -R ragb:ragb /data /home/ragb \
    && /usr/local/bin/claude --version

ENV PATH="/app/.venv/bin:/usr/local/bin:${PATH}" \
    RAGB_DATA_DIR=/data \
    RAGB_CLAUDE_HOME=/home/ragb/.claude \
    RAGB_CLAUDE_BINARY=/usr/local/bin/claude
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --retries=3 --start-period=45s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]

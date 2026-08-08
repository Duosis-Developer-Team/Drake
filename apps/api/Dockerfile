# Drake API image.
#
# Base images are digest-pinned, like the cluster agent's: a tag can be
# repointed under us, and "we built from python:3.13-slim" then stops
# describing anything reproducible.
#
# The runtime image carries the application and Alembic, because the
# migration Job runs `alembic upgrade head` from this same image — one
# artifact, one source commit, no separate migration image drifting from
# the code it migrates.

# python:3.13-slim-bookworm
FROM python@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv, pinned by digest through its own distroless image.
COPY --from=ghcr.io/astral-sh/uv@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85 /uv /usr/local/bin/uv

# Built at the path it will RUN from. A virtualenv is not relocatable:
# console scripts embed an absolute interpreter path in their shebang, so
# creating it at /build and copying it to /app leaves `alembic` as a file
# that exists and cannot execute.
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages ./packages
COPY apps/api ./apps/api

RUN uv sync --frozen --no-dev --package drake-api

# python:3.13-slim-bookworm (same digest as the builder)
FROM python@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8

# Non-root, and the same uid the chart's securityContext expects.
RUN groupadd --gid 65532 nonroot && \
    useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin nonroot

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/apps/api /app/apps/api
COPY --from=build /app/packages /app/packages

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/api/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/apps/api
USER 65532:65532
EXPOSE 8000

# No secret, no origin and no host is baked in: every one of them comes
# from the environment the chart supplies.
CMD ["uvicorn", "drake_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# Image for the Compose web service: Python, the GDAL, GEOS, and PROJ libraries that
# GeoDjango loads, and the project environment exactly as uv.lock pins it. It never
# contains source code: compose.yaml bind-mounts the repository at /app.
FROM python:3.14-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /bin/uv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgdal36 libgeos-c1t64 libproj25 \
    && rm -rf /var/lib/apt/lists/*

# The environment lives in /opt/venv, outside the bind mount, and its bin directory comes
# first on PATH so that python, pytest, and mypy resolve to it. UV_PYTHON_DOWNLOADS=never
# makes uv use this image's Python, so the build fails when it does not match
# .python-version.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY .python-version pyproject.toml uv.lock ./

# --locked installs exactly what uv.lock pins and fails when uv.lock does not match
# pyproject.toml.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

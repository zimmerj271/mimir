FROM ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS uv
FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock backend/README.md backend/.python-version ./
COPY backend/src ./src
RUN uv sync --locked --no-default-groups --no-editable --no-managed-python --python /usr/local/bin/python

FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56
COPY --from=build /build/.venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
WORKDIR /app
# No placeholder API, health response, or successful idle process. Application
# entry point and compatible-schema startup belong to subsequent assigned tasks.
ENTRYPOINT []
CMD []

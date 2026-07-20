# Runs the first vertical slice's real API: GET /health,
# POST /devices/{device_id}/config, GET /incidents. `alembic upgrade head`
# runs to completion before Uvicorn starts (see the CMD below), and the
# FastAPI lifespan performs idempotent Slice 1 policy seeding once
# migrations have completed (architecture.md Section 11.2). Frontend and
# browser E2E (Playwright) remain deferred — see CLAUDE.md "Current Phase".
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user — binding requirement, not optional hardening.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

WORKDIR /app

# Production dependencies only — no pytest/ruff/mypy in the runtime image.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Alembic configuration and migration scripts — alembic/versions/ holds
# the real Slice 1 schema migration (devices, configuration_snapshots,
# configuration_policies, incidents, and the partial unique OPEN-fingerprint
# index; see docs/architecture.md Section 11).
COPY alembic.ini ./
COPY alembic ./alembic

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Python standard library only — no curl/wget installed just for this
# (binding requirement).
HEALTHCHECK --interval=2s --timeout=3s --retries=10 --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=2)"

# Alembic runs to completion as an explicit deployment step BEFORE
# Uvicorn starts accepting requests — never a FastAPI startup event
# (architecture.md Section 11.2). `exec` hands off PID 1 to Uvicorn so it
# receives signals directly (e.g. SIGTERM on `docker compose down`).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn meta_rne.api.app:app --host 0.0.0.0 --port 8080"]

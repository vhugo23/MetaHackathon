# Meta RNE Platform — Claude Instructions

## Project Goal

Build a working data-center network-operations prototype, inspired by
hyperscale requirements — not a system that itself operates at hyperscale.

The platform should:

- ingest network-device configurations
- normalize configurations from different vendors
- evaluate normalized configurations against required-configuration policies
- detect configuration drift against a fixed baseline
- ingest or simulate telemetry
- detect operational anomalies deterministically
- generate incidents with evidence and recommendations, deduplicated so the
  same unresolved finding never creates more than one open incident
- expose a REST API, consumed by a read-only React operator dashboard

## Development Rules

1. Do not implement a feature before its requirements are documented.
2. Write tests before or alongside implementation.
3. Work on one bounded task at a time.
4. Do not add dependencies without explaining why.
5. Do not silently change architecture.
6. Do not bypass failing tests.
7. Prefer deterministic detection rules before machine learning.
8. Keep vendor-specific logic behind adapters.
9. Keep domain logic independent from frameworks.
10. Every completed task must build and pass tests.

## Workflow

For each task:

1. Read the relevant documentation.
2. Restate the acceptance criteria.
3. Propose a small implementation plan.
4. Identify affected files.
5. Write or update tests.
6. Implement the minimum required code.
7. Run tests and report results.
8. Summarize architectural consequences.

## Current Phase

**Day 2 — Repository Scaffolding.**

Day 1 planning (product-spec.md, architecture.md, domain-model.md,
test-strategy.md, both ADRs) is approved. See README.md's "Current
Project Status" for the full history of the Day 1 correction passes.

Application code is allowed **only** for:

- project/package scaffolding (`backend/` src-layout)
- FastAPI application creation
- `GET /health`
- Docker and PostgreSQL startup (Dockerfile, docker-compose.yml)
- Alembic configuration (no migrations yet — no models exist to migrate)
- automated tests for the health endpoint
- formatting, linting, type checking, and CI

**All Slice 1 business logic remains prohibited**: no configuration
parsing, vendor adapters, policy evaluation, incidents, telemetry, or
React. Those begin on a later day, against the domain model and
architecture already documented, with tests written first per the
Development Rules above.
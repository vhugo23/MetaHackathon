# ADR 0002: Technology Stack and Persistence Model

**Status:** Accepted
**Date:** 2026-07-18

## Context

The modular monolith decided in [ADR-0001](./0001-modular-monolith.md)
needs a concrete backend stack, frontend stack, test stack, and
persistence model. Earlier drafts of this project's documentation left
the implementation language, HTTP framework, and persistence layer
unresolved, which blocked writing a concrete test strategy. This ADR
closes those gaps.

## Decision

**Backend:** Python 3.12, FastAPI (HTTP layer), Pydantic (request/response
schemas and validation — also usable for domain-adjacent data validation
without coupling `domain` itself to Pydantic types, per NFR-02),
SQLAlchemy (repository implementations), PostgreSQL (persistence),
**Alembic** (schema migrations — the only schema-management tool; the
first migration creates the Slice 1 tables and the partial unique index
behind incident deduplication, architecture.md Section 11.2).

**Frontend (later slice, FR-10):** React, TypeScript, Vite.

**Testing:** pytest (backend unit/integration/contract), Vitest (frontend,
once it exists), Playwright (end-to-end, HTTP-request mode until FR-10
ships — see test-strategy.md Section 7).

**Deployment:** Docker Compose (architecture.md Section 15) — an `api`
service and a `db` (PostgreSQL) service for the first vertical slice; a
`frontend` service reserved in the compose shape for FR-10.

**CI:** GitHub Actions, running the gates in test-strategy.md Section 15.

**Persistence model (binding):**

- **PostgreSQL is the production persistence layer** for the final MVP —
  not an optional upgrade from in-memory storage. `Device`,
  `ConfigurationSnapshot` (with its `normalized_config` embedded inline,
  domain-model.md Section 4), `ConfigurationPolicy`, `Incident`, and
  `TelemetrySample` are all persisted via SQLAlchemy repositories.
- **In-memory implementations of the same repository interfaces are
  permitted only as test doubles**, for fast unit and integration tests
  (test-strategy.md Sections 4–5). They are never used in a deployed
  environment.
- **End-to-end tests must use the real API and a real PostgreSQL
  container** (test-strategy.md Section 7) — no in-memory substitution at
  that level, since E2E's purpose is to prove the deployed system works,
  including the SQLAlchemy/Postgres path.
- A **repository conformance test suite** (test-strategy.md Section 9)
  runs against both the in-memory and SQLAlchemy implementations of every
  repository interface, so "in-memory as a test double" is a verified
  substitution, not an unverified assumption that the two behave alike.

## Consequences

- **Positive:** FastAPI + Pydantic gives schema validation (422 on
  malformed request bodies, NFR-05) largely for free, and an in-process
  `TestClient` for fast contract tests (test-strategy.md Section 6).
- **Positive:** a real database from day one means transactional
  guarantees (architecture.md Section 12 — config ingestion, policy
  evaluation, and incident write/dedup happen in one DB transaction)
  instead of a design placeholder for "what happens when persistence
  fails," and removes the "state doesn't survive a restart" caveat that
  applied to the earlier in-memory-only design.
- **Positive:** PostgreSQL's partial unique indexes let the incident
  deduplication invariant (domain-model.md Section 11 — no two `OPEN`
  incidents may share a fingerprint, even under concurrent requests) be
  enforced by the database itself, not only by application logic. A
  SQLite or in-memory-only design would have left that invariant resting
  entirely on a find-then-save pattern in application code, which cannot
  be made race-free without the database's help.
- **Positive:** pytest/Vitest/Playwright is a coherent, widely-supported
  combination with first-class async/HTTP support matching FastAPI and
  React.
- **Positive:** Alembic migrations, not `Base.metadata.create_all()`, make
  the schema (including the partial unique index) an explicit, versioned
  artifact — the same one that runs in E2E (architecture.md Section 15.1),
  CI, and production, rather than three environments quietly diverging.
- **Negative:** the first vertical slice now depends on a running
  PostgreSQL instance even for local development (`docker compose up`,
  or a local Postgres) — slightly more setup than a pure in-memory MVP,
  accepted because the final MVP needs Postgres regardless, and
  introducing it on day one avoids a later migration of every repository
  implementation and every test that assumed in-memory-only storage.
- **Negative:** CI needs a PostgreSQL service container for the
  repository-conformance and E2E gates (test-strategy.md Section 15),
  adding a few seconds of container startup per CI run. Accepted as the
  cost of testing against the real persistence layer rather than only a
  fast approximation of it.

## Alternatives Considered

- **In-memory-only persistence (the earlier design)** — rejected: this
  correction pass's binding decision requires PostgreSQL for the final
  MVP; keeping in-memory as the *only* store would have made every
  E2E/production claim untestable against what actually ships.
- **SQLite instead of PostgreSQL** — rejected: PostgreSQL is explicitly
  named in the binding technology decision, and using SQLite for
  "simplicity" would reintroduce exactly the kind of dev/prod persistence
  divergence this ADR exists to close.
- **An ORM-less raw-SQL layer instead of SQLAlchemy** — rejected: no
  stated reason to avoid SQLAlchemy, and it is explicitly named in the
  binding technology decision.

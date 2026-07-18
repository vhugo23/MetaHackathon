# Meta RNE Platform

A working data-center network-operations prototype, inspired by
hyperscale requirements. It does not itself operate at hyperscale — it
demonstrates, at prototype scale, the patterns that hyperscale network
operations require: vendor-neutral configuration normalization,
required-configuration policy evaluation, configuration drift detection,
deterministic telemetry-based anomaly detection, and deduplicated,
evidence-backed incident generation.

## Project Purpose

Network operators managing multi-vendor data-center fleets face three
recurring problems: configuration management is fragmented across vendor
consoles, anomaly detection is reactive, and incident investigation is
slow and undirected. This platform ingests vendor device configurations,
normalizes them into one canonical model, checks them against declared
policies and a stored baseline, ingests or simulates telemetry, evaluates
deterministic anomaly rules, and turns any of those findings into a
single, deduplicated `Incident` record with structured evidence and a
recommendation — queryable via REST and, later, a read-only dashboard.

See [docs/problem-statement.md](./docs/problem-statement.md) for the
original hackathon brief this project is inspired by.

## MVP Boundaries

**In scope for the final MVP** (see
[docs/product-spec.md](./docs/product-spec.md) Section 6):

- Vendor configuration ingestion and normalization (Cisco, Arista)
- Required-configuration policy evaluation
- Configuration drift detection
- Telemetry ingestion and simulation
- Deterministic anomaly detection (3 rules)
- Incident creation and deduplication
- REST API
- Read-only React operator dashboard

**Explicitly out of scope** (see product-spec.md Section 7): automated
repair/remediation, live device configuration push, machine learning,
external alert integrations, authentication, multi-tenancy,
high-availability deployment, and any vendor beyond Cisco/Arista.

## First Vertical Slice

Cisco config → parse → normalize → evaluate a required-ACL policy →
create one incident → retrieve it via `GET /incidents`. One vendor, one
config submission, one detection path (policy, not drift), no telemetry,
no dashboard — and **exactly two HTTP endpoints**:

- `POST /devices/{id}/config`
- `GET /incidents`

```
POST /devices/spine-01/config  →  CiscoAdapter.parse()  →  NormalizedConfiguration
   →  PolicyEvaluator (missing ACL-EXTERNAL-IN)  →  one ConfigurationViolation
   →  IncidentFactory.build_candidate + atomic upsert  →  one Incident (severity: Medium)
   →  (transaction commits) stdout JSON log
   →  201 response: { normalized_config, violations_detected: 1,
                       incidents_created: 1, incidents_updated: 0 }
   →  GET /incidents
```

A second, identical submission is tested separately to prove
deduplication (`incidents_created: 0, incidents_updated: 1` on the
repeat) — it is not part of the primary one-submission demonstration
above.

Full sequence, rationale, and API contract: product-spec.md Section 11
and architecture.md's closing summary.

## Chosen Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic |
| Persistence | PostgreSQL via SQLAlchemy (in-memory repos are test doubles only) |
| Frontend (later slice) | React, TypeScript, Vite |
| Testing | pytest, Vitest, Playwright |
| Deployment | Docker Compose |
| CI | GitHub Actions |
| Architecture | Modular monolith (see [ADR-0001](./docs/adr/0001-modular-monolith.md)) |

Full rationale: [ADR-0001](./docs/adr/0001-modular-monolith.md) and
[ADR-0002](./docs/adr/0002-technology-stack-and-persistence.md).

## Getting Started

### Prerequisites

- Python 3.12
- Docker + Docker Compose v2 (`docker compose`, not the standalone `docker-compose`)

### Local Python setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Bash/macOS/Linux
```

On Windows (PowerShell):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Install dependencies

Runtime + dev dependencies, pinned in `backend/pyproject.toml`:

```bash
pip install -e ".[dev]"
```

### Run tests

```bash
pytest
```

### Lint, format, and type-check

```bash
ruff format --check .   # formatting check only; drop --check to apply
ruff check .             # linting
mypy src                 # static type checking
```

### Start / stop the stack (Docker Compose)

From the repository root:

```bash
docker compose up --build -d   # build the API image, start PostgreSQL + API
docker compose ps               # both services should report "healthy"
docker compose down -v          # stop and remove containers + the PostgreSQL volume
```

### Health endpoint

Once the stack is up: [http://localhost:8080/health](http://localhost:8080/health)
→ `{"status": "ok"}`. This is a **liveness check only** — it does not
query PostgreSQL. Database connectivity is proven separately, by the
container startup sequence itself: PostgreSQL must pass its own
`pg_isready` healthcheck before the API container starts, and
`alembic upgrade head` must complete successfully before Uvicorn starts
accepting requests (architecture.md Section 11.2) — if either step fails,
the API container never becomes healthy.

### Current Day 2 scope

Scaffolding only — see [CLAUDE.md](./CLAUDE.md) "Current Phase":
project/package structure, the FastAPI app, `GET /health`, Docker +
PostgreSQL startup, Alembic wiring (no migrations yet), and CI
(format/lint/type-check/tests/compose smoke test).

**Not implemented yet** (deliberately, per the approved Day 2 plan):
configuration parsing, vendor adapters, policy evaluation, incidents,
telemetry, drift detection, the React dashboard, `compose.e2e.yml`, and
the Playwright E2E suite. These begin on a later day, against the
architecture and domain model already documented, tests written first.

## Planning Documents

- [docs/problem-statement.md](./docs/problem-statement.md) — original hackathon brief
- [docs/product-spec.md](./docs/product-spec.md) — requirements, acceptance criteria, vertical slice
- [docs/architecture.md](./docs/architecture.md) — modular monolith design, flows, deployment
- [docs/domain-model.md](./docs/domain-model.md) — entities, invariants, deduplication
- [docs/test-strategy.md](./docs/test-strategy.md) — test levels, fixtures, first-slice test plan
- [docs/adr/0001-modular-monolith.md](./docs/adr/0001-modular-monolith.md)
- [docs/adr/0002-technology-stack-and-persistence.md](./docs/adr/0002-technology-stack-and-persistence.md)

## Current Project Status

**Day 2 — Repository Scaffolding.** Planning (product-spec.md,
architecture.md, domain-model.md, test-strategy.md, both ADRs) went
through two consistency correction passes on 2026-07-18 — the first
resolved cross-document conflicts (positioning, technology stack,
baseline semantics, incident deduplication, error taxonomy, FR/AC
numbering); the second was an implementation-readiness pass
(deterministic normalization, the two-stage vendor-validation boundary,
atomic incident deduplication via a PostgreSQL partial unique index, the
exact Slice 1 endpoint list and `POST` response shape, identifier-format
rules, the Cisco parser-failure contract, log-emission-after-commit
semantics) — and was approved.

Day 2 scaffolds the backend per [CLAUDE.md](./CLAUDE.md) "Current
Phase": package structure, a FastAPI app with `GET /health`, Docker
Compose with PostgreSQL, Alembic wiring, pinned dependencies, and CI. See
"Getting Started" above for how to run it and "Current Day 2 scope" for
exactly what is and is not implemented. Slice 1 business logic
(configuration parsing, policy evaluation, incidents, deduplication) has
not started.

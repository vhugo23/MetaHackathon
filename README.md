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

### Current Day 4A scope

See [CLAUDE.md](./CLAUDE.md) "Current Phase". Day 2 scaffolding
(package structure, `GET /health`, Docker + PostgreSQL startup, Alembic
wiring, CI) is done. Day 3A added, as pure domain code with no framework
dependency: the `Normalized*` configuration value objects, the
`VendorConfigAdapter` port, `AdapterRegistry`, and a representative Cisco
IOS-XE parser (`meta_rne.adapters.cisco.CiscoAdapter`).

Day 4A adds, also framework-independent (FR-07, NFR-02/NFR-03):

- `IncidentSource`/`IncidentStatus` — the approved enums
  (`meta_rne.domain.incident`).
- `IncidentCandidate` and `PolicyViolationIncidentEvidence` — the
  pre-fingerprint, pre-persistence shape `IncidentFactory` produces, and
  its immutable evidence mapping from `ConfigurationViolation`.
- `IncidentFactory.build_candidate(violation) -> IncidentCandidate`
  (`meta_rne.detection.incident_factory`) — pure function of a single
  `ConfigurationViolation`, no clock/repository/logger access. For a
  `POLICY_VIOLATION` finding, `device_id`, `rule_ref`, `affected_resource`,
  `severity`, and `recommendation` are copied verbatim (no template
  rewriting); `observed_at` is copied from `violation.detected_at`;
  `evidence` is remapped into `PolicyViolationIncidentEvidence`, adding
  `violation_type`/`source_snapshot_id` and preserving `actual_acl_name`.
- `compute_fingerprint(device_id, source, rule_ref, affected_resource) ->
  str` (`meta_rne.domain.incident`) — SHA-256 hex digest of a canonical
  JSON array (`separators=(",", ":")`, UTF-8), never a delimiter-joined
  string.
- **UTC timestamp validation**: `IncidentCandidate.observed_at` must be
  timezone-aware UTC; naive or non-UTC-offset values raise `ValueError`,
  same pattern as `ConfigurationPolicy.created_at`/`PolicyEvaluator.observed_at`.

Backend test count: **93** (`pytest`, 100% line coverage on
`domain/incident.py` and `detection/incident_factory.py`).

### Current Day 4B1 scope

Day 4B ("Slice 1 persistence foundations") is split into three reviewable
gates — **4B1 (this gate), 4B2, 4B3** — rather than one large pass. 4B1
adds only the persistence *foundations*; no repository or the atomic
incident upsert is implemented yet.

Day 4B1 adds:

- Persisted domain shapes, framework-independent: `Device`
  (`meta_rne.domain.device`), `ConfigurationSnapshot` +
  `compute_raw_text_hash` (`meta_rne.domain.snapshot`), and the persisted
  `Incident`/`IncidentUpsertOutcome`/`IncidentUpsertResult`
  (`meta_rne.domain.incident`). `ConfigurationSnapshot` validates that
  `raw_text_hash` is a lowercase 64-character hex SHA-256 digest and
  actually matches `compute_raw_text_hash(raw_config_text)`; `Incident`
  validates its `fingerprint` against the same pattern. Field names are
  `Device.created_at`/`updated_at` and
  `ConfigurationSnapshot.raw_config_text`/`raw_text_hash` — see CLAUDE.md
  "Documentation corrections applied for Day 4B1" for the rename from an
  earlier draft.
- `DeviceRepository`, `ConfigurationSnapshotRepository`,
  `ConfigurationPolicyRepository`, `IncidentRepository`, and `UnitOfWork`
  — `typing.Protocol` interfaces only (`meta_rne.domain.ports`), no
  concrete implementation. `IncidentRepository` has no
  `find_open_by_fingerprint`; the atomic upsert is the only documented
  dedup mechanism.
- Explicit JSON (de)serialization (`meta_rne.persistence.serialization`)
  for `NormalizedConfiguration`, `RequiredAclRule` tuples, and
  `PolicyViolationIncidentEvidence` — no pickle, enums preserved by
  value, tuple ordering preserved, one stable `SerializationError` for any
  malformed stored structure (never a leaked `KeyError`/`TypeError`/
  `ValueError`/`AttributeError`). Day 4B1 evidence serialization supports
  `IncidentSource.POLICY_VIOLATION` only.
- Private SQLAlchemy declarative ORM models
  (`meta_rne.persistence.sqlalchemy.models`: `_DeviceModel`,
  `_ConfigurationSnapshotModel`, `_ConfigurationPolicyModel`,
  `_IncidentModel`) — internal to the `persistence` package, never
  imported elsewhere; `metadata = _Base.metadata` is exposed for Alembic
  only.
- The first Slice 1 Alembic migration
  (`alembic/versions/0001_slice1_persistence_foundations.py`, hand-written,
  not autogenerated): creates `devices`, `configuration_snapshots`,
  `configuration_policies`, and `incidents`, the two-stage
  `devices <-> configuration_snapshots` foreign keys (added only after
  both tables exist, resolving the circular reference), CHECK constraints
  for every TEXT-backed enum column plus hash format/occurrence-count/
  timestamp-ordering invariants, and the partial unique index
  (`ux_incidents_open_fingerprint` on `fingerprint` `WHERE status =
  'OPEN'`). `alembic/env.py` now points `target_metadata` at the ORM
  models' metadata (schema-consistency/autogenerate support only —
  `Base.metadata.create_all()` is still never used; every migration
  remains hand-written).
- **PostgreSQL test separation**: a new `postgres` pytest marker
  (`backend/pyproject.toml`). `pytest -m "not postgres"` runs the fast,
  no-database suite; `pytest -m postgres` runs migration tests against a
  disposable `meta_rne_migration_test` database (reset to Alembic base
  before every test via a function-scoped fixture, so no migration test
  depends on another's execution order), separate from
  `meta_rne_test` (reserved for Day 4B2/4B3 repository conformance tests)
  and the development database `meta_rne` (never touched by tests).
  `.github/workflows/ci.yml` runs these in two jobs: the existing `ci` job
  now runs `pytest -m "not postgres"`, and a new `postgres-tests` job
  spins up a PostgreSQL 16 GitHub Actions service container, creates both
  test databases, and runs `pytest -m postgres` — cleaned up automatically
  when the job ends.

Backend test count: **135** (`pytest`; 128 run via `pytest -m "not
postgres"`, 7 via `pytest -m postgres` against a real PostgreSQL
instance). 100% line coverage on `domain/incident.py`,
`persistence/sqlalchemy/models.py`, and `domain/ports.py`; 96-97% on
`domain/device.py`/`domain/snapshot.py` (the one uncovered line in each is
the module-level docstring/import, not logic).

**Not implemented yet** (deliberately — Day 4B2/4B3, per the approved,
gated Day 4B plan): any concrete repository (SQLAlchemy or in-memory) for
`Device`/`ConfigurationSnapshot`/`ConfigurationPolicy`/`Incident`,
`seed_if_missing`, `SnapshotAlreadyExistsError`, the atomic
`upsert_open_incident` implementation (the `INSERT ... ON CONFLICT ...
DO UPDATE` strategy is designed and approved, not yet built), concurrency
tests, the concrete `UnitOfWork`, `ConfigIngestionService`, FastAPI
ingestion endpoints, structured logging, `DriftDetector`, `RuleEngine`,
telemetry, and the React dashboard. These begin on Day 4B2 (repositories,
seeding, UnitOfWork) and Day 4B3 (atomic upsert, concurrency).

Day 3B added, also framework-independent (FR-03, NFR-02/NFR-03):

- `ConfigurationPolicy` and `RequiredAclRule` — a seeded, fixture-data
  representation of a required inbound/outbound ACL assignment, each
  rule carrying its own `severity` and `recommendation`.
- `ConfigurationViolation` and structured ACL evidence
  (`AclAssignmentEvidence`) — the evaluator's output shape, distinguishing
  a missing target interface (`TARGET_INTERFACE_MISSING`) from a
  missing/unassigned/different required ACL (`MISSING_REQUIRED_ACL`),
  never silently treating a missing interface as a satisfied policy.
- A deterministic `PolicyEvaluator.evaluate(device_id, source_snapshot_id,
  observed_at, config, policies) -> tuple[ConfigurationViolation, ...]` —
  pure function, no clock/repository/logger access, returns violations in
  `policies`-then-`required_acls` tuple order.
- **Exact device applicability**: `policy.applies_to == device_id` only —
  `"*"` wildcard matching is not implemented this phase.
- **UTC timestamp validation**: `ConfigurationPolicy.created_at` and
  `PolicyEvaluator`'s `observed_at` must be timezone-aware UTC; naive or
  non-UTC-offset values raise `ValueError`.

Backend test count: **60** (`pytest`, 100% line coverage on
`domain/policy.py` and `detection/policy_evaluator.py`).

**Not implemented yet** (deliberately, per the approved Day 3B plan):
`IncidentFactory`/`Incident`, fingerprinting/deduplication, FastAPI
configuration-ingestion endpoints, SQLAlchemy repositories or tables,
Alembic business migrations, `Device`/`ConfigurationSnapshot`
persistence, `DriftDetector`, `RuleEngine`, telemetry, the React
dashboard, `compose.e2e.yml`, and the Playwright E2E suite. These begin
on a later day, against the architecture and domain model already
documented, tests written first.

## Planning Documents

- [docs/problem-statement.md](./docs/problem-statement.md) — original hackathon brief
- [docs/product-spec.md](./docs/product-spec.md) — requirements, acceptance criteria, vertical slice
- [docs/architecture.md](./docs/architecture.md) — modular monolith design, flows, deployment
- [docs/domain-model.md](./docs/domain-model.md) — entities, invariants, deduplication
- [docs/test-strategy.md](./docs/test-strategy.md) — test levels, fixtures, first-slice test plan
- [docs/adr/0001-modular-monolith.md](./docs/adr/0001-modular-monolith.md)
- [docs/adr/0002-technology-stack-and-persistence.md](./docs/adr/0002-technology-stack-and-persistence.md)

## Current Project Status

**Day 3A — Domain Foundations and Cisco IOS-XE Normalization.** Planning
(product-spec.md, architecture.md, domain-model.md, test-strategy.md,
both ADRs) went through two consistency correction passes on 2026-07-18
— the first resolved cross-document conflicts (positioning, technology
stack, baseline semantics, incident deduplication, error taxonomy, FR/AC
numbering); the second was an implementation-readiness pass
(deterministic normalization, the two-stage vendor-validation boundary,
atomic incident deduplication via a PostgreSQL partial unique index, the
exact Slice 1 endpoint list and `POST` response shape, identifier-format
rules, the Cisco parser-failure contract, log-emission-after-commit
semantics) — and was approved. Day 2 scaffolded the backend: package
structure, a FastAPI app with `GET /health`, Docker Compose with
PostgreSQL, Alembic wiring, pinned dependencies, and CI.

Day 3A adds framework-independent domain foundations and a representative
Cisco IOS-XE adapter (`NormalizedConfiguration` and friends,
`VendorConfigAdapter`, `AdapterRegistry`, `CiscoAdapter`), test-first,
with 29 unit tests. A follow-up correctness patch fixed an ACL
auto-sequence collision bug, made malformed-but-recognized BGP neighbor
lines return structured `ParseError`s instead of being silently dropped,
and documented the deferral of `routing.static_routes`. Small,
explicitly-flagged documentation corrections were approved and applied
alongside this work: adding `description` to the normalized interface
model, splitting "invalid interface IP address or subnet mask" into two
separate parser failures, and adding the two new BGP parser-failure
categories — all in architecture.md and test-strategy.md. See "Getting
Started" above and "Current Day 3A scope" for exactly what is and is not
implemented at that point. Persistence and the ingestion API have not
started.

**Day 3B — Configuration Policy Domain and Deterministic Evaluation.**
Adds `ConfigurationPolicy`/`RequiredAclRule`, `ConfigurationViolation`/
`AclAssignmentEvidence`, and the deterministic `PolicyEvaluator`, all
framework-independent, test-first, bringing the suite to 60 tests. See
"Current Day 3B scope" (superseded by "Current Day 4A scope" above) for
what was added.

**Day 4A — Incident Domain, Deterministic Fingerprinting, and
IncidentFactory.** Adds `IncidentSource`/`IncidentStatus`,
`IncidentCandidate`/`PolicyViolationIncidentEvidence`,
`IncidentFactory.build_candidate`, and `compute_fingerprint`, all
framework-independent, test-first, bringing the suite to 93 tests.
Resolved two documentation conflicts discovered during planning (see
CLAUDE.md "Documentation corrections applied for Day 4A"): `recommendation`
is a verbatim-copied plain string, not a templated value object, and
`affected_resource` uses one format end-to-end rather than two. See
"Current Day 4A scope" above for exactly what is and is not implemented.
Persistence, deduplication enforcement, and the ingestion API have not
started.

**Day 4B1 — Persistence Foundations.** The first of three reviewable
gates for Day 4B ("Slice 1 persistence foundations"): persisted `Device`/
`ConfigurationSnapshot`/`Incident` domain shapes and
`IncidentUpsertOutcome`/`IncidentUpsertResult`, `compute_raw_text_hash`,
the `DeviceRepository`/`ConfigurationSnapshotRepository`/
`ConfigurationPolicyRepository`/`IncidentRepository`/`UnitOfWork`
Protocols (interfaces only), explicit JSON serialization for
`NormalizedConfiguration`/`RequiredAclRule` tuples/
`PolicyViolationIncidentEvidence`, private SQLAlchemy declarative ORM
models, and the first Slice 1 Alembic migration (tables, the two-stage
device/snapshot foreign keys, CHECK constraints, and the partial unique
OPEN-fingerprint index), test-first, bringing the suite to 135 tests (128
fast + 7 against real PostgreSQL). Two field-name corrections were applied
during planning (see CLAUDE.md "Documentation corrections applied for Day
4B1"): `Device.first_seen_at`/`last_seen_at` renamed to `created_at`/
`updated_at`, and `ConfigurationSnapshot.raw_text`/`raw_source_hash`
renamed to `raw_config_text`/`raw_text_hash`; `IncidentRepository.
find_open_by_fingerprint` was also dropped from the documented port
surface. See "Current Day 4B1 scope" above for exactly what is and is not
implemented — no repository implementation, seeding, atomic upsert, or
concrete UnitOfWork exists yet; those are Day 4B2 and Day 4B3.

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

### Current Day 4B2 scope

The second of Day 4B's three reviewable gates: concrete `Device`/
`ConfigurationSnapshot`/`ConfigurationPolicy` repositories, both
SQLAlchemy/PostgreSQL and in-memory. No `IncidentRepository`, atomic
upsert, or `UnitOfWork` yet.

Day 4B2 adds:

- **Persistence error hierarchy** (`meta_rne.persistence.errors`):
  `PersistenceError` → `PersistenceConflictError` →
  `DeviceConflictError`/`SnapshotAlreadyExistsError`/
  `PolicySeedConflictError`/`ReferencedDeviceNotFoundError`. Repositories
  never leak a SQLAlchemy `IntegrityError`, psycopg exception, or
  constraint name; an unrecognized infrastructure failure is raised as the
  base `PersistenceError` (not a conflict subtype), so callers can tell a
  business-rule rejection apart from an unexpected failure.
- **`DeviceRepository`** (`get_by_id`/`save`) — `save` is upsert-by-
  `device_id`, but validates every transition *before* mutating anything:
  vendor change, `created_at` change, `updated_at` regression, replacing a
  set `baseline_snapshot_id`, clearing a set `current_snapshot_id`, or a
  non-null snapshot reference that doesn't exist all raise
  `DeviceConflictError` and leave the stored `Device` completely
  unchanged — no silent preservation.
- **`ConfigurationSnapshotRepository`** (`get_by_id`/`add`) — append-only;
  a duplicate `snapshot_id` raises `SnapshotAlreadyExistsError`, an
  unknown `device_id` raises `ReferencedDeviceNotFoundError`. The
  SQLAlchemy side distinguishes the two by inspecting the PostgreSQL
  SQLSTATE of a translated `IntegrityError` recovered via a SAVEPOINT
  (`session.begin_nested()`) — the caller's outer transaction, and the
  Session itself, remain fully usable afterward.
- **`ConfigurationPolicyRepository`** (`get_applicable_to_device`/
  `seed_if_missing`) — exact `applies_to == device_id` matching only.
  `seed_if_missing` treats one call as one all-or-nothing operation:
  semantic equivalence compares only `applies_to`/`required_acls`
  (`created_at` is insertion metadata, never compared or overwritten);
  identical semantic content is a no-op, differing content raises
  `PolicySeedConflictError`, and a conflict anywhere in a multi-policy
  batch leaves no partial subset inserted from that call.
- **In-memory conformance-test doubles** (`meta_rne.persistence.memory`)
  sharing one `InMemoryStore` (`meta_rne.persistence.memory.store`) across
  all three repositories, so cross-repository reference integrity
  (a snapshot's `device_id` must exist; a device's snapshot references
  must exist) is enforced the same way PostgreSQL's foreign keys enforce
  it — never merely storing arbitrary strings.
- **Pure Slice 1 seed builder** (`meta_rne.persistence.seeds.
  build_slice1_policies`) — no clock read, no Session, no persistence;
  returns the one approved `policy-acl-external-in` policy for a given
  `created_at`.
- **Timestamp normalization at the conversion boundary** — every
  SQLAlchemy repository converts `TIMESTAMPTZ` values via
  `.astimezone(UTC)` before constructing a domain object, so returned
  timestamps are correct regardless of the database session's timezone.
- **Repository conformance tests** (`tests/contract/persistence/`) run
  every behavior against both implementations via one parameterized
  `repositories` fixture; PostgreSQL-only tests (CHECK-constraint
  bypass-the-repository proofs, Session-reusability-after-conflict, and
  non-UTC-timezone conversion) live in `tests/integration/persistence/`.

Backend test count at this gate: **227** (`pytest`; 175 run via `pytest -m
"not postgres"`, 52 via `pytest -m postgres` against a real PostgreSQL
instance).

### Current Day 4B3 scope

The third and final gate of Day 4B ("Slice 1 persistence foundations"):
the concrete `IncidentRepository` (atomic `upsert_open_incident`) and the
concrete `UnitOfWork`, both SQLAlchemy/PostgreSQL and in-memory. Day 4B is
now complete end to end.

Day 4B3 adds:

- **`IncidentRepository`** (`get_by_id`/`list_all`/`upsert_open_incident`)
  — `upsert_open_incident` is one atomic PostgreSQL statement, never a
  read-before-write: `INSERT ... ON CONFLICT (fingerprint) WHERE status =
  'OPEN' DO UPDATE SET last_seen_at = excluded.last_seen_at, occurrence_count
  = incidents.occurrence_count + 1, severity = excluded.severity, evidence =
  excluded.evidence, recommendation = excluded.recommendation WHERE
  excluded.last_seen_at >= incidents.last_seen_at RETURNING <explicit
  columns>, (xmax = 0) AS was_inserted`. The `DO UPDATE SET` list never
  touches `incident_id`/`fingerprint`/`device_id`/`source`/`rule_ref`/
  `affected_resource`/`status`/`created_at`. The `WHERE excluded.
  last_seen_at >= incidents.last_seen_at` guard makes a stale observation
  (older than the stored row's `last_seen_at`) affect no row at all — equal
  timestamps still pass and still increment `occurrence_count`. When that
  guard suppresses the update, the repository issues exactly one internal,
  non-public follow-up `SELECT` (never a `find_open_by_fingerprint` port
  method) to distinguish a genuine stale observation (`ValueError`, no
  mutation) from an unexpected empty result (`PersistenceError`).
- **`CREATED`/`UPDATED` outcome detection** via a private `xmax = 0` check
  inside `SqlAlchemyIncidentRepository` only — the RETURNING clause names
  explicit columns plus a labeled `was_inserted` expression, never the
  whole ORM row or the raw `xmax` value; `ConfigIngestionService` (Day 5+)
  will never infer the outcome from `occurrence_count`.
- **Injectable incident-ID generation** (`meta_rne.persistence.incident_id.
  default_incident_id_factory`, `str(uuid4())`) — both repositories accept
  an `incident_id_factory: Callable[[], str]` constructor argument; no
  repository calls `uuid4` directly, and `incident_id` is never derived
  from the fingerprint. The SQLAlchemy side generates one ID per call
  before the statement executes (an upsert that loses the insert race may
  generate an unused ID — acceptable); the in-memory side generates one
  only after determining no OPEN incident exists for the fingerprint. An
  update always preserves the existing row's `incident_id`.
- **Validation before mutation** (`meta_rne.persistence.
  incident_validation`, shared by both implementations): a `fingerprint`
  inconsistent with `compute_fingerprint(candidate.device_id, candidate.
  source, candidate.rule_ref, candidate.affected_resource)`, an
  `observed_at` inconsistent with `candidate.observed_at`, an unsupported
  `candidate.source` (only `POLICY_VIOLATION` evidence is serializable this
  phase), or an empty/whitespace-only generated ID all raise `ValueError`
  before any row is touched.
- **Unknown-`device_id` translation** — a referenced `Device` that doesn't
  exist raises `ReferencedDeviceNotFoundError`, translated from the
  `incidents.device_id` foreign-key violation inside a SAVEPOINT
  (`session.begin_nested()`), the same pattern
  `ConfigurationSnapshotRepository.add` already uses — the caller's Session
  remains fully usable afterward. The in-memory side checks Device
  existence inside the same lock guarding the rest of the upsert.
- **In-memory atomicity** — the whole find-OPEN-by-fingerprint -> decide ->
  mutate sequence (including the Device-existence check) runs inside one
  `threading.Lock` on the shared `InMemoryStore`, proven under real thread
  interleaving by a four-worker concurrency test alongside the equivalent
  real-PostgreSQL test (both assert exactly one `CREATED` outcome, every
  other successful call `UPDATED`, one persisted `incident_id`, one `OPEN`
  row, and `occurrence_count` equal to the number of successful workers).
- **`SqlAlchemyUnitOfWork`** (`session_factory: Callable[[], Session]`,
  never an already-created `Session`) — creates exactly one `Session`
  shared by all four repositories; `commit()` calls the real `Session.
  commit()`, rolling back and re-raising the original exception unchanged
  on failure (never swallowed or replaced); `rollback()`/`close()`
  delegate directly to the `Session`. No context-manager syntax yet.
- **`InMemoryUnitOfWork`** — an isolated *working* `InMemoryStore` (with
  fresh lock instances, never the committed store's locks) copied from a
  shared *committed* `InMemoryStore` at construction time; `commit()`
  publishes all four collections into the committed store at once under
  its own lock; `rollback()` discards the working store's changes and
  publishes nothing; `close()` performs no I/O. A fresh `UnitOfWork`
  against the same committed store sees exactly what was committed.
- **UnitOfWork contract tests** — one shared suite (parameterized over
  both implementations) proves all four repositories are available, one
  transaction can stage a `Device` with null references, a
  `ConfigurationSnapshot`, the `Device` updated with references to it, a
  `ConfigurationPolicy`, and an `Incident`; `commit()` publishes all of it,
  `rollback()`/`close()`-without-`commit()` publish none of it, and a fresh
  `UnitOfWork` sees committed (never rolled-back) state. Implementation-
  specific behavior (real commit-failure re-raise, Session-sharing across
  repositories, isolation between two simultaneously-open in-memory
  UnitOfWorks) is covered by dedicated SQLAlchemy/in-memory test files.

Backend test count: **311** (`pytest`; 214 run via `pytest -m "not
postgres"`, 97 via `pytest -m postgres` against a real PostgreSQL
instance).

**Not implemented yet** (deliberately — Day 5 and later): `ConfigIngestionService`,
FastAPI ingestion endpoints, request/response schemas, seed execution
during application startup, incident acknowledgment/resolution commands,
structured logging, `DriftDetector`, `RuleEngine`, telemetry, and the React
dashboard.

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

**Day 4B2 — Device, ConfigurationSnapshot, and ConfigurationPolicy
Repositories.** The second of Day 4B's three reviewable gates: concrete
`DeviceRepository`/`ConfigurationSnapshotRepository`/
`ConfigurationPolicyRepository` implementations (SQLAlchemy/PostgreSQL and
in-memory, sharing one `InMemoryStore`), the persistence error hierarchy
(`PersistenceError`/`PersistenceConflictError`/`DeviceConflictError`/
`SnapshotAlreadyExistsError`/`PolicySeedConflictError`/
`ReferencedDeviceNotFoundError`), and the pure Slice 1 seed builder
(`build_slice1_policies`), test-first, bringing the suite to 227 tests
(175 fast + 52 against real PostgreSQL). Stale documentation discovered
during implementation was corrected (see CLAUDE.md "Documentation
corrections applied for Day 4B2"): domain-model.md §12 and
architecture.md §11.1 still described a fuller repository surface
(`DeviceRepository.list()`, `ConfigurationSnapshotRepository.
get_current_for_device`/`get_baseline_for_device`, `"*"` wildcard policy
matching) than what Day 4B1's `domain/ports.py` actually approved — now
corrected to match. See "Current Day 4B2 scope" above for exactly what is
and is not implemented — no `IncidentRepository`, atomic upsert, or
concrete `UnitOfWork` exists yet; those are Day 4B3.

**Day 4B3 — IncidentRepository and UnitOfWork.** The third and final gate
of Day 4B: concrete `IncidentRepository` implementations (SQLAlchemy/
PostgreSQL and in-memory) with the atomic `INSERT ... ON CONFLICT ... DO
UPDATE ... WHERE excluded.last_seen_at >= incidents.last_seen_at
RETURNING ...` upsert, private `xmax`-based `CREATED`/`UPDATED` detection,
an injectable `incident_id_factory`, shared caller-consistency validation,
`ReferencedDeviceNotFoundError` translation via SAVEPOINT, and concrete
`SqlAlchemyUnitOfWork`/`InMemoryUnitOfWork` implementations, test-first,
bringing the suite to 311 tests (214 fast + 97 against real PostgreSQL,
including dedicated four-worker concurrency tests for both the real
PostgreSQL and in-memory implementations). Stale documentation discovered
during implementation was corrected (see CLAUDE.md "Documentation
corrections applied for Day 4B3"): domain-model.md §12 and
architecture.md §11.1 still described a filtered `IncidentRepository.
list(filter)` that `domain/ports.py` never actually declared (it has
declared `list_all()` with no filter since Day 4B1) — now corrected, with
`list_all()`'s `created_at`-then-`incident_id` ordering documented; the
`ON CONFLICT ... DO UPDATE` statement's field list and the missing stale-
observation guard were also brought in line with what was actually built.
See "Current Day 4B3 scope" above for exactly what is and is not
implemented — no `ConfigIngestionService`, API endpoints, or startup
seeding exists yet; those are Day 5.

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
| Frontend | React, TypeScript, Vite (Day 6B — `frontend/`) |
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

### Compose smoke validation (Day 6A)

A repeatable, isolated smoke run that proves the stack above actually
works end to end in its real deployed shape — real build, real migration-
before-Uvicorn ordering, real policy seeding, real HTTP traffic, and real
state persistence across an API restart:

```bash
python scripts/compose_smoke.py \
  --project-name meta-rne-smoke \
  --api-port 58080 \
  --db-port 55432
```

Uses its own Compose project name and non-default host ports, so it never
collides with a native PostgreSQL service on port 5432 or a developer's
own `docker compose up` session, and always cleans up after itself
(`--keep` to skip cleanup for post-mortem inspection). Python standard
library only; the same invocation runs in CI's `compose-smoke` job. See
`docs/architecture.md` Section 15 for the full flow.

### CORS (Day 6A)

Disabled by default. To enable for a local frontend dev server, set
`META_RNE_CORS_ALLOWED_ORIGINS` (comma-separated origins) before starting
the API — `docker-compose.yml` already defaults it to
`http://localhost:5173` (the Vite dev server origin) for local Compose
development. See [docs/frontend-api-contract.md](./docs/frontend-api-contract.md)
for the full frontend-facing contract.

### Frontend (Day 6C)

A React/TypeScript/Vite dashboard lives at `frontend/`. It now supports two
frontend business operations: viewing the current incidents list
(`GET /incidents`, Day 6B) and submitting one Cisco IOS-XE device
configuration (`POST /devices/{device_id}/config`, Day 6C). Requires
Node.js (verified locally against **Node v24.11.1, npm 11.6.2**).

```bash
cd frontend
npm ci
npm run dev        # starts the Vite dev server at http://localhost:5173
```

Copy `frontend/.env.example` to `frontend/.env` to override the backend base
URL for local development:

```
VITE_API_BASE_URL=http://localhost:8080
```

Defaults to `http://localhost:8080` (the Compose `api` service's default
host port) when unset. `docker-compose.yml`'s `META_RNE_CORS_ALLOWED_ORIGINS`
default (`http://localhost:5173`) already matches Vite's default dev port,
so `docker compose up -d` (backend) + `npm run dev` (frontend) work together
with no extra configuration.

The dashboard (`IncidentDashboard`) renders four incident-section states —
loading, empty (`GET /incidents` returns `[]`), a controlled error state
(with a Retry action) on request failure, and the populated incident list, in
the exact order the backend returns them, each incident exposing its
policy-violation evidence (including `fingerprint`) in a keyboard-accessible
`<details>` region. No incident mutation, filtering, pagination, sorting,
authentication, or routing is implemented. Incident acknowledgment/
resolution remains deferred to a later day. This exact dashboard and form —
served as a real production build, not the dev server — is what Day 6D's
Playwright browser test drives end to end; see
["Current Day 6D scope"](#current-day-6d-scope) below.

### Current Day 6C scope

`ConfigurationSubmissionForm` is now rendered inside `IncidentDashboard`,
above the incident-list content, and remains visible and usable regardless
of the incident section's own state (loading/empty/populated/error/
refreshing).

**Form.** Three controlled inputs: a Device ID text input (treated as an
opaque string — checked for blankness via `deviceId.trim().length === 0`,
but the value actually sent is never trimmed or otherwise rewritten), a
Vendor `<select>` that is enabled but has exactly one option (`cisco-ios-xe`
/ "Cisco IOS-XE" — the only vendor currently registered on the backend, with
no placeholder or future-vendor value implied), and a Raw configuration
`<textarea>` (rejected locally only when `rawConfigText.length === 0`;
whitespace-only text is allowed, and the value is never trimmed, normalized,
or line-ending-rewritten).

**Request.** On valid local submission, exactly one
`POST /devices/{encodeURIComponent(deviceId)}/config` is issued with a body
containing exactly `{"vendor": "cisco-ios-xe", "raw_config_text": <exact
textarea value>}` — never `device_id` (the path segment is authoritative)
and never `observed_at` (always server-generated). `device_id` is
URL-encoded as a single path segment, never parsed or trimmed.

**Lifecycle.** idle → submitting → success/error, mirroring the pattern
`useIncidents` already established: one `AbortController` per submission
paired with a monotonically increasing request ID, so a superseded or
stale-resolving request can never overwrite newer state, `AbortError` never
becomes a visible error, and unmounting aborts the active request. While
submitting, the submit button is natively `disabled` (plus a defensive
`onSubmit` guard, and `aria-busy` on the `<form>`) so a second click cannot
create a duplicate POST; a visible `role="status"` "Submitting
configuration…" message communicates the pending state accessibly.

**Errors.** A malformed 2xx response is rejected the same way a malformed
`GET /incidents` response is — via `isConfigurationSubmissionResponse`'s
full structural validation of the response, including every nested
`normalized_config` field (`interfaces[]`, `routing.bgp_neighbors[]`,
`acls[].entries[]`) — never a bare type cast. Failure responses render only
controlled, safe text inside a `role="alert"` region: `ApiErrorResponse`'s
`detail`/`code`, or — for FastAPI's own `{"detail": [...]}` request-
validation body — one stable safe message (the array, field locations, and
rejected input are never rendered), or — for a malformed/non-JSON/HTML body
or a network failure — the same kind of stable fallback message
`GET /incidents` already uses. No raw HTML, stack trace, or server body is
ever rendered, and `dangerouslySetInnerHTML` is never used.

**Success.** On a validated `201`, the form displays `device_id`,
`snapshot_id`, `violations_detected`, `incidents_created`, and
`incidents_updated` as visible text inside a `role="status"` region, plus
the complete `normalized_config` inside a semantic `<details>`/`<summary>`,
rendered as indented JSON text (`JSON.stringify`, relying on React's default
text escaping — never `dangerouslySetInnerHTML`). Entered form values remain
present after success or error, so another submission can be made without
retyping.

**Incident refresh.** A successful submission triggers the dashboard's
existing incident refresh exactly once — `IncidentDashboard` is still the
sole owner of `useIncidents()`; `ConfigurationSubmissionForm` never touches
incident state directly, and no polling or second data-fetching hook was
added. The triggered refresh inherits every existing `useIncidents`
guarantee unchanged (previous cards stay visible, the Refresh button is
natively disabled, stale results can't overwrite newer ones, abort works the
same way). A failed submission or a local validation rejection triggers zero
refreshes. If the triggered refresh itself fails, the incident section shows
its own controlled error state completely independently — the already-
successful submission result is never rewritten into a failure, never
retried, and never followed by a second automatic `GET`.

The backend contract itself is unchanged by Day 6C — see
[docs/frontend-api-contract.md](./docs/frontend-api-contract.md).

**Response validation.** `GET /incidents`'s parsed JSON is never trusted via
a bare type cast: `src/api/types.ts`'s `isIncidentResponse`/
`isPolicyViolationIncidentEvidenceResponse` runtime guards check every
returned array element structurally (required string fields non-empty,
`occurrence_count` a non-negative integer, nested evidence shape) before the
data reaches the dashboard; a malformed element rejects the whole response
into the controlled error state rather than rendering partial/incorrect
data. Severity/status/source/violation_type/direction are checked as
non-empty strings only, never against a closed enum — an unrecognized
future backend value is preserved and rendered as plain text instead of
failing the page.

**Refresh behavior.** Clicking Refresh (or Retry, after an error) keeps
previously loaded incident cards visible and marks the Refresh button
natively `disabled` (plus `aria-busy` on the list and an
`aria-live="polite"` "Refreshing incidents…" status) for the duration of the
new request — it does not drop back to a full loading screen, and the
native `disabled` attribute means a second click cannot start an
overlapping request. Internally, `src/hooks/useIncidents.ts` pairs one
`AbortController` per request with a monotonically increasing request ID:
whichever guard fires first, a stale request's late success or late
failure can never overwrite a newer result, and a request superseded by a
newer one never surfaces as a user-visible error.

Frontend verification, from `frontend/`:

```bash
npm ci
npm run format:check   # prettier --check .
npm run lint            # eslint .
npm run typecheck       # tsc -b
npm test -- --run       # vitest run (non-watch)
npm run build            # tsc -b && vite build
```

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

### Current Day 5A scope

The first application-layer use case: `ConfigIngestionService`, orchestrating
every existing Day 3A–4B3 component (adapter registry, `PolicyEvaluator`,
`IncidentFactory`, all four repositories) across exactly one `UnitOfWork`
transaction per call. No HTTP layer yet — `ConfigIngestionService` is called
directly by tests; Day 5B wires it behind `POST /devices/{device_id}/config`.

Day 5A adds:

- **`IngestConfigurationCommand`/`ConfigIngestionResult`**
  (`meta_rne.application.models`) — the command carries `vendor: str`
  exactly as an external caller would provide it (never `VendorType`), so an
  unsupported vendor stays representable before registry resolution; the
  result (`device_id`, `snapshot_id`, `normalized_config`,
  `violations_detected`, `incidents_created`, `incidents_updated`) matches
  the vertical-slice response shape above verbatim and validates
  `incidents_created + incidents_updated == violations_detected` at
  construction.
- **`ConfigurationParseError`** (`meta_rne.application.errors`) — the one
  narrow application error this phase; wraps and preserves the adapter's
  `ParseError` value verbatim on `.parse_error` when `adapter.parse()`
  returns one, since Day 5A has no normalized configuration to persist or
  return in that case. No broader `ConfigIngestionError` superclass was
  introduced without a second error needing it.
- **Injectable snapshot-ID generation**
  (`meta_rne.application.snapshot_id.default_snapshot_id_factory`,
  `str(uuid4())`) — `ConfigurationSnapshotRepository.add` never generates
  its own ID (unlike `IncidentRepository`'s `incident_id_factory`), so the
  service generates and validates (non-empty, non-whitespace) exactly one ID
  per successfully normalized command, before any `UnitOfWork` exists.
- **A pre-transaction boundary** — command validation, adapter resolution,
  `adapter.parse()` (exactly once), canonical `VendorType` derivation from
  `adapter.vendor_id` (never trusting the caller's raw string as a domain
  vendor), and snapshot-ID generation/validation all happen before any
  `UnitOfWork` is constructed. An unsupported vendor, a parse failure, or an
  invalid generated ID therefore creates zero UnitOfWorks — proven directly
  by asserting the injected `unit_of_work_factory` was never called, not by
  asserting `close()` was skipped.
- **One `UnitOfWork` per successful call** — new-device staging follows the
  existing two-stage save (null references → add snapshot → save with
  `current_snapshot_id == baseline_snapshot_id == snapshot_id`); an existing
  device is explicitly reconstructed with the *this call's* canonical
  vendor (never silently preserving the stored vendor), the new
  `current_snapshot_id`, and the existing `baseline_snapshot_id`/
  `created_at` — so a vendor change surfaces as `DeviceConflictError` from
  `DeviceRepository.save` itself, after the snapshot has already been
  staged, relying on the whole-transaction rollback rather than a
  duplicate service-level check.
- **Policy evaluation and incident upsert inside the same transaction** —
  `get_applicable_to_device` → `PolicyEvaluator.evaluate` → per violation,
  `IncidentFactory.build_candidate` → `compute_fingerprint` →
  `upsert_open_incident`, counting `CREATED`/`UPDATED` outcomes. Both
  `PolicyEvaluator.evaluate` and `IncidentFactory.build_candidate` are
  accepted as injected, narrow, pure collaborators with the real functions
  as production defaults — used only to prove late-failure rollback
  deterministically in tests, never to add a new abstraction layer. The
  service never constructs an `Incident` domain object directly and never
  seeds policies itself.
- **Exception-preserving lifecycle handling** — on any exception after the
  `UnitOfWork` is constructed, the service attempts `rollback()` then
  `close()` exactly once each and re-raises the *original* exception
  unchanged; a rollback or close failure during error handling is recorded
  as an exception note (`add_note`) rather than replacing the original
  exception. On success, `close()` runs once after `commit()` and any
  failure there is allowed to propagate normally (no swallowed exception on
  any path, `close()` never called twice).

Backend test count: **370** (`pytest`; 270 run via `pytest -m "not
postgres"`, 100 via `pytest -m postgres` against a real PostgreSQL
instance, including three focused `ConfigIngestionService` tests proving
atomic multi-table commit and atomic multi-table rollback after a forced
late failure against a real transaction).

**Not implemented yet** (deliberately — Day 5B and later): FastAPI
ingestion endpoints, request/response schemas, dependency-injection wiring
in `api/app.py`, seed execution during application startup, incident
acknowledgment/resolution commands, structured logging, `DriftDetector`,
`RuleEngine`, telemetry, and the React dashboard.

### Current Day 5B scope

The first vertical slice is runnable end to end over real HTTP:
`POST /devices/{device_id}/config` and `GET /incidents`, backed by real
PostgreSQL in production (`docker compose up`, then `curl` — see below) or
real in-memory repositories in tests.

Day 5B adds:

- **`POST /devices/{device_id}/config`** — path `device_id` is
  authoritative (never read from the body); `SubmitConfigurationRequest{vendor,
  raw_config_text}` (`ConfigDict(extra="forbid")`, so a `device_id` or
  `observed_at` in the body is a 422, not silently ignored) is exactly the
  request; `SubmitConfigurationResponse` (`device_id`, `snapshot_id`,
  `normalized_config`, `violations_detected`, `incidents_created`,
  `incidents_updated`) is exactly the `201` response — **no success
  envelope**, the body is the resource itself.
- **`GET /incidents`** — returns `list[IncidentResponse]` directly, no
  envelope, no filter/pagination/sort query parameters. `IncidentResponse`
  includes `fingerprint` (not treated as internal-only at the API layer)
  and a narrowly-typed `PolicyViolationIncidentEvidenceResponse` (the only
  evidence shape any current `IncidentSource` produces). Backed by
  `ListIncidentsService` (`meta_rne.application.incident_queries`) — one
  `UnitOfWork` per call, `list_all()` exactly once, never `commit()`s,
  same exception-preserving rollback/close lifecycle as
  `ConfigIngestionService`.
- **Explicit `api/schemas.py`** — every normalized-configuration and
  incident field is serialized via an explicit `from_domain` classmethod,
  never `ConfigDict(from_attributes=True)` auto-copying a domain
  dataclass; no field is invented that the current domain type doesn't
  actually have (`NormalizedRouting` has no `static_routes` yet, so the
  response has none either — matches domain-model.md exactly, not an
  older planning-doc example).
- **Corrected HTTP error mapping** (`api/errors.py`) — `code` is lowercase
  snake_case, the message field is `detail` (not `message`), and there is
  no success/error envelope. `UnsupportedVendorError`/
  `ConfigurationParseError` → 422 (`unsupported_vendor`/
  `configuration_parse_error`, the latter carrying the real
  `ParseError.message`/`.line_number`, never a stack trace or the full
  submitted config); `DeviceConflictError`/`SnapshotAlreadyExistsError`/
  `ReferencedDeviceNotFoundError` → 409; any other caller/application
  `ValueError` → 422 (`invalid_request`); `PersistenceError`/
  `SerializationError` → 500 with a generic public detail (registered
  after the specific 409 conflict subclasses); request-schema validation
  keeps FastAPI's own default 422 body untouched; an invalid injected
  clock (`InvalidClockError`) and any other unmapped exception get **no**
  custom handler — both fall through to FastAPI's normal production 500
  behavior rather than a broad catch-all that would echo exception
  internals.
- **One server-generated `observed_at` per `POST` request**
  (`meta_rne.api.clock.utc_now`, injected) — called exactly once, validated
  UTC-aware before `IngestConfigurationCommand` is constructed;
  `GET /incidents` never calls the clock.
- **`create_app(...)`** (`meta_rne.api.app`) — a controlled composition
  factory, not an import-time side effect: importing `api.app` creates no
  SQLAlchemy engine/`Session` and needs no `DATABASE_URL` set. Production
  engine construction is lazy (first actual request or lifespan startup,
  whichever comes first) and the engine is disposed on shutdown. Every
  request gets its own `UnitOfWork`/`Session`. The module-level
  `app = create_app()` (unchanged import path, for Uvicorn) is the only
  thing tests never touch — every test builds its own isolated
  `create_app(...)` instance directly, never `app.dependency_overrides`.
- **Idempotent Slice 1 policy seeding at startup** — a FastAPI `lifespan`
  hook (`seed_on_startup=True` in production) calls
  `build_slice1_policies` + `seed_if_missing` inside one `UnitOfWork`,
  after Alembic migrations have already completed (Docker `CMD` still
  runs `alembic upgrade head` before Uvicorn starts — unchanged;
  architecture.md Section 11.2). A semantic seed conflict
  (`PolicySeedConflictError`) fails application startup rather than being
  silently absorbed.
- **Production `AdapterRegistry`** — exactly one registry containing
  `CiscoAdapter`; no vendor resolution or parsing happens inside a route.

**Run the vertical slice locally:**

```
docker compose up --build
curl -X POST http://localhost:8080/devices/spine-01/config \
  -H 'Content-Type: application/json' \
  -d '{"vendor": "cisco-ios-xe", "raw_config_text": "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"}'
curl http://localhost:8080/incidents
```

Backend test count: **438** (`pytest`; 328 run via `pytest -m "not
postgres"`, 110 via `pytest -m postgres` against a real PostgreSQL
instance, including ten focused API-level PostgreSQL tests proving
startup-seed idempotency/conflict, `POST`/`GET` atomicity, independent
`POST`/`GET` Sessions, and the real lazy-`DATABASE_URL` production
composition path).

**Deferred identity hardening.** `device_id` (caller-supplied path
segment) and `snapshot_id`/`incident_id`/`policy_id` (opaque strings) have
no format/length constraint beyond non-empty — no UUID-shape validation,
no reserved-character stripping, no path-traversal-style hardening beyond
what FastAPI's path-parameter parsing already provides. Acceptable for a
single-tenant prototype with no authentication (product-spec.md Section 7);
would need revisiting before any multi-tenant or externally-facing
deployment.

**Not implemented yet** (deliberately — Day 6 and later): incident
acknowledgment/resolution commands, authentication/authorization,
filtering/pagination/sorting query parameters, `DriftDetector`,
`RuleEngine`, telemetry, structured logging beyond FastAPI's own request
logging, the React dashboard, Playwright, and browser end-to-end tests.

### Current Day 6A scope

Proves the completed Slice 1 vertical works in its actual deployed Docker
Compose shape, and stabilizes the frontend-facing HTTP/OpenAPI contract
before a React app is built. No Slice 1 business behavior changed.

Day 6A adds:

- **Stable OpenAPI operation IDs and documented error responses**
  (`meta_rne.api.routes`) — `health_check`, `submit_device_configuration`,
  `list_incidents`; `POST /devices/{device_id}/config` and
  `GET /incidents` now document their real `409`/`422`/`500` bodies
  (`ApiErrorResponse`, plus FastAPI's own `HTTPValidationError` for
  request-schema `422`s via an explicit `oneOf`) in `GET /openapi.json`,
  not only the previous default `201`/`200` + validation-only `422`.
- **Configurable CORS** (`meta_rne.api.cors`,
  `create_app(cors_allowed_origins=...)`) — disabled by default (empty
  tuple); the production entrypoint reads `META_RNE_CORS_ALLOWED_ORIGINS`
  (comma-separated, trimmed, empty entries ignored). No wildcard origin,
  ever; `allow_credentials=False`; `allow_methods` limited to `GET`,
  `POST`, `OPTIONS`.
- **`docker-compose.yml` host-port overrides**
  (`META_RNE_DB_HOST_PORT`/`META_RNE_API_HOST_PORT`, defaults unchanged:
  `5432`/`8080`) and a local-development CORS default
  (`META_RNE_CORS_ALLOWED_ORIGINS` defaulting to `http://localhost:5173`,
  the future Vite dev server's origin).
- **`scripts/compose_smoke.py`** — a repeatable, isolated, project-scoped
  Compose smoke runner (Python standard library only): real build, real
  migration-before-Uvicorn ordering, real policy seeding, three real HTTP
  config submissions against `spine-01` proving incident
  creation/dedup/evidence, a real API restart proving PostgreSQL-backed
  persistence with no database reset, and unconditional project-scoped
  cleanup. Run identically by a developer and by CI's new `compose-smoke`
  job.
- **OpenAPI and CORS contract tests**
  (`tests/contract/api/test_openapi_contract.py`,
  `tests/contract/api/test_cors_api.py`).
- **`docs/frontend-api-contract.md`** — the frontend-facing contract
  document: request/response shapes, the full error-code catalog, CORS
  configuration, and examples, derived from the current
  `api/schemas.py`/`api/errors.py`, not a planning aspiration.

**Not implemented yet** (deliberately — Day 6B and later): React, Vite,
any Node/npm tooling, frontend source code, the Playwright HTTP-mode E2E
suite (test-strategy.md Section 7, still separate from and not replaced
by the Day 6A Compose smoke script), browser end-to-end tests,
authentication/authorization, filtering/pagination, incident
acknowledgment/resolution, drift detection, telemetry, and any new API
endpoint.

### Current Day 6B scope

Builds the first frontend vertical slice against the Day 6A-stabilized
contract: a React/TypeScript/Vite dashboard (`frontend/`) that requests
`GET /incidents` and renders the complete request lifecycle. No backend
code, API schema, or domain/application/persistence behavior changed.

Day 6B adds:

- **`frontend/`** — a Vite + React 19 + TypeScript app (strict mode,
  `noUncheckedIndexedAccess`/`noImplicitOverride`/`noFallthroughCasesInSwitch`
  all enabled), with all direct dependencies pinned to exact versions
  (`frontend/package-lock.json`) after verifying peer-dependency
  compatibility across `vite`/`@vitejs/plugin-react`/`vitest`/`typescript`/
  `eslint`/`typescript-eslint`/`react` — notably **TypeScript is pinned to
  `6.0.3`** rather than the newer `7.0.2`, the highest release still inside
  `typescript-eslint@8.65.0`'s supported peer range (`>=4.8.4 <6.1.0`).
- **`src/api/client.ts`/`incidents.ts`/`types.ts`** — a narrow
  `fetch`-based HTTP client (`getJsonArray`), `fetchIncidents()`, and
  explicit TypeScript types (`IncidentResponse`,
  `PolicyViolationIncidentEvidenceResponse`, `ApiErrorResponse`, and enum
  unions for `severity`/`status`/`source`/`violation_type`/`direction`)
  derived directly from `docs/frontend-api-contract.md`, preserving
  `fingerprint` and every other documented field, adding none. Base URL is
  read once through a typed `import.meta.env.VITE_API_BASE_URL`
  (`src/vite-env.d.ts`), trailing slash stripped, defaulting to
  `http://localhost:8080`.
- **`src/api/types.ts`** runtime guards (`isIncidentResponse`,
  `isPolicyViolationIncidentEvidenceResponse`) — structural validation of
  every parsed array element (never a bare `as IncidentResponse[]` cast):
  required string fields non-empty, `occurrence_count` a non-negative
  integer, nested evidence shape checked; a malformed element rejects the
  whole response into the controlled error state. Severity/status/source/
  violation_type/direction are checked as non-empty strings only, never a
  closed enum, so an unrecognized future backend value still renders as
  text instead of failing the page.
- **`src/hooks/useIncidents.ts`** — the request-lifecycle state machine
  (`loading` / `success` (with `isRefreshing`) / `error`). One
  `AbortController` paired with a monotonically increasing request ID per
  request: a completion is applied only when its request ID still matches
  the current one, so a late-resolving stale success or stale failure can
  never overwrite a newer result, independent of whether the underlying
  client actually honors `AbortSignal`; a superseded request's own
  `AbortError` never surfaces as a user-visible error. Unmount aborts the
  active request and blocks any further state update. `refresh()` (used by
  both the Refresh and Retry controls) preserves the previous successful
  `data` and sets `isRefreshing: true` instead of dropping to a full
  loading screen.
- **`src/pages/IncidentDashboard.tsx`** and the state components
  (`LoadingState`, `IncidentEmptyState`, `IncidentErrorState`,
  `IncidentCard`) — the four required states (loading, empty, controlled
  error with Retry, populated), incidents rendered as responsive cards (not
  a table — chosen to avoid a duplicated desktop/mobile markup structure)
  in the exact order the backend returns them, with severity/status shown
  as text (not color-only) and evidence (including `fingerprint`) in a
  keyboard-accessible `<details>` region. The Refresh button is natively
  `disabled` (not merely `aria-disabled`) while a refresh is pending, so a
  second click cannot start an overlapping request; existing cards stay
  visible and `aria-busy`/an `aria-live="polite"` status communicate the
  pending refresh accessibly.
- **A fourth CI job, `frontend`** (`.github/workflows/ci.yml`) — Node-based,
  no PostgreSQL, no Docker: `npm ci`, `prettier --check`, `eslint`,
  `tsc -b`, `vitest run`, `vite build`. Independent of `ci`/
  `postgres-tests`/`compose-smoke`, all of which are unmodified and still
  pass.
- **75 frontend tests across 4 files** (Vitest + React Testing Library):
  21 API-client tests (`src/api/client.test.ts` — URL joining/defaulting,
  exact request options, `{code, detail}` error surfacing with the code
  preserved, malformed/empty/HTML error bodies falling back to one stable
  message, malformed 2xx JSON rejected, `AbortSignal` passthrough), 24
  runtime-parser tests (`src/api/incidents.test.ts` — valid payload,
  top-level non-array, null entries, missing/wrong-typed/negative/
  non-integer fields, missing or null evidence, each enum-like field
  rejecting an empty string and separately preserving an unrecognized
  future value, backend order preserved, opaque IDs byte-for-byte
  unchanged), 5 hook-level lifecycle tests (`src/hooks/useIncidents.test.ts`
  — abort on unmount, refresh aborts an in-flight request and starts
  exactly one new one, a late stale success/failure cannot overwrite a
  newer success, a superseded rejection never produces an error state —
  using deferred promises and direct `refresh()` calls, deliberately
  ignoring `AbortSignal` to prove the request-ID guard independently), and
  25 dashboard tests (`src/pages/IncidentDashboard.test.tsx` — loading,
  empty, populated with preserved ordering, evidence/fingerprint exposure,
  `occurrence_count === 1` rendering, severity/status as text, semantic
  `<time>` timestamps, controlled error state, retry-to-success, one click
  produces exactly one request, cards preserved while a refresh is
  pending, an accessible busy status, Refresh enabled after a successful
  load, Refresh natively `disabled` while pending, another click on the
  disabled button starting no new request, Refresh re-enabled after a
  successful refresh, a failed refresh's Retry control enabled, refresh
  success/failure outcomes, heading persistence, unmount aborts the active
  request). Deliberately overlapping requests are proven at the hook
  level only — the native `disabled` attribute makes a second overlapping
  Refresh click impossible to produce by clicking through the rendered
  dashboard.

**Not implemented yet** (deliberately — Day 6C and later): configuration
submission from the browser, incident acknowledgment/resolution, incident
mutations, filtering/pagination/client-side sorting, authentication, React
Router, any global state library, TanStack Query, a component library,
Tailwind, charts, telemetry, WebSockets/polling, Playwright, browser
end-to-end tests, a frontend Docker image or Compose service.

Backend test count: unchanged at **470** (360 non-`postgres` + 110
`postgres`-marked) — Day 6B changed no backend code.

### Current Day 6C scope

Builds the second frontend vertical slice: a configuration-submission form
(`ConfigurationSubmissionForm`) integrated into the existing
`IncidentDashboard`, POSTing to the already-existing
`POST /devices/{device_id}/config` endpoint (Day 5B/6A) and triggering
exactly one existing incident refresh on success. No backend code, API
schema, or domain/application/persistence behavior changed.

Day 6C adds, built in four reviewable gates:

- **`src/api/client.ts`** — `postJson(path, body, options)`, a POST sibling
  to `getJsonArray`, reusing the same `parseErrorDetail`/`ApiRequestError`
  machinery. A new `isFastApiValidationErrorBody` check (matched only by
  `detail` being an array, never by inspecting its contents) maps FastAPI's
  own `{"detail": [...]}` request-validation body to one new stable message
  (`VALIDATION_ERROR_MESSAGE`), so a validation-error array is never
  rendered to the user.
- **`src/api/types.ts`** — `ConfigurationSubmissionRequest`
  (`vendor: "cisco-ios-xe"` literal, `raw_config_text: string`),
  `ConfigurationSubmissionResponse` and its nested
  `NormalizedConfigurationResponse`/`NormalizedInterfaceResponse`/
  `NormalizedRoutingResponse`/`NormalizedBgpNeighborResponse`/
  `NormalizedAclResponse`/`NormalizedAclEntryResponse` types, matching
  `docs/frontend-api-contract.md` exactly (no `static_routes`, no invented
  field). A matching family of `is*` runtime structural guards
  (`isConfigurationSubmissionResponse` and its nested per-field guards)
  validates every field of a parsed `201` response — never a bare type
  cast — rejecting the whole response into a controlled error on any
  mismatch.
- **`src/api/configurations.ts`** — `submitDeviceConfiguration(deviceId,
  request, options)`: builds `/devices/${encodeURIComponent(deviceId)}/config`
  (one opaque path segment, never trimmed), constructs a **fresh** request
  body containing exactly `{vendor, raw_config_text}` (never forwarding a
  caller-supplied object directly — TypeScript's structural typing doesn't
  guarantee a runtime object has only the declared keys, so a stray
  `device_id`/`observed_at`/other property on the input can never leak into
  the serialized body), and rejects a structurally malformed `201` via
  `isConfigurationSubmissionResponse`.
- **`src/hooks/useConfigurationSubmission.ts`** — the
  idle/submitting/success(`response`)/error(`message`, optional `code`)
  submission lifecycle, following `useIncidents`'s own
  `AbortController`/monotonically-increasing-request-ID/mounted-ref pattern:
  a new `submit()` call aborts any in-flight submission and starts exactly
  one new POST; a stale completion (by request ID or unmount) can never
  update state; `AbortError` never surfaces as a visible error. An optional
  `onSuccess` callback is held in a ref synced via `useLayoutEffect` (keyed
  on the callback itself, never assigned during render, which React
  disallows for refs) so the *latest committed* callback runs — not one a
  passive effect might not have caught up to yet before an already-in-flight
  POST resolves. The callback fires exactly once per current successful
  POST, is never awaited, and neither a synchronous exception nor a rejected
  Promise it returns can turn a successful submission into an error.
- **`src/components/ConfigurationSubmissionForm.tsx`** — a standalone
  component owning only local form-input/validation state (no duplicated
  submission state), built on `useConfigurationSubmission`. Device ID and
  raw-configuration text are preserved exactly as typed (device ID blankness
  checked via `.trim().length === 0` without trimming the sent value; raw
  configuration rejected locally only when `.length === 0`, so whitespace-
  only text is allowed and never rewritten). The Vendor `<select>` is
  enabled with exactly one option (`cisco-ios-xe` / "Cisco IOS-XE"). Submit
  is natively `disabled` while submitting, backed by a defensive `onSubmit`
  guard; local validation messages use `aria-invalid`/`aria-describedby`/
  `role="alert"`; pending state uses `role="status"` text plus `aria-busy`
  on the `<form>`; the hook's error state renders only its controlled
  `message`/optional `code` (`role="alert"`, no raw JSON/HTML/stack trace,
  no `dangerouslySetInnerHTML`); the success state (`role="status"`) shows
  `device_id`/`snapshot_id`/`violations_detected`/`incidents_created`/
  `incidents_updated` plus `normalized_config` inside a semantic
  `<details>`/`<summary>` rendered as `JSON.stringify`-formatted text (React
  text escaping, never raw HTML). Entered values remain present after
  success or error.
- **`src/pages/IncidentDashboard.tsx`** — unconditionally renders
  `ConfigurationSubmissionForm` inside the existing `<main>`, above the
  incident-list content, passing `onSubmissionSuccess={() => { refresh();
  }}` — the *only* integration trigger (no effect watches submission state).
  `IncidentDashboard` remains the sole owner of `useIncidents()`; no second
  `useIncidents` instance, duplicated incident state, context/store,
  polling, or new API request function was introduced. The triggered
  refresh reuses `useIncidents`'s existing abort-and-supersede logic
  unchanged, so old-card preservation, native Refresh-button disabling,
  stale-result protection, and abort behavior all carry over automatically;
  a refresh failure that follows a successful submission produces the
  incident section's own independent controlled error state without ever
  rewriting the already-successful submission result.
- **New styles only** (`src/styles.css`) — form layout, labels/controls,
  textarea sizing, validation/error text, pending/success/result
  presentation, and the normalized-configuration preformatted region
  (including a `prefers-color-scheme: dark` override); no existing
  incident-card styling changed, no CSS framework or new asset added.
- **101 new frontend tests across 3 new files, plus additions to 2 existing
  files** (Vitest + React Testing Library): `src/api/client.test.ts` (+17,
  `postJson` request shape/headers/credentials/`AbortSignal`, `{code,
  detail}` surfacing, the new FastAPI-validation-array-to-safe-message
  mapping, malformed/HTML/empty error bodies, malformed/empty 2xx JSON),
  `src/api/configurations.test.ts` (30, path-segment encoding including
  reserved characters, exact request body — including a dedicated
  regression test proving a caller-supplied object with extra
  `device_id`/`observed_at`/arbitrary properties is never forwarded as-is —
  complete nested `normalized_config` structural validation), 22
  hook-level lifecycle/concurrency tests
  (`src/hooks/useConfigurationSubmission.test.ts` — idle/submitting/
  success/error transitions, exact `submit()` call shape, supersession,
  stale-success/stale-failure guards, `onSuccess` exactly-once semantics
  including latest-callback-wins and synchronous-exception/rejected-Promise
  isolation, unmount-before-resolution), 22 standalone form tests
  (`src/components/ConfigurationSubmissionForm.test.tsx` — labels, the
  single-option vendor select, local validation messages and their
  clearing, byte-exact device-ID/raw-config preservation, exact submit call
  shape, pending/disabled presentation, `ApiRequestError`/network-failure
  presentation, success-field rendering, `normalized_config` details/escaped
  text, `onSubmissionSuccess` exactly-once/withheld semantics, values
  surviving success), and 9 new dashboard-level integration tests
  (`src/pages/IncidentDashboard.test.tsx` — form presence across every
  incident-section state, exactly-one-refresh-on-success, no-refresh-on-
  failure/local-rejection, the refresh-failure/submission-success
  independence case, and a supersession case against a pending manual
  refresh) — focused on cross-component integration rather than repeating
  assertions the standalone hook/form tests already make.

**Not implemented yet** (deliberately — Day 6D and later): additional
vendors, vendor autodetection, file upload, configuration history, device
inventory, `GET /devices`, incident acknowledgment/resolution, incident
mutations, filtering/pagination/client-side sorting, authentication, React
Router, any global state library, TanStack Query, a component library,
Tailwind, charts, telemetry, WebSockets/polling, Playwright, browser
end-to-end tests, a frontend Docker image or Compose service.

Backend test count: unchanged at **470** (360 non-`postgres` + 110
`postgres`-marked) — Day 6C changed no backend code. Frontend test count:
**176** across 7 files (see `docs/test-strategy.md` Section 7.1 for the full
inventory).

### Current Day 6D scope

A single browser-level end-to-end test proves the Day 6C configuration-
submission → incident-refresh flow works through the system's real,
deployed shape — not through Vitest's mocked `fetch`, not through in-memory
repositories — driven by an actual Chromium browser via Playwright:

```
Chromium (Playwright)
  → real `vite preview` production build
  → real React frontend (IncidentDashboard / ConfigurationSubmissionForm)
  → real cross-origin HTTP
  → real FastAPI (POST /devices/spine-01/config, GET /incidents)
  → real, disposable PostgreSQL
```

**The exact scenario proven**, against a guaranteed-fresh database (the
backend's existing idempotent Slice 1 policy seeding, unchanged since
Day 5B): the dashboard starts with an empty incident list; submitting the
`spine-01` / `cisco-ios-xe` / missing-ACL configuration from this README's
own worked example (`hostname spine-01\n!\ninterface
GigabitEthernet0/1\n!\n`) yields a real `201` response with
`violations_detected: 1`, `incidents_created: 1`, `incidents_updated: 0`,
and a present (never literally asserted) `snapshot_id`; the submission
triggers exactly one automatic `GET /incidents` refresh (never a second
`POST`), after which the resulting incident is visible with `device_id:
"spine-01"`, `status: "OPEN"`, `severity: "Medium"`,
`rule_ref: "policy-acl-external-in"`, the affected `GigabitEthernet0/1`
interface, and `occurrence_count: 1`; reloading the page issues a third
real `GET /incidents` and the same incident is still there. Generated
UUIDs, fingerprints, timestamps, and locale-formatted dates are
deliberately never asserted as literal values.

**Isolation.** `scripts/browser_e2e.py` (Python standard library only, same
discipline as `scripts/compose_smoke.py`) reserves three host ports
simultaneously (PostgreSQL, API, frontend preview — never a bind-then-
close-then-reuse pattern), generates a unique, validated Compose project
name, starts only the existing `docker-compose.yml`'s `db`+`api` services
(no new Compose file, no frontend Docker image, no frontend Compose
service — the frontend stays uncontainerized), and computes
`META_RNE_CORS_ALLOWED_ORIGINS` and the browser's own origin from the exact
same selected frontend port, both as `http://127.0.0.1:<port>` — never
`localhost`, never a mismatch — so the real CORS check the browser performs
actually has to pass. `docker compose down --volumes --remove-orphans`
always runs in a `finally` block, followed by an independent verification
(via `com.docker.compose.project` label queries) that no container or
volume for that project remains; there is no `--keep` option and no path
that intentionally retains state.

**Run it locally**, from the repository root (requires Docker, Node, and
npm on `PATH`; Chromium must already be installed — see below):

```bash
python scripts/browser_e2e.py
```

The orchestration helpers themselves have their own fast, dependency-free
test suite:

```bash
python scripts/test_browser_e2e.py
```

Playwright's Chromium browser binary is not committed to the repository and
must be installed locally when needed:

```bash
cd frontend
npx playwright install chromium
```

**Scope.** Chromium only — no Firefox, WebKit, or mobile-device-emulation
project; one worker, zero retries; no visual-regression snapshot testing.
`frontend/playwright.config.ts` requires `PLAYWRIGHT_BASE_URL` to be set
(the config fails to load otherwise, never silently defaulting), retains a
trace/screenshot/video on failure only, and writes an HTML report only in
CI. A fifth GitHub Actions job, `browser-e2e` (independent of `ci`/
`postgres-tests`/`compose-smoke`/`frontend`, all four unchanged), runs the
orchestration helper tests before installing Chromium (so a broken helper
fails fast, before paying for the browser download), installs Chromium
only, runs `scripts/browser_e2e.py` with a deterministic per-run project
name, uploads the Playwright report/test-results as an artifact only on
failure (7-day retention), and always runs a project-scoped defense-in-depth
cleanup step.

Test count: **1** Playwright browser test (1 file), plus **19** Python
`unittest` tests for the orchestration helpers (1 file) — neither counted
as part of the 176 Vitest tests or the 470 backend `pytest` tests. Backend
test count is unchanged at 470 (360 non-`postgres` + 110 `postgres`-marked)
— Day 6D changed no backend code, no API schema, no domain/application/
persistence/migration behavior, and no React application source.

### Current Day 7A scope

The backend gains its first incident-lifecycle mutation: an operator can
explicitly resolve an existing `OPEN` incident.

```
POST /incidents/<incident-id>/resolve
```

- **No request body.** The path segment is the only input.
- **Direct response object.** A successful call returns `200 OK` with the
  complete, updated incident directly — the same shape `GET /incidents`
  already returns, never a wrapper (`{"data": ...}`).
- **`OPEN -> RESOLVED` only.** There is no acknowledgment, reopening,
  assignment, or bulk-resolution endpoint. Resolving an unknown incident ID
  returns a controlled `404`:

  ```json
  {"code": "incident_not_found", "detail": "Incident '<incident_id>' was not found."}
  ```

- **Idempotent.** Resolving an already-`RESOLVED` incident returns `200`
  with the incident unchanged — the same `resolved_at`/`updated_at` as the
  first call, not a new timestamp and not an error.

**Timestamp meanings**, now that a fourth one exists:

| Field | Meaning |
|---|---|
| `created_at` | When this incident was first created (never changes again) |
| `last_seen_at` | The most recent violation *detection* — advances only when the same finding is re-ingested while the incident is still `OPEN` |
| `updated_at` | The most recent *persisted mutation* to this row, of any kind — creation, a re-ingestion update, or an explicit resolution |
| `resolved_at` | `null` while `OPEN`; the exact moment an operator resolved the incident, once `RESOLVED` |

Resolving an incident leaves `last_seen_at` and `occurrence_count`
completely untouched — those remain the detection-side story; only
`status`, `resolved_at`, and `updated_at` change.

**Recurrence.** If the same still-invalid configuration is submitted again
*after* its incident has been resolved, the platform does not reopen the
resolved incident — it creates a **new** `OPEN` incident (same fingerprint,
new `incident_id`, `occurrence_count: 1`), and the original resolved
incident is left exactly as it was. Ordinary `OPEN` deduplication (the same
finding, still `OPEN`, updates in place rather than duplicating) is
completely unchanged by this — see `docs/domain-model.md` Section 11.

`GET /incidents` remains unfiltered: it returns both `OPEN` and `RESOLVED`
incidents, with no new query parameter to hide either.

**Backend-only.** The React dashboard does not have a resolve button or any
other way to call this endpoint yet — Day 7A is a backend vertical slice
only. A future frontend phase may add one against this same contract.

Backend test count: **571** (`pytest`; 431 non-`postgres` + 140
`postgres`-marked). Frontend (176 Vitest, 19 orchestration-helper, 1
Playwright) is unchanged — Day 7A added no frontend, Playwright, or CI
change. **767 automated tests combined.**

### Current Day 7B scope

Builds the frontend vertical slice for the endpoint Day 7A added: an
operator can now resolve an `OPEN` incident directly from the dashboard.

**Operator flow:**

1. Every incident whose `status` is exactly `OPEN` shows a "Resolve
   incident" button on its card.
2. Clicking it sends exactly one `POST /incidents/{incident_id}/resolve`
   with no request body (`Accept: application/json`, `credentials: "omit"`,
   no `Content-Type` header — there is no body to describe).
3. Only that incident's button disables and its label changes to
   "Resolving…" — every other card (including another pending resolution)
   stays fully interactive.
4. On success, the complete persisted `RESOLVED` incident the `POST`
   response returned replaces the matching card locally — status becomes
   `RESOLVED`, the button disappears, and `updated_at`/`resolved_at` render
   as new "Updated"/"Resolved" timestamp rows (the same `<time>` convention
   `Last seen` already uses).
5. **No `GET /incidents` refresh is ever performed** as a consequence of a
   resolution, success or failure — the `POST` response is already the
   authoritative persisted state.
6. A failure (including the exact `incident_not_found` `404`) renders a
   controlled `role="alert"` message inside that one card only, re-enables
   its button, and leaves the incident `OPEN` — pressing Resolve again
   retries, clearing the previous error as the new attempt starts.

**Eligibility.** The control renders only for exact `status === "OPEN"` —
`RESOLVED`, the dormant `ACKNOWLEDGED`, and any unrecognized future status
value all render no action at all (never a disabled/hidden button, simply
none).

**Concurrency.** Two different incidents can be resolved at the same time,
each with its own independent pending/error state; a same-incident double
click is suppressed synchronously (before React even commits a pending
state update), not merely by the disabled button. `useIncidents` remains
the single owner of the incident array — there is no second hook and no
duplicate list.

**Stale-response reconciliation.** Both a resolve `POST` response and a
`GET /incidents` refresh response are reconciled against whatever the
dashboard currently holds for that `incident_id`, using each side's parsed
`updated_at` instant (never string comparison) — the newer instant wins;
at an equal instant, `RESOLVED` always wins over a non-`RESOLVED` value, so
a stale `OPEN` read can never revert an already-`RESOLVED` incident.
Because `GET /incidents` is unfiltered and append-only in the current
scope, a refresh response that happens to omit a previously-seen incident
never deletes it from the dashboard — that incident is retained,
appended after the incoming response's own incidents, in its prior order.

**Unchanged.** A successful configuration submission still triggers exactly
one incident refresh, independent of any pending resolution. There is no
confirmation modal, no bulk resolution, no acknowledgment/reopening/
assignment control, and no `GET /incidents` call anywhere in the resolution
path itself. **The Day 7B-era note that the browser test doesn't exercise
resolution is superseded by Day 7C below.**

Frontend verification, from `frontend/`:

```bash
npm ci
npm run format:check
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

Verified totals as of Day 7B: **571** backend `pytest` tests (431
non-`postgres` + 140 `postgres`-marked, unchanged since Day 7A), **276**
frontend Vitest tests across 7 files, **19** Python orchestration-helper
tests, **1** Playwright browser test (still configuration-submission/
refresh only) — **867 automated tests combined**. **Superseded by Day 7C
below.**

### Current Day 7C scope

Adds the second Playwright Chromium scenario, proving the incident-
resolution vertical slice (Day 7A backend, Day 7B frontend) through a real
browser end to end. No frontend or backend product code changed — this
phase is Playwright-and-documentation-only.

**Real browser path exercised:**

1. Load the dashboard through Chromium.
2. Submit the invalid Cisco IOS-XE configuration (the same `spine-01`
   fixture the configuration-submission scenario uses) through the real,
   visible configuration form.
3. Locate the matching `OPEN` incident by stable identity
   (`device_id`/`rule_ref`/`affected_resource`) plus exact visible status
   `OPEN`.
4. Click "Resolve incident".
5. Observe the real, no-body `POST /incidents/{incident_id}/resolve`
   request (`Accept: application/json`, no `Content-Type`, `postData() ===
   null`).
6. Render the direct persisted `RESOLVED` response locally — no
   `GET /incidents` refresh occurs as a result of resolving.
7. Reload the page and confirm the `RESOLVED` state, with its `Updated`/
   `Resolved` timestamps, survived in PostgreSQL.

**Both Playwright scenarios, after Day 7C:**

- `frontend/e2e/config-submission-refresh.spec.ts` — configuration
  submission -> incident refresh -> reload persistence (unchanged
  binding behavior; see Day 6D above).
- `frontend/e2e/incident-resolution.spec.ts` (new) — establishes an `OPEN`
  incident through the UI, resolves it through the real "Resolve incident"
  control, and confirms the persisted `RESOLVED` state survives a reload.

**Isolation.** The two scenarios share one disposable PostgreSQL database
for the whole `playwright test` invocation (one Compose stack, one
orchestrated run — see Day 6D's `scripts/browser_e2e.py`, unchanged).
Neither scenario assumes execution order or the other's absence:

- The dashboard's initial state is never assumed empty — only that the
  initial `GET /incidents` succeeds.
- Neither scenario assumes the page contains exactly one `<article>`.
- Every incident card is located by stable identity fields **plus an
  exact visible lifecycle status** (`OPEN` or `RESOLVED`), so a historical
  `RESOLVED` row is always excluded when locating an `OPEN` target, and a
  current `OPEN` row is always excluded when locating a persisted
  `RESOLVED` target.
- `Occurrences` (configuration-submission scenario) is asserted as a
  positive integer, not a fixed literal, since both scenarios may submit
  against the same `spine-01`/`policy-acl-external-in` fingerprint and
  dedupe against each other's `OPEN` row.

This isolation was verified directly (not merely inferred from
`workers: 1`) across three real orchestrated runs: the resolution scenario
alone against a fresh database; the resolution scenario running before the
configuration scenario (so the configuration scenario's own submission
creates a new `OPEN` recurrence against an already-`RESOLVED` historical
incident); and the standard discovery order. All three passed with cleanup
verified.

Frontend verification, from `frontend/`:

```bash
npm ci
npm run format:check
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

Verified totals as of Day 7C: **571** backend `pytest` tests (431
non-`postgres` + 140 `postgres`-marked, unchanged since Day 7A/7B), **276**
frontend Vitest tests across 7 files (unchanged since Day 7B), **19** Python
orchestration-helper tests (unchanged), **2** Playwright browser tests (2
files: configuration-submission/refresh, and incident resolution) — **868
automated tests combined**. No third Playwright scenario exists.

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

**Day 5A — ConfigIngestionService.** The first application-layer use case:
`ConfigIngestionService` orchestrates adapter resolution/normalization,
the Device/ConfigurationSnapshot two-stage save, `PolicyEvaluator`, and
`IncidentRepository.upsert_open_incident` across exactly one `UnitOfWork`
transaction per call, test-first (the first service test was run against a
codebase with no `ConfigIngestionService` at all, producing a real
`ModuleNotFoundError`, before the service was written), bringing the suite
to 370 tests (270 fast + 100 against real PostgreSQL). No HTTP endpoint,
request/response schema, or dependency-injection wiring exists yet — the
service is called directly by tests. See "Current Day 5A scope" above for
exactly what is and is not implemented; `POST /devices/{device_id}/config`,
`GET /incidents`, FastAPI schemas, and startup policy seeding remain
Day 5B.

**Day 5B — REST API.** `POST /devices/{device_id}/config` and
`GET /incidents`, the first vertical slice's exact two endpoints, are now
runnable end to end over real HTTP against real PostgreSQL — no success
envelope (the response body is the resource itself), corrected HTTP error
mapping (422 for unsupported-vendor/parse failures, 409 for persistence
conflicts, 500 with a generic detail for persistence/serialization/
unmapped failures), a controlled `create_app(...)` composition factory
(no import-time engine/`Session`, lazy production engine construction,
disposed on shutdown), one server-generated `observed_at` per `POST`
request, and idempotent Slice 1 policy seeding via a FastAPI `lifespan`
hook that runs after Alembic migrations complete — test-first (the first
contract test was run against a codebase with no `create_app`/routes/
schemas at all, producing a real `ImportError`, before any of it was
written), bringing the suite to 438 tests (328 fast + 110 against real
PostgreSQL). Two stale documentation examples predating this phase were
corrected (see CLAUDE.md "Documentation corrections applied for Day 5B"):
architecture.md's success-envelope/400-status examples, and
domain-model.md's `GET /incidents` worked example, which had omitted
`fingerprint`. See "Current Day 5B scope" above for exactly what is and is
not implemented; incident acknowledgment/resolution, authentication,
query filtering, drift/telemetry, and the React dashboard remain Day 6 and
later.

**Day 6A — Deployment/Contract Stabilization**, **Day 6B — React Dashboard
Foundation**, and **Day 6C — Configuration Submission Frontend Vertical
Slice.** Day 6A proved the completed vertical slice in its real deployed
Docker Compose shape and stabilized the frontend-facing HTTP/OpenAPI
contract (`docs/frontend-api-contract.md`); Day 6B built the first frontend
consumer of that contract, a read-only React dashboard requesting
`GET /incidents`; Day 6C added the second frontend vertical slice, a
configuration-submission form integrated into that same dashboard, POSTing
to the already-existing `POST /devices/{device_id}/config` endpoint and
triggering exactly one existing incident refresh on success — no backend
code, API schema, or domain/application/persistence behavior changed across
any of the three. See "Current Day 6A scope"/"Current Day 6B scope"/"Current
Day 6C scope" above for exactly what is and is not implemented; backend test
count is unchanged at 470 (360 non-`postgres` + 110 `postgres`-marked) since
Day 5B, and frontend test count stands at 176 across 7 files.

**Day 6D — Browser (Playwright/Chromium) End-to-End Vertical-Slice
Validation.** Adds one browser-level Playwright test
(`frontend/e2e/config-submission-refresh.spec.ts`) proving the Day 6C
configuration-submission → incident-refresh flow through a real Chromium
browser, a real production `vite preview` build, real cross-origin HTTP, a
real FastAPI process, and a real, disposable PostgreSQL database — never a
mock, never an in-memory repository, never an intercepted/fulfilled
response — plus the isolated, cross-platform orchestration
(`scripts/browser_e2e.py`, `scripts/test_browser_e2e.py`) that makes the
run reproducible on a developer's machine and in a fifth, independent
GitHub Actions job (`browser-e2e`). No backend code, API schema,
domain/application/persistence/migration behavior, or React application
source changed — Day 6D is validation of already-approved Day 6C behavior,
not a new product feature. See "Current Day 6D scope" above for the full
detail. Test totals as of Day 6D: 176 Vitest tests (7 files), 19 Python
orchestration-helper tests (1 file), 1 Playwright browser test (1 file),
470 backend `pytest` tests (360 non-`postgres` + 110 `postgres`-marked) —
**666 automated tests combined**. Day 6D is implemented and passes the full
frontend/browser/backend verification matrix but has not yet been
committed — see CLAUDE.md's "Current Phase". Additional vendors, drift
detection, telemetry, incident acknowledgment/resolution, authentication,
Firefox/WebKit and mobile-emulation browser projects, visual-regression
snapshot testing, a frontend Docker image or Compose service, and
production/cloud deployment remain Day 6E and later.

**Day 7A — Backend Incident-Resolution Vertical Slice.** Adds the first
incident-lifecycle mutation: `POST /incidents/{incident_id}/resolve`
(`OPEN -> RESOLVED` only, no request body, a direct `IncidentResponse`,
idempotent repeated calls, an exact controlled `incident_not_found` `404`),
backed by `Incident.updated_at`/`resolved_at` and `Incident.resolve(at)`
domain invariants, a narrow atomic `IncidentRepository.resolve(...)` (both
SQLAlchemy/PostgreSQL and in-memory), Alembic revision
`0002_incident_resolution.py` (adds `updated_at`/`resolved_at`, backfills
`updated_at` from `last_seen_at`; revision 0001 untouched; no status-column
migration needed since `RESOLVED` was already a permitted value), an
explicit application-layer `Clock` protocol and `ResolveIncidentService`
(never importing `api/clock.py`), and a production `CallableClock` adapter
reusing the exact same injected clock `POST /devices/{id}/config` already
uses. Real-PostgreSQL tests prove the binding recurrence behavior:
resolving an incident and then reingesting the identical still-invalid
configuration creates a **new** `OPEN` incident rather than reopening the
resolved one, existing `OPEN` deduplication is unweakened, and concurrent
resolution attempts against one incident both return a consistent result
with no row corruption — all without any lock, retry, queue, or
isolation-level change, since the existing partial unique index
(`ux_incidents_open_fingerprint`, `WHERE status = 'OPEN'`) already excludes
a resolved row and therefore already permitted this. See "Current Day 7A
scope" above for the full detail. Test totals as of Day 7A: 176 Vitest
tests (7 files, unchanged), 19 Python orchestration-helper tests (1 file,
unchanged), 1 Playwright browser test (1 file, unchanged — it still covers
configuration submission/refresh only, not resolution), 571 backend
`pytest` tests (431 non-`postgres` + 140 `postgres`-marked) — **767
automated tests combined**. Day 7A is implemented across five reviewable
gates (7A-A/7A-B/7A-C/7A-D/7A-E), all approved, and passes the full
backend/frontend/browser verification matrix, but has not yet been
committed — see CLAUDE.md's "Current Phase". No frontend resolve control,
acknowledgment/reopening/assignment/comments, bulk resolution, status
filtering, authentication, and every other Day 6D-era deferral remain
later-day scope; the existing five CI jobs are unchanged, and no sixth job
was added.

**Day 7B — Frontend Incident-Resolution Vertical Slice.** Builds the
frontend consumer of Day 7A's endpoint: a "Resolve incident" control on
every exact-`OPEN` incident card, calling
`POST /incidents/{incident_id}/resolve` with no request body through a
dedicated `postNoBody` transport primitive, validated first by the shared
`isIncidentResponse` structural check and then by endpoint-specific success
semantics (matching `incident_id`, exact `RESOLVED` status, populated
`resolved_at`). `useIncidents` (still the single owner of the incident
array) gains per-incident pending/error state, a synchronous
active-request-ref duplicate guard, and independent resolution for
different incidents; a successful response atomically replaces only the
matching incident (order and unrelated object references preserved,
`lastUpdatedAt` untouched, zero follow-up `GET /incidents`). A shared
timestamp-reconciliation helper (`Date.parse`, never lexical string
comparison) protects both the resolve response and any concurrent
`GET /incidents` refresh from reverting an already-newer or already-
`RESOLVED` incident, and retains any current-only incident a refresh
response happens to omit — correct because `GET /incidents` is unfiltered
and append-only, never evidence of deletion. `IncidentCard` remains
presentational (native button, native `disabled`, visible "Resolving…"
label, card-scoped `role="alert"`); `IncidentDashboard` only wires hook
state to cards, adding no duplicate state of its own. See "Current Day 7B
scope" above for the full detail. Test totals as of Day 7B: 276 Vitest
tests (7 files, up from 176), 19 Python orchestration-helper tests (1 file,
unchanged), 1 Playwright browser test (1 file, unchanged — still
configuration submission/refresh only), 571 backend `pytest` tests
(unchanged since Day 7A — Day 7B is frontend-only) — **867 automated tests
combined**. Day 7B is implemented across five reviewable gates
(7B-A/7B-B/7B-C/7B-D/7B-E), all approved, and passes the full
backend/frontend/browser verification matrix, but has not yet been
committed — see CLAUDE.md's "Current Phase". A Playwright resolution
scenario, incident acknowledgment/reopening/assignment/comments, bulk
resolution, status filtering, and authentication remain later-day scope;
the existing five CI jobs are unchanged, and no sixth job was added.

**Day 7C — Browser-Driven Incident-Resolution Vertical Slice.** Adds the
second Playwright Chromium scenario
(`frontend/e2e/incident-resolution.spec.ts`), proving the Day 7A/7B
incident-resolution vertical slice through a real Chromium browser, a real
production `vite preview` build, real cross-origin HTTP, a real FastAPI
process, and a real, disposable PostgreSQL database — establishing an
`OPEN` incident through the real configuration-submission form, resolving
it through the real "Resolve incident" control, observing the real
no-body `POST /incidents/{incident_id}/resolve` request and its direct
`RESOLVED` response, and confirming that state survives a page reload,
all without ever mocking, intercepting, or fulfilling a network response.
A small shared Playwright-layer helper module
(`frontend/e2e/helpers.ts`) was extracted so both scenarios share
identical dashboard-loading/submission/card-location logic; the existing
configuration-submission scenario was refactored onto it with two
behavior-preserving relaxations forced by the two scenarios now sharing
one disposable database across a single orchestrated run — it no longer
assumes the dashboard starts empty, and it now locates its own incident by
stable identity plus exact status `OPEN` instead of assuming the page
contains exactly one `<article>` (its `Occurrences` assertion is
correspondingly a positive-integer check, not a fixed literal). This
isolation was verified directly, not merely inferred from `workers: 1`:
the resolution scenario alone against a fresh database, the resolution
scenario running before the configuration scenario (exercising a
historical-`RESOLVED`-then-new-`OPEN`-recurrence path), and the standard
discovery order — all three real orchestrated runs passed with cleanup
verified. No frontend or backend product code, API contract, browser
orchestrator, Playwright configuration, Docker Compose, or CI workflow
changed. See "Current Day 7C scope" above for the full detail. Test
totals as of Day 7C: 276 Vitest tests (7 files, unchanged), 19 Python
orchestration-helper tests (1 file, unchanged), 2 Playwright browser tests
(2 files — configuration-submission/refresh, and incident resolution),
571 backend `pytest` tests (431 non-`postgres` + 140 `postgres`-marked,
unchanged since Day 7A/7B) — **868 automated tests combined**. Day 7C is
implemented across two reviewable gates (7C-A/7C-B), both approved, and
passes the full backend/frontend/browser verification matrix, but has not
yet been committed — see CLAUDE.md's "Current Phase". Incident
acknowledgment/reopening/assignment/comments, bulk resolution, status
filtering, and authentication remain later-day scope; the existing five
CI jobs are unchanged, and no sixth job was added.

**Day 8A — Multi-Vendor Configuration Support (Arista EOS).** Adds the
platform's second vendor end to end, across six reviewable gates
(8A-A through 8A-F, all approved). **Supported vendors are now Cisco
IOS-XE and Arista EOS** — selectable from the same, single, vendor-neutral
configuration-submission form; no second form, page, or route was added.

**Architecture overview (unchanged shape, one more adapter):** vendor
adapter (`CiscoAdapter` or `AristaAdapter`) → vendor-neutral
`NormalizedConfiguration` → `PolicyEvaluator` (exact `applies_to ==
device_id` matching, no wildcard) → `IncidentFactory` + atomic
deduplicated upsert → the same incident dashboard and `GET /incidents`
API. `AristaAdapter` (`backend/src/meta_rne/adapters/arista.py`) is a
self-contained line-oriented parser — no import from or delegation to
`CiscoAdapter` — implementing a narrow, explicitly-scoped EOS subset
(hostname; interface/description/CIDR IP address/shutdown/`ip
access-group`; named-only `ip access-list` with optional-or-implicit ACL
sequence numbers; `router bgp`/`neighbor ... remote-as`), reusing the
existing `ParseErrorCode` contract with no new member. The production
`AdapterRegistry` now registers both adapters; `ConfigIngestionService`,
`api/routes.py`, and `api/schemas.py` gained no vendor branch and no
schema change.

**Demo workflows:**

- *Cisco*: submit a Cisco IOS-XE configuration for `spine-01` missing
  `ACL-EXTERNAL-IN` inbound on `GigabitEthernet0/1` — the existing
  `policy-acl-external-in` policy fires, producing one `OPEN` incident.
- *Arista*: select "Arista EOS" in the same form, submit an EOS
  configuration for `leaf-02` missing `ACL-EXTERNAL-IN` inbound on
  `Ethernet1` — a second, independent, exact-match seeded policy,
  `policy-acl-external-in-leaf-02` (`applies_to="leaf-02"`), fires through
  the identical detection pipeline, producing its own `OPEN` incident.

**Two device-specific policy rows, not one shared policy.** Both express
the same logical required-ACL requirement and equivalent required-ACL
semantics (`ACL-EXTERNAL-IN` inbound, `Medium` severity) — evaluated
through the same, unmodified, vendor-neutral `PolicyEvaluator` — but
remain two genuinely separate rows with their own `policy_id`/`applies_to`
values. No wildcard (`"*"`) applicability was introduced.

**Frontend.** `SupportedVendor` (`frontend/src/api/types.ts`) is now
`"cisco-ios-xe" | "arista-eos"`; the vendor `<select>` renders both
options with Cisco IOS-XE as the default; a real `useState`-backed
handler replaces the previous no-op vendor-change handler; the selected
vendor and the entered raw configuration text are both forwarded
unchanged. `frontend/src/api/configurations.ts` and
`frontend/src/hooks/useConfigurationSubmission.ts` required no code
change — both were already generic over the request type.

**Browser proof.** A third, focused Playwright Chromium scenario
(`frontend/e2e/arista-configuration-submission.spec.ts`) proves the
complete new wiring — real vendor selection, a real (never
mocked/intercepted) `POST /devices/leaf-02/config` request and response,
the resulting `OPEN` incident located by stable identity, and its
survival across a real page reload against real PostgreSQL. The two
existing Cisco scenarios (configuration submission, incident resolution)
are unchanged. `frontend/e2e/helpers.ts`, `frontend/playwright.config.ts`,
`scripts/browser_e2e.py`, `docker-compose.yml`, and all five CI jobs are
unchanged.

Verified totals as of Day 8A: **628** backend `pytest` tests (486
non-`postgres` + 142 `postgres`-marked), **281** frontend Vitest tests (7
files), **19** Python orchestration-helper tests (1 file, unchanged), **3**
Playwright browser tests (3 files) — **931 automated tests combined**. All
five existing CI-equivalent jobs (`ci`, `postgres-tests`, `compose-smoke`,
`frontend`, `browser-e2e`) pass; none were added, removed, or modified.
Policy CRUD, wildcard applicability, a third vendor, universal device
applicability, authentication, and telemetry remain later-day scope, and
complete EOS syntax coverage is not claimed.

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

**Day 6A — Docker Compose smoke validation and frontend-facing contract
stabilization.**

Day 1 planning, Day 2 scaffolding, Day 3A, Day 3B, Day 4A, Day 4B1, Day 4B2,
Day 4B3, Day 5A, Day 5B, and Day 6A are complete and approved. See README.md's
"Current Project Status" for the full history. Day 4B ("Slice 1
persistence foundations") is complete across all three reviewable gates
(4B1/4B2/4B3). The first vertical slice (product-spec.md Section 11) is
runnable end to end over real HTTP against a real PostgreSQL database
(Day 5B), and Day 6A proves that same slice in its actual deployed Docker
Compose shape (`scripts/compose_smoke.py`) and stabilizes the OpenAPI/CORS
contract a future React app will consume — see README.md's "Current Day
6A scope" and `docs/frontend-api-contract.md`. Day 6A implements no
frontend (no React, Vite, Node/npm) and changed no Slice 1 business
behavior — only route metadata (`operation_id`/documented responses), CORS
middleware wiring, Compose port configuration, and a new standalone smoke
script.

Application code is currently allowed **only** for the completed Day 3A,
Day 3B, Day 4A, Day 4B1, Day 4B2, Day 4B3, Day 5A, Day 5B, and Day 6A
capabilities:

- explicit OpenAPI `operation_id`s (`health_check`,
  `submit_device_configuration`, `list_incidents`) and documented `409`/
  `422`/`500` responses (`api/routes.py`); configurable CORS
  (`meta_rne.api.cors`, `create_app(cors_allowed_origins=...)`, env var
  `META_RNE_CORS_ALLOWED_ORIGINS`, disabled by default, no wildcard); a
  standalone `scripts/compose_smoke.py` runner and a third CI job
  (`compose-smoke`); OpenAPI/CORS contract tests
  (`tests/contract/api/test_openapi_contract.py`,
  `tests/contract/api/test_cors_api.py`); `docs/frontend-api-contract.md`
  (Day 6A)

- normalized configuration domain objects (Day 3A)
- vendor adapter contracts, `AdapterRegistry`, Cisco IOS-XE parsing and
  normalization (Day 3A)
- `ConfigurationPolicy`/`RequiredAclRule`, `ConfigurationViolation`/
  `AclAssignmentEvidence`, and the deterministic `PolicyEvaluator` (Day 3B)
- `IncidentSource`/`IncidentStatus`, `IncidentCandidate`/
  `PolicyViolationIncidentEvidence`, `IncidentFactory.build_candidate`,
  and `compute_fingerprint` (Day 4A)
- persisted `Device`, `ConfigurationSnapshot` (+ `compute_raw_text_hash`),
  and persisted `Incident`/`IncidentUpsertOutcome`/`IncidentUpsertResult`
  domain objects; `DeviceRepository`/`ConfigurationSnapshotRepository`/
  `ConfigurationPolicyRepository`/`IncidentRepository`/`UnitOfWork`
  Protocols (interfaces only — no concrete implementation yet); explicit
  JSON serialization (`meta_rne.persistence.serialization`) for
  `NormalizedConfiguration`, `RequiredAclRule` tuples, and
  `PolicyViolationIncidentEvidence`; private SQLAlchemy declarative ORM
  models (`meta_rne.persistence.sqlalchemy.models`); the first Alembic
  migration (Slice 1 tables, the two-stage `devices`/
  `configuration_snapshots` foreign keys, CHECK constraints, and the
  partial unique OPEN-fingerprint index) (Day 4B1)
- Concrete `DeviceRepository`/`ConfigurationSnapshotRepository`/
  `ConfigurationPolicyRepository` implementations, both SQLAlchemy/
  PostgreSQL (`meta_rne.persistence.sqlalchemy.*_repository`) and
  in-memory conformance-test doubles (`meta_rne.persistence.memory.*`,
  sharing one `InMemoryStore` so cross-repository reference integrity is
  enforced without a database); the persistence error hierarchy
  (`meta_rne.persistence.errors`: `PersistenceError` →
  `PersistenceConflictError` → `DeviceConflictError`/
  `SnapshotAlreadyExistsError`/`PolicySeedConflictError`/
  `ReferencedDeviceNotFoundError`); the pure Slice 1 seed builder
  (`meta_rne.persistence.seeds.build_slice1_policies`) (Day 4B2)
- Concrete `IncidentRepository` implementations, both SQLAlchemy/PostgreSQL
  (`meta_rne.persistence.sqlalchemy.incident_repository`) and in-memory
  (`meta_rne.persistence.memory.incident_repository`): `upsert_open_incident`
  is one atomic `INSERT ... ON CONFLICT (fingerprint) WHERE status = 'OPEN'
  DO UPDATE ...` statement (never a read-before-write) targeting the
  partial unique index, guarded by a `WHERE excluded.last_seen_at >=
  incidents.last_seen_at` condition so a stale observation mutates nothing;
  `CREATED`/`UPDATED` outcome detection via a private `xmax = 0` check,
  returning explicit named columns, never an ORM object; `incident_id` is
  generated by an injected `incident_id_factory: Callable[[], str]`
  (production default `str(uuid4())`, `meta_rne.persistence.incident_id`)
  only for a new row, never derived from the fingerprint; a referenced
  `device_id` that doesn't exist raises `ReferencedDeviceNotFoundError`
  (translated from the foreign-key violation inside a SAVEPOINT, Session
  left fully usable); the in-memory side enforces the same contract under
  one lock guarding the whole find-OPEN-by-fingerprint -> decide -> mutate
  sequence, verified by both a repository conformance suite and dedicated
  four-worker concurrency tests (real PostgreSQL and in-memory) (Day 4B3)
- Concrete `UnitOfWork` implementations: `SqlAlchemyUnitOfWork`
  (`meta_rne.persistence.sqlalchemy.unit_of_work`), constructed from a
  `session_factory: Callable[[], Session]`, creating exactly one `Session`
  shared by all four repositories — `commit()` calls the real `Session.
  commit()`, rolling back and re-raising the original exception on failure;
  `rollback()`/`close()` delegate directly to the `Session`; no
  context-manager syntax yet. `InMemoryUnitOfWork`
  (`meta_rne.persistence.memory.unit_of_work`), working against an isolated
  *working* `InMemoryStore` (fresh locks, never the committed store's lock
  instances) copied from a shared *committed* `InMemoryStore` at
  construction — `commit()` publishes all four collections into the
  committed store at once under its own lock; `rollback()`/`close()`
  publish nothing (Day 4B3)
- unit tests, persistence/migration tests, repository contract tests,
  UnitOfWork contract tests, concurrency tests, and representative fixtures
- documentation corrections explicitly approved for Day 3A/3B/4A/4B1/4B2/4B3
  (see below)

**Documentation corrections applied for Day 3A:**

1. `docs/domain-model.md` — added `description: string | null` to the
   normalized interface model.
2. `docs/architecture.md` and `docs/test-strategy.md` — split "invalid
   interface IP address or subnet mask" into two separate parser
   failures (address vs. mask).

**Documentation corrections applied for Day 3B:**

1. `docs/domain-model.md` §6/§7/§16/§18 — `RequiredAclRule` gained
   `severity`/`recommendation`; `ConfigurationViolation` restructured to
   `rule_ref`/`affected_resource`/`severity`/`evidence`/`recommendation`
   with a new `AclAssignmentEvidence` value object; `ViolationType` split
   into `MISSING_REQUIRED_ACL` and `TARGET_INTERFACE_MISSING`; `"*"`
   wildcard `applies_to` matching scoped out of Day 3B (exact
   `applies_to == device_id` only).
2. `docs/architecture.md` §7/§9 — evaluator narrative updated to match
   (no `FixedClock`, deterministic violation ordering, computed vs.
   copied violation fields); one stale `policy_id` reference corrected
   to `rule_ref`.
3. `docs/test-strategy.md` §12/§19 — policy-evaluator sub-case list and
   one test name updated to match.

**Documentation corrections applied for Day 4A:**

1. `docs/domain-model.md` §7/§10/§11/§13/§16/§17/§18 — resolved a
   conflict where §7 said `IncidentFactory` copies `recommendation`
   verbatim while §13 and the §18 worked example showed it templated
   into different wording; `Incident.recommendation`/
   `IncidentCandidate.recommendation` are now documented as a plain
   `string`, copied verbatim, with a `Recommendation{summary, details}`
   value object and template generation explicitly deferred. Also
   resolved a second conflict where `ConfigurationViolation.
   affected_resource` (interface-centered) and `Incident.
   affected_resource` (`"acl:{name}:{interface}:{direction}"`) were
   documented as two different formats needing an undefined derivation;
   `affected_resource` is now copied verbatim end-to-end (only one
   format), which also corrects a pre-existing §18 worked example that
   didn't match §7's own contract. Documented the new
   `PolicyViolationIncidentEvidence` value object (adds `violation_type`/
   `source_snapshot_id`, keeps `actual_acl_name`, renames
   `expected_acl_name`) and the `IncidentCandidate.observed_at` field
   (= `violation.detected_at`, not read from a clock).
2. `docs/architecture.md` §9 — the `IncidentFactory.build_candidate` flow
   and the vertical-slice value table updated to match (verbatim
   `affected_resource`/`recommendation`, added `observed_at`).
3. `docs/test-strategy.md` §13/§19 — `IncidentFactory`/fingerprint test
   descriptions updated to match the verbatim-copy contract and the
   actual test name/location (`compute_fingerprint` lives in
   `tests/unit/domain/test_incident.py`, a `domain` service per
   domain-model.md §17, not `detection`).

**Documentation corrections applied for Day 4B1:**

1. `docs/domain-model.md` §2/§14/§18 — `Device.first_seen_at`/`last_seen_at`
   renamed to `created_at`/`updated_at` (same fields, no behavior change;
   consistent with every other created/updated pair in this document).
2. `docs/domain-model.md` §4/§18 — `ConfigurationSnapshot.raw_text`/
   `raw_source_hash` renamed to `raw_config_text`/`raw_text_hash` (same
   fields; the hash field's name now states both what it hashes and that
   it's a hash).
3. `docs/domain-model.md` §12, `docs/architecture.md` §11.1,
   `docs/test-strategy.md` §9 — `IncidentRepository.find_open_by_fingerprint`
   removed from the documented port surface; the atomic
   `upsert_open_incident` is the only documented dedup mechanism, matching
   the Day 4B1 binding decision that no separate read-only lookup method is
   needed to prove it.
4. `docs/architecture.md` §8/§11.2 — the two remaining `raw_source_hash`/
   `raw_text` references updated to `raw_text_hash`/`raw_config_text` to
   match.

**Documentation corrections applied for Day 4B2:**

1. `docs/domain-model.md` §12 and `docs/architecture.md` §11.1 — the
   documented `DeviceRepository`/`ConfigurationSnapshotRepository`/
   `ConfigurationPolicyRepository` method lists were stale relative to
   `domain/ports.py` since Day 4B1 (they still listed `DeviceRepository.
   list()`, `ConfigurationSnapshotRepository.save()`/
   `get_current_for_device`/`get_baseline_for_device`, and
   `ConfigurationPolicyRepository.get_for_device()` with `"*"` wildcard
   matching). Corrected to the actual approved surface: `get_by_id`/`save`
   (Device), `get_by_id`/`add` (Snapshot), `get_applicable_to_device`/
   `seed_if_missing` (Policy, exact-match only, no wildcard) — and
   documented the Day 4B2 conflict-error behavior each method now has.
2. `docs/test-strategy.md` §9 — the repository-conformance bullet's
   generic `save`/`get_by_id`/`list` phrasing updated to name the actual
   per-repository contract (including the new conflict errors) instead of
   implying a uniform `save`/`list` surface across all three repositories.

**Documentation corrections applied for Day 4B3:**

1. `docs/domain-model.md` §12 and `docs/architecture.md` §11.1 — both
   still documented `IncidentRepository.list(filter: {device_id,
   severity})`, but `domain/ports.py` has declared `list_all()` with no
   filter parameter since Day 4B1; corrected to the actual approved
   surface (`get_by_id`/`list_all`/`upsert_open_incident`), documented
   `list_all()`'s ascending `created_at`-then-`incident_id` ordering, and
   noted that `device_id`/`severity` filtering is deferred to the
   application/API layer rather than the repository.
2. `docs/architecture.md` §11.1 — the `INSERT ... ON CONFLICT ... DO
   UPDATE ... RETURNING` statement was updated to match what was actually
   built and approved during implementation: the `DO UPDATE SET` list now
   names exactly `last_seen_at`/`occurrence_count`/`severity`/`evidence`/
   `recommendation` (never `incident_id`/`fingerprint`/`device_id`/
   `source`/`rule_ref`/`affected_resource`/`status`/`created_at`); a
   `WHERE excluded.last_seen_at >= incidents.last_seen_at` guard was added
   so a stale observation affects no row (the original design lacked this
   guard); `RETURNING` now lists explicit named columns plus `(xmax = 0)
   AS was_inserted` rather than `*`; and the internal (non-public)
   follow-up `SELECT` used only to distinguish a genuinely stale
   observation from an unexpected empty result is now documented. The
   `UnitOfWork` interface block gained `close() -> None` (already on
   `domain/ports.py` since Day 4B1 but missing from this diagram), and the
   prose was updated to describe the actual `SqlAlchemyUnitOfWork`
   (`session_factory`-constructed) and `InMemoryUnitOfWork`
   (working/committed store split) designs.
3. `docs/test-strategy.md` §9 — the repository-conformance and
   failure-path bullets were expanded to name the actual `upsert_open_
   incident` field-preservation/update and `UnitOfWork` commit/rollback
   contract now implemented; the concurrency-test bullet corrected to
   name the actual test (`..._yields_one_open`, four workers, not two,
   `occurrence_count == 4`) and its actual file location.

- `ConfigIngestionService` (`meta_rne.application.config_ingestion`),
  orchestrating adapter resolution/normalization, the existing Device/
  ConfigurationSnapshot two-stage save, `PolicyEvaluator.evaluate`,
  `IncidentFactory.build_candidate`, `compute_fingerprint`, and
  `IncidentRepository.upsert_open_incident` across exactly one injected
  `UnitOfWork` per successful call; `IngestConfigurationCommand`/
  `ConfigIngestionResult` (`meta_rne.application.models`);
  `ConfigurationParseError` (`meta_rne.application.errors`), preserving the
  adapter's `ParseError` value verbatim; an injectable
  `default_snapshot_id_factory` (`meta_rne.application.snapshot_id`); a
  pre-transaction boundary (command validation, adapter resolution, parse,
  canonical-`VendorType` derivation, snapshot-ID generation/validation) that
  creates zero UnitOfWorks for an unsupported vendor, a parse failure, or an
  invalid generated ID; exception-preserving rollback/close handling that
  never replaces the original exception with a secondary lifecycle failure
  (Day 5A)
- unit tests (application command/result validation, service orchestration
  against a real `InMemoryUnitOfWork`, including pre-transaction-boundary,
  success, and rollback/lifecycle cases) and focused PostgreSQL integration
  tests proving atomic multi-table commit and atomic multi-table rollback
  after a forced late failure (Day 5A)

**Documentation corrections applied for Day 5A:**

1. `docs/architecture.md` §4/§4.1 — the previously "binding" design (a
   positional `ingest(device_id, vendor, config_text)` signature, an
   injected `Clock` port supplying `observed_at`, and a stale
   `get_for_device` reference already superseded by Day 4B2's
   `get_applicable_to_device`) did not match the actual approved Day 5A
   design: an explicit `IngestConfigurationCommand` (carrying
   `observed_at` directly, no `Clock` dependency), a pre-transaction
   boundary that opens zero `UnitOfWork`s for an unsupported vendor, a
   parse failure, or an invalid generated snapshot ID, and
   `ConfigurationParseError` (not a DTO variant) as the parse-failure
   signal. Corrected to match; structured logging (the old steps 10/13)
   is now explicitly noted as deferred past Day 5A rather than described
   as already wired in.

- `POST /devices/{device_id}/config` and `GET /incidents` (`meta_rne.
  api.routes.build_router`), backed by explicit Pydantic schemas
  (`meta_rne.api.schemas`) that are the resource itself on success — no
  `{"data": ..., "error": null}` envelope — and a direct `list[IncidentResponse]`
  for `GET /incidents`, never wrapped; `SubmitConfigurationRequest` rejects
  unknown fields (`ConfigDict(extra="forbid")`) and blank
  `vendor`/empty `raw_config_text` via Pydantic field validators, so those
  fail through FastAPI's own 422 `RequestValidationError` path with no
  custom envelope; `ApiErrorResponse{code, detail}` (lowercase snake_case
  codes, `detail` not `message`) is the direct (unwrapped) body for every
  mapped error category (Day 5B)
- HTTP error mapping (`meta_rne.api.errors.register_exception_handlers`):
  `UnsupportedVendorError`/`ConfigurationParseError` → 422
  (`unsupported_vendor`/`configuration_parse_error`, the latter using the
  real `ParseError.message`/`.line_number`, never a stack trace or the
  full submitted config); `DeviceConflictError`/
  `SnapshotAlreadyExistsError`/`ReferencedDeviceNotFoundError` → 409
  (`device_conflict`/`snapshot_already_exists`/`referenced_device_not_found`);
  any other caller/application `ValueError` → 422 (`invalid_request`);
  `PersistenceError` → 500 (`persistence_error`, generic public detail,
  registered after its specific conflict subclasses); `SerializationError`
  → 500 (`serialization_error`, generic public detail); `InvalidClockError`
  (an injected clock returning a naive/non-UTC value — a server-composition
  failure, not caller input) and any other unmapped exception get **no**
  custom handler at all, falling through to FastAPI's normal unmapped-
  exception 500 behavior rather than a broad catch-all (Day 5B)
- `meta_rne.api.clock.utc_now`/`require_utc`/`InvalidClockError` — the
  `POST` route calls its injected clock exactly once per request,
  validates the result is UTC-aware before constructing
  `IngestConfigurationCommand`, and `ConfigIngestionService.ingest` is
  called exactly once; `GET /incidents` never calls the clock at all
  (Day 5B)
- `meta_rne.application.incident_queries.ListIncidentsService` — the
  narrow read-only use case behind `GET /incidents`: one `UnitOfWork` per
  call, `uow.incidents.list_all()` exactly once, never `commit()`s,
  `close()` exactly once, with the same exception-preserving
  rollback/close lifecycle as `ConfigIngestionService` (a SQLAlchemy read
  can open a transaction that still needs an explicit rollback) (Day 5B)
- `create_app(...)` (`meta_rne.api.app`) — a controlled composition
  factory, never a bare module-level side effect: importing `api.app`
  creates no SQLAlchemy engine/`Session` and requires no `DATABASE_URL`;
  production engine construction
  (`meta_rne.api.dependencies._LazySqlAlchemyUnitOfWorkFactory`) is lazy,
  happening on first actual request or lifespan startup, and the engine is
  disposed on shutdown; every request gets its own `UnitOfWork`/`Session`
  because `ConfigIngestionService`/`ListIncidentsService` each invoke the
  injected `unit_of_work_factory` fresh, once per operation; the
  module-level `app = create_app()` (unchanged import path, for Uvicorn)
  is otherwise untouched by tests — every test builds its own isolated
  `create_app(...)` instance directly, never `app.dependency_overrides`
  (Day 5B)
- idempotent Slice 1 policy seeding
  (`meta_rne.api.dependencies.seed_slice1_policies`), run from
  `create_app`'s FastAPI `lifespan` only when `seed_on_startup=True`
  (the production default; contract tests pass `seed_on_startup=False`
  except the dedicated startup tests) — one validated UTC timestamp from
  the injected clock, one `UnitOfWork`, `build_slice1_policies` +
  `seed_if_missing`, commit once, close once, with the same
  exception-preserving rollback/close lifecycle; a semantic seed conflict
  (`PolicySeedConflictError`) fails application startup rather than being
  suppressed. Alembic migrations remain an explicit deployment step run
  *before* Uvicorn starts (Docker `CMD`, unchanged) — never a FastAPI
  startup hook, per architecture.md Section 11.2 (Day 5B)
- production `AdapterRegistry` composition
  (`meta_rne.api.dependencies.build_production_adapter_registry`) — exactly
  one registry containing `CiscoAdapter`, injected into
  `ConfigIngestionService`; no vendor resolution or parsing happens in a
  route (Day 5B)
- API contract tests against in-memory repositories
  (`tests/contract/api/test_config_ingestion_api.py`,
  `tests/contract/api/test_incidents_api.py`,
  `tests/contract/api/test_startup_seeding_api.py`) and focused PostgreSQL
  API integration tests
  (`tests/integration/api/test_api_postgres.py`) proving real HTTP →
  real transaction atomicity, independent POST/GET Sessions, and the real
  lazy-`DATABASE_URL` production composition path (Day 5B)

**Documentation corrections applied for Day 5B:**

1. `docs/architecture.md` Section 10/10.1/12 — still documented a
   `{"data": ..., "error": null}` success envelope and an HTTP 400 for
   `UnsupportedVendorError`/`ParseError`; the actual approved Day 5B
   design returns the resource directly on success (no envelope) and maps
   those two categories to 422 (schema-adjacent caller errors), with
   persistence conflicts split out to 409 — a category the original table
   never listed at all. Corrected; see docs/test-strategy.md Section 14's
   updated table for the full mapping.
2. `docs/domain-model.md` Section 18's `GET /incidents` worked example
   omitted `fingerprint` from each list item and wrapped the array in the
   same success envelope as the corrected item above; corrected to a bare
   `list[IncidentResponse]` including `fingerprint`, matching
   `IncidentResponse` (`api/schemas.py`) exactly.
3. `docs/architecture.md` Section 11.1's `ConfigurationPolicyRepository`
   diagram comment still said `get_for_device`, already superseded by Day
   4B2's `get_applicable_to_device` (and already corrected everywhere else
   in this file during Day 4B2/5A) — corrected here too.
4. `docs/product-spec.md` NFR-05, AC-01/AC-02/AC-04/AC-12, and Section 11's
   worked example still documented the same obsolete envelope/400 contract
   as item 1 above (left uncorrected in the initial Day 5B pass, out of
   that phase's approved file list). Corrected in a follow-up minimal
   patch: NFR-05's status/`code` table now matches `api/errors.py` exactly
   (422 for `UnsupportedVendorError`/`ConfigurationParseError`, 409 for the
   three persistence-conflict subclasses, `detail` not `message`, no
   envelope); the `data.`-prefixed field references in AC-01/AC-02/AC-04
   and Section 11's worked JSON were corrected to direct fields; Section
   11's `routing` example no longer shows `static_routes`, which
   `NormalizedRouting` does not have.
5. The three genuine conflicts flagged (but deliberately left uncorrected)
   at the end of item 4 above are now also fixed, in a second follow-up
   patch: `docs/architecture.md` Section 5.1's Cisco parser-contract table
   no longer pairs `ParseError` with `CONFIG_PARSE_ERROR`/400 — reworded to
   state the parser returns a value with no HTTP status of its own, and
   that `application`/`api` translate it to HTTP 422 with
   `code: "configuration_parse_error"`; Section 5's `resolve(vendor_id)`
   bullet corrected from `UnsupportedVendorError (400)` to 422 with
   `code: "unsupported_vendor"`; Section 10's own cross-reference to
   `docs/product-spec.md`'s NFR-05 table (claiming it "still documents the
   older contract") was itself stale after item 4's patch and is now
   corrected, along with a leftover wrong class name
   (`ConfigIngestionResponse` → `SubmitConfigurationResponse`) found
   alongside it. `docs/domain-model.md`'s informal prose at two spots
   (`CONFIG_PARSE_ERROR`/`UNSUPPORTED_VENDOR`, uppercase) is corrected to
   the lowercase public codes, and its `ConfigurationSnapshot` worked
   example no longer shows `static_routes`. `docs/test-strategy.md`
   Section 9's `PERSISTENCE_ERROR` reference (written when
   `ConfigIngestionService` didn't exist yet) is corrected to
   `persistence_error`, now that it does. `docs/architecture.md` Section
   6's canonical `NormalizedConfiguration` diagram and Section 8's
   (explicitly not-yet-implemented) `DriftDetector` bullet still name
   `static_routes` — left as-is, since Section 6 is now annotated inline
   as deferred and Section 8 is already headed "not part of the first
   vertical slice"; neither claims to be current Day 5B behavior.

**Still prohibited**: incident acknowledgment/resolution commands,
authentication/authorization, filtering/pagination/sorting query
parameters, drift detection, anomaly/telemetry ingestion, structured
logging beyond FastAPI's own request logging, the React dashboard,
Vite, Node/npm tooling, Playwright, browser end-to-end tests, and new
Alembic migrations. All of these are Day 6B or later, against the domain
model, architecture, and ports already documented, with tests written
first per the Development Rules above.
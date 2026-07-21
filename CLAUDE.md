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

**Day 7A — Backend-only incident-resolution vertical slice (implemented
across gates 7A-A/7A-B/7A-C/7A-D/7A-E, all approved, awaiting commit/CI
approval).**

Day 1 planning, Day 2 scaffolding, Day 3A, Day 3B, Day 4A, Day 4B1, Day 4B2,
Day 4B3, Day 5A, Day 5B, Day 6A, Day 6B, Day 6C, and Day 6D are complete and
approved. Day 7A adds the first incident-lifecycle mutation
(`OPEN -> RESOLVED`) end to end through the backend: domain invariants and
`Incident.resolve(at)` (Gate 7A-A, plus a timestamp-monotonicity
correctness patch checked against `updated_at` rather than `last_seen_at`),
an explicit application-layer `Clock` protocol and `ResolveIncidentService`
with a controlled `IncidentNotFoundError` (Gate 7A-B), a production
`CallableClock` adapter and `POST /incidents/{incident_id}/resolve` wired
through the existing `create_app`/`build_router` composition with a direct
`IncidentResponse` and an exact `incident_not_found` 404 (Gate 7A-C), and a
binding real-PostgreSQL proof that reingesting a resolved finding creates a
new `OPEN` incident while existing `OPEN` deduplication and concurrent
resolution remain correct (Gate 7A-D). Day 7A changed no frontend source,
no Playwright test, no CI workflow, and no `docker-compose.yml` — see
README.md's "Current Day 7A scope".

Application code is allowed for **`frontend/`** (unchanged since Day 6D) in
addition to the Day 3A–Day 7A backend capabilities listed below:

- `src/api/client.ts` (`getJsonArray`, `postJson`, `ApiRequestError` with an
  optional `code`), `src/api/incidents.ts` (`fetchIncidents`),
  `src/api/configurations.ts` (`submitDeviceConfiguration`),
  `src/api/types.ts` (`IncidentResponse`,
  `PolicyViolationIncidentEvidenceResponse`, `ApiErrorResponse`,
  `ConfigurationSubmissionRequest`, `ConfigurationSubmissionResponse`,
  `NormalizedConfigurationResponse` and its nested interface/routing/ACL
  response types, `severity`/`status`/`source`/`violation_type`/`direction`
  unions, and the `isIncidentResponse`/
  `isPolicyViolationIncidentEvidenceResponse`/
  `isConfigurationSubmissionResponse` (plus its nested
  `isNormalizedConfigurationResponse`/`isNormalizedInterfaceResponse`/
  `isNormalizedRoutingResponse`/`isNormalizedBgpNeighborResponse`/
  `isNormalizedAclResponse`/`isNormalizedAclEntryResponse`) runtime
  structural guards that validate every parsed field — never a bare type
  cast) — derived directly from `docs/frontend-api-contract.md`, no invented
  fields (no `static_routes`), `fingerprint` preserved. Enum-like fields are
  validated as non-empty strings only (never a closed union), so an
  unrecognized future backend value is preserved and rendered as text.
  `postJson` narrowly recognizes FastAPI's own `{"detail": [...]}` request-
  validation body shape and maps it to one stable safe public message,
  never rendering the array, field locations, rejected input, or any other
  internal detail.
- `src/hooks/useIncidents.ts` — the loading/success(`isRefreshing`)/error
  request-lifecycle state machine: one `AbortController` paired with a
  monotonically increasing request ID per request, so a late-resolving
  stale success or stale failure can never overwrite a newer result
  (independent of whether the client honors `AbortSignal`), a superseded
  request's `AbortError` never surfaces as an error, and unmount aborts the
  active request. `refresh()` (shared by Refresh and Retry, and now also by
  a successful configuration submission) preserves previously loaded data
  instead of dropping to a full loading state.
- `src/hooks/useConfigurationSubmission.ts` — the
  idle/submitting/success(`response`)/error(`message`, optional `code`)
  submission lifecycle for `POST /devices/{device_id}/config`, following the
  same `AbortController`/monotonically-increasing-request-ID/mounted-ref
  guarantees as `useIncidents`: a new `submit()` call aborts any in-flight
  submission and starts exactly one new POST, a stale completion can never
  overwrite newer state, `AbortError` never surfaces as a visible error, and
  unmount aborts the active request and blocks further state updates. An
  optional `onSuccess` callback is stored in a ref synced via
  `useLayoutEffect` (never assigned during render) so the latest committed
  callback — not a stale closure — always runs, exactly once, for a current
  successful POST; the callback's outcome (a synchronous exception or a
  rejected Promise) is deliberately never awaited and can never turn a
  successful submission into an error.
- `src/components/ConfigurationSubmissionForm.tsx` — a standalone controlled
  form (device ID text input, an enabled single-option `cisco-ios-xe`
  vendor `<select>`, a raw-configuration `<textarea>`) built on
  `useConfigurationSubmission`. Device ID blankness is checked via
  `deviceId.trim().length === 0` without ever trimming the value actually
  sent; raw configuration is rejected locally only when
  `rawConfigText.length === 0` (whitespace-only text is allowed) and is
  never trimmed, normalized, or line-ending-rewritten. The submit button is
  natively `disabled` while submitting, with a defensive `onSubmit` guard as
  a second line of defense against a stray submit event; local validation
  messages use `aria-invalid`/`aria-describedby`/`role="alert"`; pending
  state uses `role="status"` text and `aria-busy` on the `<form>`; the
  hook's error state renders only its controlled `message`/`code` text
  (`role="alert"`); the success state (`role="status"`) displays
  `device_id`/`snapshot_id`/`violations_detected`/`incidents_created`/
  `incidents_updated` plus `normalized_config` inside a semantic
  `<details>`/`<summary>`, rendered as JSON text via `JSON.stringify` (React
  text escaping, never `dangerouslySetInnerHTML`).
- `src/pages/IncidentDashboard.tsx` — still the sole owner of
  `useIncidents()` (no duplicate incident state, no second data-fetching
  hook); now also renders `ConfigurationSubmissionForm` inside the existing
  `<main>`, above the incident-list content and unconditionally visible
  across every incident-section state (loading/empty/populated/error/
  refreshing), passing `onSubmissionSuccess={() => { refresh(); }}` as the
  only integration trigger (no effect watches submission state). A
  successful POST therefore causes exactly one additional `GET /incidents`;
  a failed POST or a local validation rejection causes zero. A refresh
  triggered this way inherits every existing `useIncidents` guarantee
  (old-card preservation, native Refresh-button disabling, stale-result
  protection, abort behavior) unchanged; a subsequent refresh failure
  produces the incident section's own controlled error state without ever
  changing the already-successful submission result.
- `src/components/{LoadingState,IncidentEmptyState,IncidentErrorState,
  IncidentCard}.tsx` — unchanged from Day 6B: the four required incident-
  list UI states, incidents rendered as responsive cards in backend order,
  evidence/fingerprint in an accessible `<details>` region, and a Refresh
  button that is natively `disabled` (not merely `aria-disabled`) while a
  refresh is pending, with `aria-busy`/an `aria-live="polite"` status
  communicating the pending refresh.
- the `frontend` GitHub Actions CI job (Node-based, no PostgreSQL/Docker)
- 176 frontend Vitest/RTL tests across 7 files (`src/api/client.test.ts`,
  `src/api/incidents.test.ts`, `src/api/configurations.test.ts`,
  `src/hooks/useIncidents.test.ts`,
  `src/hooks/useConfigurationSubmission.test.ts`,
  `src/components/ConfigurationSubmissionForm.test.tsx`,
  `src/pages/IncidentDashboard.test.tsx`)

**Day 6D adds** a single browser-level end-to-end test proving the Day 6C
flow works through the real, deployed shape, plus the isolated,
cross-platform orchestration that makes that test reproducible on a
developer's machine and in CI:

- `frontend/playwright.config.ts` — Chromium only (no Firefox/WebKit/mobile
  emulation), `workers: 1`, `retries: 0`, no `webServer` block (the
  orchestrator below owns the stack's lifecycle instead), a mandatory
  `PLAYWRIGHT_BASE_URL` (the config throws at load time if it is unset or
  blank — never a silent `localhost` fallback), `trace: "retain-on-failure"`,
  `screenshot: "only-on-failure"`, `video: "retain-on-failure"`, an explicit
  `outputDir` under `frontend/test-results`, and no visual-snapshot
  behavior.
- `frontend/e2e/config-submission-refresh.spec.ts` — the one Playwright
  test: navigates the real dashboard, confirms the fresh-database empty
  state, submits the exact `spine-01` / `cisco-ios-xe` / missing-ACL
  configuration from README's own worked example, asserts the real `201`
  POST response and the visible success fields (`device_id`, a present
  non-empty `snapshot_id`, `violations_detected: 1`, `incidents_created: 1`,
  `incidents_updated: 0`), asserts exactly one initial `GET /incidents` and
  exactly one additional `GET /incidents` after the POST (never a second
  POST), asserts the resulting incident's stable fields (device, `OPEN`,
  `Medium`, `policy-acl-external-in`, the affected interface,
  `occurrence_count: 1`), reloads the page, and asserts the same incident
  is still visible through a third real `GET /incidents` — all via pure
  network *observation* (`page.on("request")`/`page.waitForResponse()`),
  never `page.route()` interception or fulfillment. No generated UUID,
  fingerprint, timestamp, or locale-formatted date is ever asserted as a
  literal value.
- `scripts/browser_e2e.py` — the single authoritative, Python-standard-
  library-only orchestration script (same discipline as
  `scripts/compose_smoke.py`): reserves three host ports simultaneously
  (never bind-then-close-then-reuse), generates a unique, validated,
  lowercase Compose project name, starts only `docker-compose.yml`'s
  existing `db`+`api` services (no new Compose file, no frontend Compose
  service, no frontend Docker image), waits for container health and a real
  `GET /health` before touching the browser, builds the real frontend with
  `VITE_API_BASE_URL` baked in, launches `vite preview` directly through
  `node` (never `npm`, so `terminate()` cannot leave an orphaned wrapper
  process), waits for a real `GET /` 200, runs
  `npm run test:e2e:direct` with `PLAYWRIGHT_BASE_URL` computed from the
  same frontend-port value used to build `META_RNE_CORS_ALLOWED_ORIGINS`
  (so the CORS origin and the actual browser origin are always identical,
  with `127.0.0.1` used everywhere, never `localhost`), preserves
  Playwright's real exit code, and — in a `finally` block that runs on any
  outcome — terminates the preview process, releases every port
  reservation, runs `docker compose down --volumes --remove-orphans`, and
  independently verifies (via `com.docker.compose.project` label queries)
  that no container or volume for that project remains. No `--keep` option
  exists; there is no debugging path that intentionally retains state.
- `scripts/test_browser_e2e.py` — 19 `unittest`-based tests (Python
  standard library only) covering the orchestration script's pure/narrowly-
  isolated helpers (project-name validation/generation, simultaneous
  three-port reservation and independent release, runtime-
  environment/CORS construction, Compose/Vite/npm command assembly) —
  never the browser or backend, which are proved by actually running
  `scripts/browser_e2e.py`.
- a fifth GitHub Actions job, `browser-e2e` (independent of `ci`/
  `postgres-tests`/`compose-smoke`/`frontend`, all four unchanged):
  Python 3.12 + Node 24 + pinned npm 11.6.2, the helper tests run *before*
  installing Chromium (fail fast on a broken helper before paying for the
  browser download), Chromium-only installation
  (`playwright install --with-deps chromium`), the isolated orchestration
  command with a deterministic CI project name
  (`meta-rne-browser-e2e-${{ github.run_id }}-${{ github.run_attempt }}`),
  a failure-only Playwright report/test-results artifact upload
  (`if: failure()`, 7-day retention), and an always-run, project-scoped
  defense-in-depth cleanup step.

Verified automated-test inventory as of Day 7A: **176** frontend Vitest
tests (7 files, unchanged from Day 6D), **19** Python orchestration-helper
tests (1 file, unchanged), **1** Playwright browser test (1 file — never
counted as part of the Vitest file total; still covers configuration
submission and refresh only, does not resolve an incident), **571** backend
`pytest` tests (431 non-PostgreSQL + 140 PostgreSQL) — **767** automated
tests combined.

**Still prohibited**: additional vendors, vendor autodetection, file upload,
configuration history, device inventory, `GET /devices`, incident
acknowledgment (the enum member and DB constraint remain dormant
compatibility state — no public transition into it exists), reopening,
assignment, comments/notes, audit history, user identity, bulk resolution,
status filtering/pagination/client-side sorting, authentication/
authorization, React Router, any global state library, TanStack Query, a
component library, Tailwind, charts, telemetry, WebSockets/polling, a
frontend Docker image or Compose service, production deployment, and cloud
infrastructure. A frontend resolve control does not exist yet — Day 7A is
backend-only; a future frontend phase may add one. Within browser testing
specifically, Firefox/WebKit projects, mobile/device-emulation projects,
visual-regression snapshot testing, and accessibility-auditing libraries
remain out of scope. All of these are later days.

Application code is currently allowed **only** for the completed Day 3A,
Day 3B, Day 4A, Day 4B1, Day 4B2, Day 4B3, Day 5A, Day 5B, Day 6A, Day 6B,
Day 6C, Day 6D, and Day 7A capabilities (Day 6D added no new backend
capability; Day 7A's own additions are listed after the Day 6D bullet list
below):

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

- **`Incident.updated_at`/`resolved_at` and `Incident.resolve(at)`**
  (`meta_rne.domain.incident`) — `updated_at: datetime` (required,
  timezone-aware) and `resolved_at: datetime | None` (nullable, timezone-
  aware when set) added to the persisted `Incident` dataclass. Invariants:
  `created_at <= last_seen_at <= updated_at`; `resolved_at <= updated_at`
  when present; `status == RESOLVED` iff `resolved_at is not None`.
  `resolve(at)`: `OPEN -> RESOLVED`, requiring `at >= updated_at` (checked
  against `updated_at`, not `last_seen_at`, since an OPEN incident may
  legally already have `last_seen_at < updated_at` — a correctness patch
  applied within Gate 7A-A after an initial `last_seen_at`-only check was
  found insufficient); assigns the one captured value to both
  `resolved_at` and `updated_at`; already-`RESOLVED` returns the incident
  unchanged as a true no-op, before any timestamp validation; the dormant
  `ACKNOWLEDGED` member raises rather than silently resolving — no public
  transition into or out of it exists (Day 7A, Gate 7A-A)
- **`IncidentRepository.resolve(incident_id, resolved_at) -> Incident | None`**
  (`meta_rne.domain.ports`), added deliberately narrow rather than a
  generic full-row `save()` — it only ever writes `status`/`resolved_at`/
  `updated_at`, so it can never clobber a concurrent
  `upsert_open_incident`'s writes to `occurrence_count`/`evidence`/
  `last_seen_at`/`severity`. SQLAlchemy: one atomic conditional
  `UPDATE ... WHERE incident_id = :id AND status = 'OPEN' AND updated_at <=
  :resolved_at RETURNING ...` (never a read-before-write, same idiom as
  `upsert_open_incident`); a naive/non-UTC `resolved_at` is rejected before
  the statement is built; a zero-row result triggers one internal follow-up
  `SELECT` (`populate_existing=True`, so a Session's earlier
  identity-map-cached object can never be returned stale) distinguishing
  missing (`None`), already-`RESOLVED` (returned unchanged), still-`OPEN`
  with a stale supplied timestamp (`ValueError`), and any other persisted
  status such as the dormant `ACKNOWLEDGED` (`ValueError`) — never an
  apparent-success return for an unresolved conflict. In-memory: the same
  operation under the existing `incidents_lock`, delegating to
  `Incident.resolve()`. `upsert_open_incident` (both implementations) now
  also sets `updated_at = observed_at` on both the create and dedup-update
  branches, alongside `last_seen_at`, and never touches `resolved_at` (Day
  7A, Gate 7A-A)
- Alembic revision `0002_incident_resolution.py` (`down_revision =
  "0001_slice1_persistence"`, revision 0001 itself unedited): adds
  `incidents.updated_at` (nullable, backfilled from `last_seen_at`, then
  tightened to `NOT NULL`) and `incidents.resolved_at` (nullable), plus
  `ck_incidents_updated_at_after_last_seen_at`,
  `ck_incidents_resolved_at_matches_status`, and
  `ck_incidents_resolved_at_before_or_equal_updated_at`. No status-column
  migration was needed — `incidents.status`'s existing CHECK constraint
  (revision 0001) already permitted `'RESOLVED'`. The partial unique index
  `ux_incidents_open_fingerprint` (`WHERE status = 'OPEN'`) is unchanged —
  a `RESOLVED` row already falls outside it, which is what lets the same
  fingerprint recur as a new `OPEN` row after resolution, with no index or
  migration change required for that behavior (Day 7A, Gate 7A-A)
- **`Clock` protocol and `ResolveIncidentService`**
  (`meta_rne.application.incident_resolution`) — a minimal
  `class Clock(Protocol): def now(self) -> datetime: ...`, never importing
  `meta_rne.api.clock` or calling the system clock directly.
  `ResolveIncidentService.resolve(incident_id) -> Incident`: loads the
  incident; unknown id raises `IncidentNotFoundError`
  (`meta_rne.application.errors`, preserving `.incident_id` as structured
  data); already-`RESOLVED` returns it unchanged with **zero** `Clock`
  calls, no repository write, and no `commit()`; an `OPEN` incident calls
  `Clock.now()` **exactly once**, passes that single captured value to
  `uow.incidents.resolve(...)`, commits once, and returns the repository's
  persisted result (accepting, without a second `Clock` call, an
  already-`RESOLVED` incident a concurrent request committed first). Every
  path follows the same exception-preserving rollback/close-with-notes
  `UnitOfWork` lifecycle as `ConfigIngestionService`/`ListIncidentsService`
  (Day 7A, Gate 7A-B)
- **`CallableClock`** (`meta_rne.api.clock`) — adapts `create_app`'s
  existing `clock: Callable[[], datetime]` parameter to the application
  layer's `Clock` protocol via `require_utc`, so `ResolveIncidentService`
  reuses the identical injected time source `POST /devices/{id}/config`
  already uses for `observed_at` — never a second clock. **`POST
  /incidents/{incident_id}/resolve`** (`api/routes.py`, `operation_id =
  "resolve_incident"`) — no request body; success is HTTP 200 with a
  direct, complete `IncidentResponse` (the same schema `GET /incidents`
  uses, now including `updated_at: datetime` and
  `resolved_at: datetime | None`, `null` for `OPEN` incidents); the route
  only calls `resolve_incident_service.resolve(incident_id)` and maps the
  result — no transaction control and no direct field assignment in the
  route. `IncidentNotFoundError` maps to HTTP 404 with the exact body
  `{"code": "incident_not_found", "detail": "Incident '<incident_id>' was
  not found."}` (`api/errors.py`), built from `exc.incident_id`, never
  `str(exc)`. `GET /incidents` remains unfiltered — it returns both `OPEN`
  and `RESOLVED` incidents, unchanged from prior days. No new route,
  frontend control, CI job, or Docker Compose service was added (Day 7A,
  Gate 7A-C)
- Real-PostgreSQL-proven recurrence and concurrency behavior (Gate 7A-D,
  tests only, no production-code change): resolving an incident and then
  reingesting the identical still-invalid configuration creates a **new**
  `OPEN` incident (same fingerprint, new `incident_id`,
  `occurrence_count: 1`) while the original `RESOLVED` row is left
  completely unchanged; the new `OPEN` incident then deduplicates further
  reingestion exactly as before (`occurrence_count` increments, no third
  row); two concurrent `resolve()` calls against one `OPEN` incident both
  return a consistent, identical persisted `RESOLVED` result with no row
  corruption and no duplicate `OPEN` row; a committed ingestion update
  before a later resolution is honored (the resolution succeeds against
  the advanced row, leaving `occurrence_count`/`last_seen_at` untouched);
  a resolution timestamp stale relative to a since-advanced `updated_at` is
  rejected rather than moving `updated_at` backward. No locks, retries,
  queues, or isolation-level changes were introduced to achieve any of
  this — the existing atomic conditional-UPDATE design (Gate 7A-A) was
  already sufficient

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

**Still prohibited**: incident acknowledgment (the dormant `ACKNOWLEDGED`
enum member/DB constraint has no public transition), reopening, assignment,
comments/notes, audit history, user identity, bulk resolution, status
filtering, authentication/authorization, filtering/pagination/sorting query
parameters, drift detection, anomaly/telemetry ingestion, and structured
logging beyond FastAPI's own request logging. Explicit, single-purpose
incident resolution (`POST /incidents/{incident_id}/resolve`, `OPEN ->
RESOLVED` only) is no longer prohibited as of Day 7A — see the "Current
Phase" section above; new Alembic migrations are likewise no longer
categorically prohibited, since Day 7A added one (`0002_incident_
resolution.py`) without editing revision 0001. The React dashboard
(`frontend/`), Vite, and Node/npm tooling are no longer prohibited — Day 6B
implemented the first frontend vertical slice, Day 6C implemented the
second (configuration submission), and Day 6D added a Chromium-only
Playwright browser end-to-end test plus its isolated orchestration —
Playwright and browser end-to-end tests are therefore no longer prohibited,
though Firefox/WebKit, mobile-device-emulation projects, visual-regression
snapshot testing, accessibility-auditing libraries, a frontend Docker image
or Compose service, production deployment, and cloud infrastructure remain
so. A frontend resolve control does not exist yet (Day 7A is backend-only).
All remaining items are later days, against the domain model, architecture,
and ports already documented, with tests written first per the Development
Rules above.
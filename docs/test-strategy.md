# Test Strategy — Meta RNE Platform

**Status:** Draft — Day 1 consistency correction
**Date:** 2026-07-18
**Phase:** Planning / Architecture

Defines how the MVP in [product-spec.md](./product-spec.md),
[architecture.md](./architecture.md), and [domain-model.md](./domain-model.md)
is tested, per [CLAUDE.md](../CLAUDE.md) Rule 2 and NFR-04. Stack is fixed
by [ADR-0002](./adr/0002-technology-stack-and-persistence.md): pytest
(backend), Vitest (frontend, later slice), Playwright (E2E), all against
Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL.

---

## 1. Testing Goals

1. Every FR (FR-01–FR-10) and AC (AC-01–AC-13) has ≥ 1 automated test that
   fails if the requirement breaks.
2. Prove the first vertical slice (product-spec.md Section 11) end-to-end
   before drift, telemetry, Arista, or the dashboard are built.
3. Keep `domain`/`detection` provably framework-independent (NFR-02) by
   testing them with no server and no database running.
4. Most tests run in milliseconds so the unit suite runs on every save.
5. Failure paths (parse errors, invalid input, persistence failure) are
   first-class test subjects, not an afterthought.

---

## 2. Testing Principles

- **Behavior over implementation.** No test targets a private/internal
  method; assertions are on return values, stored state, HTTP responses,
  or log lines.
- **Real components by default; no reliance on mocks.** In-memory
  repositories are real, fast, dependency-injected implementations of the
  same interfaces as the production SQLAlchemy repositories — using them
  in a test is not mocking. Hand-written test doubles are reserved for two
  cases: simulating a failure a real component can't produce cheaply
  (e.g., a repository raising on `upsert_open_incident`, or a `UnitOfWork`
  raising on `commit`, Section 9), and E2E's real
  Postgres/API pair, where nothing is substituted at all.
- **One behavior per test**, except the deliberately multi-step
  vertical-slice tests (Section 19).
- **Deterministic only.** No `sleep`; time-windowed rules (RULE-LINK-FLAP)
  use injected/fixed timestamps.
- **Tests are written before or alongside implementation** (CLAUDE.md
  Rule 2), never retrofitted.

---

## 3. Test Pyramid

```
                    ▲
                   ╱ ╲          E2E (Playwright, real API + real Postgres)
                  ╱   ╲         few — vertical slices only
                 ╱─────╲
                ╱       ╲       API contract (pytest + FastAPI TestClient)
               ╱         ╲      one per endpoint × outcome
              ╱───────────╲
             ╱             ╲    Integration (pytest, in-memory repos)
            ╱               ╲   application services + real repos
           ╱─────────────────╲
          ╱                   ╲  Unit (pytest)
         ╱                     ╲ domain, detection, adapters — the bulk
        ╱───────────────────────╲
```

Target mix, by test count: ~65% unit, ~20% integration, ~10% contract,
~5% E2E — cheapest place to assert a behavior is the layer with fewest
collaborators.

---

## 4. Unit Test Responsibilities

| | |
|---|---|
| **Tested** | `domain` value shapes/invariants, `detection` services in isolation (`PolicyEvaluator`, `DriftDetector`, `RuleEngine` — one rule at a time), vendor adapters (`CiscoAdapter.parse`, `AristaAdapter.parse`), `IncidentFactory`'s severity/recommendation/fingerprint mapping. |
| **Mocked** | Nothing — these take plain data in, return plain data out. |
| **Real components** | Everything under test is real; no collaborators to substitute. |
| **Speed** | Sub-millisecond to low single-digit ms/test; full suite well under 5s. |
| **Location** | `tests/unit/domain/`, `tests/unit/detection/`, `tests/unit/adapters/`. |

---

## 5. Integration Test Responsibilities

| | |
|---|---|
| **Tested** | `application` use-case services — chiefly `ConfigIngestionService`, which directly coordinates `PolicyEvaluator`, `IncidentFactory`, and `IncidentRepository` (architecture.md Section 4; there is no separate `PolicyEvaluationService` or `IncidentService`), plus `IncidentQueryService` — wired to **real in-memory repositories** and real adapters/detection services — orchestration, not transport. |
| **Mocked** | Nothing, except an in-memory log recorder in place of raw stdout capture (Section 13). |
| **Real components** | In-memory repositories (test doubles for the SQLAlchemy interfaces, ADR-0002), adapters, detection services. |
| **Speed** | Single-digit ms/test; full suite in a few seconds. |
| **Location** | `tests/integration/application/`. |

---

## 6. API Contract Test Responsibilities

| | |
|---|---|
| **Tested** | `api` (FastAPI) HTTP surface: Pydantic schema validation, envelope shape, status-code mapping (architecture.md Section 12), route wiring. One test per endpoint × meaningful outcome. |
| **Mocked** | Nothing by default — the FastAPI app is constructed with real `application` services and in-memory repositories, invoked via FastAPI's `TestClient` (in-process, no socket). |
| **Real components** | Full stack below `api` (application, domain, detection, persistence-as-in-memory) is real and in-process; only the TCP transport is skipped. |
| **Speed** | Tens of ms/test; full suite in a few seconds. |
| **Location** | `tests/contract/api/`, one file per resource (`test_devices_api.py`, `test_incidents_api.py`). |

---

## 6.1 Compose Smoke Test Responsibilities (Day 6A)

| | |
|---|---|
| **Tested** | The real deployed Docker Compose shape: image build, real `db` + `api` startup/healthchecks, real Alembic migration completing before Uvicorn starts, real idempotent Slice 1 policy seeding, real HTTP ingestion/query traffic, and real PostgreSQL-backed state surviving an `api` process restart with no database reset. |
| **Mocked** | Nothing — real image, real containers, real HTTP, real restart. |
| **Real components** | The actual `docker-compose.yml` shape (Section 15), run under its own isolated Compose project name and host ports so it never collides with a developer's own stack or other local Postgres instances. |
| **Speed** | Single-digit minutes/run, dominated by image build; not run on every save — CI's dedicated `compose-smoke` job, and on demand locally. |
| **Location** | `scripts/compose_smoke.py` (repo root, Python-standard-library only — the single authoritative implementation, invoked identically by a developer and by CI, never duplicated in CI YAML). |

**Distinct from Section 7's E2E suite below**: this proves the deployed
*shape* boots and serves correct traffic for the exact scenarios it
drives (three sequential config submissions against `spine-01`, a
restart, incident/evidence assertions) — it is not the full
acceptance-test matrix, does not use Playwright, and is not a replacement
for the still-unbuilt E2E suite once it exists. See
`docs/architecture.md` Section 15 for the full flow description and
invocation.

---

## 7. End-to-End Test Responsibilities

| | |
|---|---|
| **Tested** | The full vertical slice against a **running `compose.e2e.yml` stack — the real FastAPI app and a real PostgreSQL container**, driven over real HTTP. This is binding (product-spec NFR-06): E2E must use the real API and PostgreSQL, never in-memory repositories. |
| **Mocked** | Nothing — the one level where "no mocks" is absolute. |
| **Real components** | `api` + `db` services from `compose.e2e.yml` (architecture.md Section 15.1), migrated and seeded before any test runs. Playwright's `request` context issues real HTTP calls. |
| **Speed** | Hundreds of ms to a few seconds/test, dominated by container startup (paid once per suite run). Kept to a small number of tests. |
| **Location** | `e2e/vertical-slice/`, alongside `compose.e2e.yml` (not `docker-compose.yml`, which is the local-dev/production file, Section 15). |

**Network reachability.** Playwright needs a route to `api` that survives
however the suite is invoked. Either `api`'s port is published to the
host in `compose.e2e.yml` (Playwright runs on the host, hits
`localhost:<port>`), or a `playwright` service is defined *inside* the
same Compose network and reaches `api` by its service name — one of the
two, chosen and documented at implementation time; either satisfies "real
HTTP to a real deployed instance."

**Startup ordering.** The suite does not send its first request until
`db` and `api` report healthy (a `depends_on: condition: service_healthy`
health check on each, or an equivalent explicit wait/poll) — not merely
"the containers exist," since `api` can be up before it has finished
running migrations (architecture.md Section 11.2) or accepting
connections.

**Full E2E lifecycle** (architecture.md Section 15.1, restated from the
test side): start the stack → wait for health → migrate → seed → run the
Playwright suite, which itself first asserts the `POST` response (status
`201`, `normalized_config`, `violations_detected`/`incidents_created`/
`incidents_updated`) before calling `GET /incidents` at all, so a broken
`POST` fails fast with a specific assertion instead of surfacing only as
a confusing `GET` mismatch → destroy the entire stack and its volumes,
whether the suite passed or failed.

Playwright is used in **HTTP request mode**, not browser automation —
there is no browser UI until FR-10 ships. When the React dashboard is
built, `e2e/` grows a browser-driven suite alongside this one.

### 7.1 Vitest (frontend)

**Day 6B**: `frontend/` now exists with **75 colocated Vitest + React
Testing Library tests across 4 files** (`src/**/*.test.ts(x)`, not a
separate `frontend/tests/` directory):

- `src/api/client.test.ts` (21) — base-URL joining/defaulting (no trailing
  slash, one, several), exact request options (`GET`, `Accept:
  application/json`, no `Content-Type`, no `Authorization`, `credentials:
  "omit"`), successful array response, `{code, detail}` error surfacing
  with the `code` preserved on `ApiRequestError`, a stable fallback message
  for malformed/empty/HTML error bodies (never rendering their raw
  content), malformed 2xx JSON rejected with the controlled
  incidents-response message (no parser internals leaked), `AbortSignal`
  passthrough as the exact same object.
- `src/api/incidents.test.ts` (24) — the `isIncidentResponse`/
  `isPolicyViolationIncidentEvidenceResponse` runtime structural
  validators: a valid payload passes; a top-level object, a `null` array
  entry, missing/wrong-typed/non-integer/negative `occurrence_count`, and a
  missing-or-null `evidence` are all rejected; `actual_acl_name: null` is
  accepted; each enum-like field (`severity`/`status`/`source`/
  `violation_type`/`direction`) is tested twice — rejecting an empty
  string, and separately preserving an unrecognized future value as text;
  backend order is preserved; opaque IDs pass through byte-for-byte.
- `src/hooks/useIncidents.test.ts` (5) — `useIncidents`'s request lifecycle
  in isolation via `renderHook`: the active request is aborted on unmount;
  `refresh()` aborts an in-flight request and starts exactly one new one;
  a late-resolving stale success and a late-resolving stale failure can
  each never overwrite a newer successful result; a superseded request's
  rejection never produces a visible error state. These use deferred
  promises and direct `refresh()` calls whose mocked responses deliberately
  ignore `AbortSignal`, proving the monotonic request-ID guard
  independently of cancellation actually being honored.
- `src/pages/IncidentDashboard.test.tsx` (25) — loading, empty, populated
  with preserved backend ordering, evidence/fingerprint exposure,
  `occurrence_count === 1`, severity/status as text, semantic `<time>`
  timestamps, a controlled error state (including a malformed-payload
  response), retry-to-success, one Refresh/Retry click producing exactly
  one request, existing cards remaining visible with an accessible
  `aria-live` busy status while a refresh is pending, Refresh enabled after
  a successful load, Refresh natively `disabled` (not merely
  `aria-disabled`) while pending, a further click on the disabled button
  starting no additional request, Refresh re-enabled after a successful
  refresh completes, a failed refresh's resulting Retry control enabled, a
  successful refresh replacing old data, a failed refresh producing the
  controlled error state, heading persistence across states, and unmount
  aborting the active request. Deliberately overlapping requests are *not*
  produced through this component's UI — that is the hook-level suite's
  job, now that the native `disabled` attribute makes a second overlapping
  click impossible to produce by clicking through the rendered dashboard
  (a real click on a disabled button dispatches no event at all).

Fetch is stubbed directly (`vi.stubGlobal("fetch", ...)`) — no mocking
library beyond what Vitest/RTL already provide. Run via `npm test -- --run`
(CI) or `npm test` (watch, local).

**Day 6C**: adds the second frontend vertical slice (configuration
submission), bringing the suite to **176 tests across 7 files** (verified by
an actual clean-install `npm ci` + `npm test -- --run`, not carried forward
from an earlier count). Three new files
(`src/api/configurations.test.ts`, `src/hooks/useConfigurationSubmission.test.ts`,
`src/components/ConfigurationSubmissionForm.test.tsx`) and additions to two
existing files (`src/api/client.test.ts`, `src/pages/IncidentDashboard.test.tsx`)
cover, by responsibility rather than as an exhaustive per-test list:

- **HTTP client and safe errors** (`client.test.ts` additions) — the new
  `postJson` request shape (method/headers/credentials/body/`AbortSignal`),
  and the new narrow recognition of FastAPI's `{"detail": [...]}`
  validation-error body, mapped to one stable safe message without
  rendering the array's contents.
- **Configuration request/response contract** (`configurations.test.ts`) —
  path-segment encoding (including reserved characters, and a proof that
  the device ID is never trimmed), the exact two-key request body (with a
  dedicated regression test proving a caller-supplied object carrying extra
  `device_id`/`observed_at`/other properties is never forwarded as-is), and
  complete structural validation of a `201` response including every nested
  `normalized_config` field — mirroring the rigor `incidents.test.ts`
  already applies to `GET /incidents`.
- **Submission hook lifecycle/concurrency** (`useConfigurationSubmission.test.ts`)
  — idle/submitting/success/error transitions, the same
  abort-and-supersede/stale-result-guard pattern proven for
  `useIncidents.test.ts` above, plus submission-specific guarantees:
  `onSuccess` invoked exactly once for a current successful POST, never for
  a failed/aborted/superseded one, always using the *latest committed*
  callback even when its identity changes mid-flight, and never turned into
  a submission failure by a synchronous callback exception or a rejected
  callback Promise.
- **Standalone form behavior/accessibility** (`ConfigurationSubmissionForm.test.tsx`)
  — rendered independently of the dashboard: explicit labels, the
  single-option enabled vendor select, local validation (blank device ID,
  empty configuration text) and its clearing on edit, byte-exact
  preservation of entered values through to the exact POST call, native
  submit-button disabling plus a defensive `onSubmit` guard, controlled
  `role="alert"`/`role="status"` presentation of errors/pending/success,
  and `normalized_config` rendered as escaped text inside a semantic
  `<details>`/`<summary>`.
- **Dashboard refresh integration** (`IncidentDashboard.test.tsx` additions)
  — cross-component behavior only, deliberately not repeating what the
  standalone hook/form suites above already prove: the form renders above
  and remains usable across every incident-section state, a successful
  submission triggers exactly one additional `GET /incidents` and never a
  second one, a failed POST or local validation rejection triggers zero,
  a refresh failure following a successful submission produces the
  incident section's own independent controlled error without ever
  rewriting the already-successful submission result, and a submission
  arriving while an unrelated manual refresh is still pending supersedes it
  under `useIncidents`'s existing rules.

Browser E2E for the dashboard was deferred through Day 6B/6C — see Section
7.2 below for Day 6D, which adds it. The HTTP-mode Playwright suite
described in Section 7 above remains separately deferred and was
unaffected and untouched by Day 6B, 6C, or 6D.

### 7.2 Browser (Playwright/Chromium) End-to-End Testing (Day 6D)

**Distinct from Section 7 above.** Section 7 describes a still-unbuilt
*HTTP-mode* Playwright suite that drives the API directly via `request` —
no browser, no frontend, no CORS. Day 6D instead builds a genuinely
different *browser*-mode suite, whose purpose is exactly what an HTTP-only
suite cannot prove: that a real browser, loading the real built frontend,
can actually reach the API across origins (real CORS headers, not a test
assumption) and render the result.

| | |
|---|---|
| **Tested** | The complete configuration-submission → incident-refresh vertical slice, driven by a real Chromium browser via Playwright, against a real `vite preview` production build, real cross-origin HTTP, a real FastAPI process, and a real, disposable PostgreSQL database (architecture.md Section 15.2). |
| **Mocked** | Nothing — the one level (alongside Section 7's still-unbuilt HTTP-mode suite) where "no mocks" is absolute. The spec observes real requests/responses (`page.on("request")`, `page.waitForResponse()`) and never calls `page.route()` to intercept or fulfill one. |
| **Real components** | Chromium (via Playwright), the actual built React app served by `vite preview`, the actual FastAPI app, and a real PostgreSQL instance started from the existing `docker-compose.yml`'s `db`+`api` services (no new Compose file, no frontend Docker image or Compose service). |
| **Speed** | A few seconds for the test itself; the full orchestrated run (image build, health waits, frontend build, preview startup) is minutes, dominated by the same costs as `compose-smoke` (Section 6.1). Not run on every save — a dedicated CI job (`browser-e2e`), and on demand locally. |
| **Location** | `frontend/e2e/config-submission-refresh.spec.ts` (test), `frontend/playwright.config.ts` (Chromium-only project, `workers: 1`, `retries: 0`, `PLAYWRIGHT_BASE_URL` mandatory — the config fails to load if it is unset/blank rather than silently defaulting), `scripts/browser_e2e.py` (the isolated, Python-standard-library-only orchestrator; single authoritative implementation, same discipline as `scripts/compose_smoke.py`), `scripts/test_browser_e2e.py` (19 `unittest` tests for the orchestrator's own pure/narrowly-isolated helpers — project-name validation/generation, simultaneous three-port reservation and independent release, runtime-environment/CORS construction, Compose/Vite/npm command assembly). |

**Fresh-database assertions (exact, not soft-checked).** The orchestrator
guarantees a fresh, disposable database and the backend's existing
idempotent Slice 1 policy seeding (architecture.md Section 11.2,
unchanged) for every run, so the spec asserts exact values rather than
permitting dirty-state ranges: the dashboard starts with an empty incident
list; the `POST` response has `violations_detected: 1`,
`incidents_created: 1`, `incidents_updated: 0`; the resulting incident has
`occurrence_count: 1`. A present, non-empty `snapshot_id` is asserted
without ever asserting its generated value; `incident_id`, `fingerprint`,
and every timestamp are never asserted as literals.

**Request-count assertions.** The spec attaches its request observer and
its `GET /incidents`/`POST /devices/spine-01/config` response waiters
*before* the triggering action in every case (before `page.goto()`, before
clicking Submit, before `page.reload()`) so a fast response can never be
missed. Counts are asserted at three checkpoints: after the initial page
load (one `GET`, zero `POST`s), after a successful submission (two `GET`s,
one `POST`), and after a page reload (three `GET`s, still one `POST`).

**Reload persistence.** After the reload's third `GET /incidents`, the same
logical incident (matched on `device_id`/`rule_ref`/`affected_resource` —
never on `incident_id`, per the "no generated-ID literal" rule above) is
still visible, proving the finding survived in PostgreSQL across a full
page reload, not merely in React state.

**Failure artifacts.** On failure, Playwright retains a trace, a
screenshot, and a video (all failure-only, never on success) under
`frontend/test-results/`, plus an HTML report under
`frontend/playwright-report/` in CI — uploaded as a GitHub Actions artifact
only when the job fails (`if: failure()`, 7-day retention), never on a
passing run.

**Isolated cleanup.** Every run uses a unique, validated, lowercase Compose
project name and a disposable volume; `docker compose down --volumes
--remove-orphans` runs unconditionally in a `finally` block, followed by an
independent verification (via `com.docker.compose.project` label queries)
that no container or volume for that project remains. There is no `--keep`
option and no path that intentionally retains state — every run is
disposable by construction (architecture.md Section 15.2).

**Dedicated CI job.** `browser-e2e` (`.github/workflows/ci.yml`) is
independent of `ci`/`postgres-tests`/`compose-smoke`/`frontend` (all four
unchanged): Python 3.12, Node 24, pinned npm 11.6.2, the orchestration
helper tests run *before* Chromium is installed (so a broken helper fails
fast rather than after paying for the browser download), Chromium-only
installation, the isolated orchestration command with a deterministic
per-run project name, failure-only artifact upload, and an always-run,
project-scoped defense-in-depth cleanup step.

**Scope, explicitly bounded.** Chromium only — no Firefox/WebKit project,
no mobile/device-emulation project, no visual-regression snapshot testing.
Deferred alongside Section 18's existing deferrals.

**Verified counts, as of Day 7B** (each recorded separately — none of
these are interchangeable substitutes for another, see below):

| Layer | Count | Location |
|---|---|---|
| Vitest (frontend) | 276 tests, 7 files (176 as of Day 6D/7A; see Section 7.3 for the Day 7B additions) | `frontend/src/**/*.test.{ts,tsx}` |
| Python `unittest` (orchestration helpers) | 19 tests, 1 file (unchanged) | `scripts/test_browser_e2e.py` |
| Playwright (browser) | 1 test, 1 file (unchanged — still config-submission/refresh only, does not resolve an incident) | `frontend/e2e/config-submission-refresh.spec.ts` |
| pytest (backend) | 571 tests (431 non-`postgres` + 140 `postgres`, unchanged since Day 7A) | `backend/tests/` |
| **Combined** | **867 automated tests** | — |

Day 7A's 101 new/extended backend tests (571 vs. Day 6D's 470) are covered
in Section 20 below, by layer. Vitest, the orchestration helpers, and
Playwright were all unchanged from Day 6D through Day 7A — Day 7A was a
backend-only vertical slice, verified by re-running the existing frontend/
browser suites unmodified against the migrated schema and expanded
`IncidentResponse` (Section 20.13). **Day 7B is the inverse: frontend-only.**
It adds 100 new Vitest tests (276 vs. Day 7A's 176 — see Section 7.3) and
changes no backend code, so the orchestration helpers and Playwright are
verified unmodified, exactly as Day 7A verified the frontend/browser suites
unmodified against its own backend-only change.

**Why these layers are not interchangeable.** Each proves something the
others structurally cannot: Vitest proves frontend units, components, and
hooks in isolation (React state machines, runtime response validators,
accessible markup) with a stubbed `fetch` — fast, but it never sends a real
HTTP request or renders in a real browser. Python `unittest` proves the
orchestration script's own pure/narrowly-isolated helpers (port
reservation, project-name validation, command assembly) — it never starts
Docker, Node, or a browser, so it cannot prove the orchestrated flow
actually works end to end. Playwright proves *system wiring*: that the
built frontend, served for real, can actually reach the real backend across
a real network origin boundary with real CORS headers, and that what a user
would see in a real browser matches what the backend actually persisted —
something no amount of mocked-`fetch` Vitest coverage or in-memory-repository
pytest coverage can demonstrate by construction. pytest proves backend
domain/application/persistence/API correctness in depth (every branch,
every error path, every repository conformance case) far more cheaply than
a browser ever could, which is exactly why the browser suite stays at one
test — it exists to prove wiring, not to re-prove business logic already
covered exhaustively at the layers below it.

---

### 7.3 Day 7B — Incident Resolution Frontend Vertical Slice

Adds 100 new/extended Vitest tests (276 vs. Day 7A's 176) across the same
7 files, covering the frontend consumer of Day 7A's
`POST /incidents/{incident_id}/resolve` endpoint, by layer:

- **`IncidentResponse` contract** (`src/api/types.test.ts` is not a separate
  file — these live in `src/api/incidents.test.ts`): `updated_at`/
  `resolved_at` accepted as a datetime string and (`resolved_at` only) as
  `null`; a missing `updated_at` or a missing `resolved_at` key rejected;
  an empty-string or wrong-typed `resolved_at` (number/object/array)
  rejected; every field, old and new, preserved on a valid response; `OPEN`/
  `RESOLVED`/the dormant `ACKNOWLEDGED`/an unrecognized future status all
  remain structurally accepted (the shared validator stays forward-
  compatible — eligibility is a rendering concern, not a validation one).
- **`postNoBody` transport** (`src/api/client.test.ts`): `POST` method,
  exact configured-base-URL path construction, `Accept: application/json`,
  `credentials: "omit"`, no `body` key in the constructed `RequestInit` at
  all, no `Content-Type` header, `AbortSignal` passthrough, a successful
  JSON body returned, the existing `{code, detail}`/malformed/HTML/network/
  invalid-JSON-success error conventions all reused and reproven for this
  transport function specifically (not just assumed from `postJson`'s
  coverage).
- **`resolveIncident` endpoint client** (`src/api/incidents.test.ts`): exact
  encoded path (`/incidents/{encodeURIComponent(id)}/resolve`, proven
  against space/slash/quote/Unicode/reserved-character incident IDs), no
  body sent, `Accept`/`credentials`/`AbortSignal` as above, a complete
  `RESOLVED` response returned with every field intact, and — the
  endpoint-specific semantic layer — rejection of an otherwise-structurally-
  valid response with a mismatched `incident_id`, a non-`RESOLVED` status
  (`OPEN`, `ACKNOWLEDGED`, or an unrecognized value), or a `null`
  `resolved_at`; the exact `incident_not_found` `404` converted to
  `ApiRequestError` with `code`/`detail` preserved verbatim; malformed-error
  and network-failure handling reused from the shared client conventions.
- **Hook eligibility, concurrency, and reconciliation**
  (`src/hooks/useIncidents.test.ts`): `resolveIncident` issues a `POST` only
  for a matching, exactly-`OPEN` incident while the top-level state is
  `success` — no `POST` for `RESOLVED`/`ACKNOWLEDGED`/an unknown status/a
  missing ID/the `loading` or top-level `error` states; two synchronous
  calls for the same incident produce exactly one `POST` (the
  active-request-ref guard, proven to run before React commits any
  `resolvingIds` state); two different incident IDs resolve independently,
  each with its own pending/error entry, and one's failure never touches
  the other's state; a successful response replaces only the matching array
  element, preserving order and every unrelated element's object identity,
  leaving `lastUpdatedAt` untouched, with zero `GET /incidents` calls; a
  failure leaves the array and top-level state untouched, storing a
  controlled message under only that incident's key; a retry clears the
  prior error and can succeed; unmount aborts every active resolution
  controller in addition to the list-fetch controller, and a late
  (superseded) completion produces no state update and no React act
  warning; a stale completion can never clear a newer retry's own
  in-progress state. A dedicated, non-exported `pickIncident`/
  `mergeIncidentLists` reconciliation suite proves: parsed-instant
  comparison (`Date.parse`, never lexicographic string ordering — proven
  with a deliberately-unparseable timestamp that would sort incorrectly
  under naive string comparison); an older resolve response never
  overwriting a newer current incident; a newer resolve response replacing
  it; an equal-instant tie preferring `RESOLVED` over non-`RESOLVED` on
  both the resolve-response and the `GET`-refresh path; incoming-only
  incidents added; current-only incidents retained and appended in their
  prior order after the incoming list; and a `GET` refresh reconciling
  correctly around an in-flight/just-completed resolution.
- **Component/dashboard rendering and accessibility**
  (`src/pages/IncidentDashboard.test.tsx`, `within(article)`-scoped
  throughout so an assertion can never accidentally target another
  incident's card): a "Resolve incident" button renders only for exact
  `OPEN`, never for `RESOLVED`/`ACKNOWLEDGED`/an unknown status; `Updated`
  renders unconditionally from `updated_at` via a semantic `<time
  dateTime=...>` element and `Resolved` renders only when `resolved_at` is
  populated, both asserted via the `dateTime` source value, never a
  locale-formatted string; clicking Resolve sends exactly one `POST` with
  no body; the clicked card's button natively disables and shows
  "Resolving…" while an unrelated card's button stays enabled, and two
  different incidents can both show pending simultaneously; a rapid second
  interaction (blocked by the now-native-`disabled` button) sends no second
  `POST`; success renders the returned `RESOLVED` incident, removes the
  button, renders the new timestamps, leaves an unrelated card and card
  order unchanged, performs zero additional `GET`s, and leaves the
  dashboard's Refresh/configuration-submission controls visible; failure
  renders a `role="alert"` scoped to only the affected card, leaves the
  incident `OPEN`, re-enables the button, performs zero additional `GET`s,
  and leaves dashboard-level data visible; a retry after failure starts a
  second `POST`, clears the previous error, and a later success renders
  `RESOLVED`. Existing loading/top-level-error/manual-refresh/
  configuration-submission-refresh regression tests (already present before
  Day 7B) continue to pass unmodified except for one intentional correction
  (below).
- **One corrected pre-existing test.** `IncidentDashboard.test.tsx`'s
  refresh-replaces-data test previously asserted that a `GET` refresh
  response fully replaces the incident list; Day 7B's GET-reconciliation
  contract (current-only-incident retention, above) makes that assumption
  incorrect by design, so the test was renamed and its expectation updated
  to assert both the incoming and the retained current-only incident are
  present — not a behavior regression, a corrected assumption.

---

## 8. Test Data and Fixture Strategy

- **Vendor config fixtures** — at least two valid Cisco IOS-XE fixtures
  (`tests/fixtures/configs/cisco/`), each covering interfaces, a BGP
  neighbor, a static route, and ACLs (R-01/A-07):
  - `cisco_missing_required_acl.txt` — drives the vertical-slice incident
    path (`ACL-EXTERNAL-IN` not assigned).
  - `cisco_required_acl_assigned.txt` — proves ACL normalization and the
    satisfied-policy path (`ACL-EXTERNAL-IN` correctly assigned inbound
    on `GigabitEthernet0/1`).

  Plus one Arista EOS fixture (later slice). Malformed variants for the
  parser contract (Section 10) live alongside as `*-malformed.txt`.
- **Policy fixtures** — `ConfigurationPolicy` fixtures are plain factory
  functions, not files; one fixture is the vertical slice's
  `policy-acl-external-in` policy (domain-model.md Section 18).
- **Telemetry fixtures** — factory functions parameterized by timestamp,
  covering each rule's trigger sequence (later slice).
- **No shared mutable fixture state.** Every test constructs its own
  repositories/fixtures (product-spec R-05).
- **Builders for entities with many optional fields** — a small,
  test-only `NormalizedConfigBuilder`-style helper may construct a
  minimal valid config and override only the field under test.

---

## 9. Database (Persistence) Testing Strategy

Per ADR-0002: PostgreSQL via SQLAlchemy is the production persistence
layer; in-memory implementations of the same repository interfaces exist
only as fast test doubles for unit/integration tests.

- **Repository conformance tests.** One shared test suite, parameterized
  over both the in-memory and SQLAlchemy implementations (domain-model.md
  Section 12), asserts both satisfy the identical contract: `DeviceRepository.
  save` then `get_by_id` returns what was saved, and every rejected
  lifecycle transition raises `DeviceConflictError`, unchanged
  (Day 4B2); `ConfigurationSnapshotRepository.add` then `get_by_id` returns
  what was added, a duplicate `snapshot_id` raises
  `SnapshotAlreadyExistsError`, and an unknown `device_id` raises
  `ReferencedDeviceNotFoundError`; `ConfigurationPolicyRepository.
  get_applicable_to_device` reflects all seeded, applicable policies, and
  `seed_if_missing` is a no-op for semantically identical content
  (ignoring `created_at`) and raises `PolicySeedConflictError` otherwise,
  all-or-nothing per call; `upsert_open_incident` returns the right
  `IncidentUpsertResult.outcome` for both branches, replaces `severity`/
  `evidence`/`recommendation`/`last_seen_at` and increments
  `occurrence_count` by exactly one on every repeated match, preserves
  `incident_id`/`fingerprint`/`created_at`/`status`/`device_id`/`source`/
  `rule_ref`/`affected_resource`, rejects a stale observation
  (`ValueError`, no mutation) while accepting equal timestamps, rejects a
  fingerprint/observed_at inconsistent with the candidate or an
  unsupported `candidate.source`, and never leaves two `OPEN` rows for one
  fingerprint, including under concurrency (below) (Day 4B3);
  `TelemetryRepository.get_recent` returns only samples within the
  requested window; `UnitOfWork.commit()` publishes everything staged
  across all four repositories in one call, `rollback()` discards it, and
  a fresh `UnitOfWork` against the same committed state sees exactly what
  was committed (Day 4B3). The SQLAlchemy side runs against a real,
  ephemeral PostgreSQL instance (transaction-per-test rollback) — an
  integration-level test, since it touches a real database.
- **Test isolation.** Unit/integration/contract tests each construct a
  fresh in-memory `UnitOfWork` per test (R-05). The SQLAlchemy conformance
  suite uses a transaction-per-test rollback so tests never see another
  test's rows; `SqlAlchemyUnitOfWork` contract tests that must call a real
  `commit()` and still roll back at teardown join every `Session` to the
  same outer transaction as a SAVEPOINT participant (SQLAlchemy 2.0's
  `join_transaction_mode="create_savepoint"`), so `commit()` releases the
  SAVEPOINT rather than the real transaction (Day 4B3).
- **Failure-path testing without a real outage.** Hand-written test
  doubles, not mocking-framework mocks: a `FailingRepository` whose
  `upsert_open_incident` raises (for testing a failure specifically at the
  incident write, Day 5+), and — for `SqlAlchemyUnitOfWork.commit()`'s own
  contract test (Day 4B3) — a `Session.commit` swapped for a function that
  raises, proving `commit()` rolls back and re-raises the original
  exception unchanged, with no partial state left behind. Both convert to
  the controlled `persistence_error` / 500 response (architecture.md
  Section 12) — now implemented (Day 5B), never a leaked stack trace.
  Deliberately fast and in-process — simulating a real Postgres outage is
  out of proportion to what these tests need to prove.
- **Concurrency test proving atomic deduplication.**
  `test_incident_repository_sqlalchemy__concurrent_upsert_same_fingerprint__yields_one_open`
  (`tests/integration/persistence/test_incident_repository_concurrency.py`):
  four worker threads, each with its own connection/Session/repository
  instance, call `upsert_open_incident` with the same fingerprint
  synchronized by a `threading.Barrier`, each committing explicitly. The
  test asserts exactly one `CREATED` outcome, every other successful
  outcome `UPDATED`, no unhandled unique-violation exception, all four
  results sharing one persisted `incident_id`, exactly one `OPEN` row
  afterward, and `occurrence_count == 4`. **This proves the guarantee at
  the repository level** — it is not a test of two full concurrent HTTP
  ingestion requests, which would need its own integration test and is not
  part of Slice 1. Runs against real PostgreSQL (the partial unique index,
  architecture.md Section 11, is what enforces it); a four-worker,
  lock-based version
  (`tests/unit/persistence/test_in_memory_incident_concurrency.py`) runs
  against the in-memory implementation to confirm it honors the same
  contract without a database constraint. This is the named test behind
  AC-11's concurrency clause.
- E2E tests (Section 7) exercise the real SQLAlchemy repositories against
  a real Postgres container by construction.

---

## 10. Vendor Adapter Testing Strategy

Tests architecture.md Section 5's contract:
`VendorConfigAdapter.parse(raw_text) -> NormalizedConfiguration | ParseError`.

- **One adapter, one test module**, no shared fixtures between
  `test_cisco_adapter.py` and `test_arista_adapter.py` (NFR-01).
- **Golden-file parsing tests** — the fixture config parsed once, asserted
  against a fully-specified expected `NormalizedConfiguration` (every
  field, not a subset).
- **Deterministic normalization** —
  `test_cisco_adapter__same_input_parsed_twice__returns_equal_normalized_config`:
  parses the same fixture text twice (in two separate calls, not reusing
  any cached result) and asserts the two `NormalizedConfiguration` values
  are equal — the concrete proof behind AC-01's determinism clause and
  domain-model.md invariant 10.
- **Malformed input tests — one named test per contract category**
  (architecture.md Section 5.1's parser contract; this is the full
  contract, not just "an unterminated interface block"). Slice 1
  implements at minimum the starred subset; the remainder complete FR-02
  before it is considered done:

  | Test | Contract category | Slice 1 minimum? |
  |---|---|---|
  | `test_cisco_adapter__empty_input__returns_parse_error` | Empty input | `*` |
  | `test_cisco_adapter__whitespace_only_input__returns_parse_error` | Whitespace-only input | `*` |
  | `test_cisco_adapter__missing_hostname__returns_parse_error` | Missing `hostname` | `*` |
  | `test_cisco_adapter__malformed_hostname_declaration__returns_parse_error` | Malformed `hostname` | |
  | `test_cisco_adapter__malformed_interface_declaration__returns_parse_error` | Malformed `interface` (includes, but is not limited to, an unterminated block) | |
  | `test_cisco_adapter__invalid_interface_ip_address__returns_parse_error` | Invalid interface IP address | `*` |
  | `test_cisco_adapter__invalid_interface_subnet_mask__returns_parse_error` | Invalid interface subnet mask | `*` |
  | `test_cisco_adapter__invalid_acl_direction__returns_parse_error` | Invalid `ip access-group` direction | |
  | `test_cisco_adapter__acl_assignment_references_undeclared_acl__returns_parse_error` | ACL assignment references an undeclared ACL | |
  | `test_cisco_adapter__invalid_bgp_neighbor_ip__returns_parse_error` | BGP `neighbor` line with an invalid IPv4 neighbor address | |
  | `test_cisco_adapter__non_integer_bgp_remote_as__returns_parse_error` | BGP `neighbor` line with a non-integer remote AS | |
  | `test_cisco_adapter__non_positive_bgp_remote_as__returns_parse_error` | BGP `neighbor` line with a zero or negative remote AS | |

  Every case asserts `ParseError`, never an escaped exception or a
  partial `NormalizedConfiguration`.
- **Registry resolution tests** — `AdapterRegistry.resolve("unknown-vendor")`
  produces `UnsupportedVendorError`, not a `KeyError`.
- **Unsupported-but-plausible syntax is ignored, not rejected** —
  `test_cisco_adapter__unknown_well_formed_command__is_ignored_not_rejected`:
  a line the parser doesn't recognize does not cause a `ParseError` by
  itself.

---

## 11. Configuration Normalization Testing

- **Cross-vendor equivalence** (later slice) — Cisco and Arista fixtures
  describing an equivalent device normalize to structurally equal
  `NormalizedConfiguration`s (AC-02).
- **Interface + ACL assignment normalization** — a Cisco interface with
  `ip access-group ACL-EXTERNAL-IN in` normalizes to
  `Interface.acl_in == "ACL-EXTERNAL-IN"`, and that ACL exists in
  `acls` (domain-model.md invariant 3). This is the concrete
  "normalized interface and ACL assignment" test (Section 19).
- **Field-level assertions** alongside golden-file tests (e.g., MTU parses
  as `int`; a missing `ip access-group` line normalizes to `acl_in = null`).

---

## 12. Policy and Drift Detection Testing

- **`PolicyEvaluator` — satisfied rule → no violation.** A config with the
  required ACL correctly assigned produces an empty violation list
  (AC-03; Section 19).
- **`PolicyEvaluator` — unsatisfied rule → exactly one violation.** Four
  sub-cases, each asserting one violation with the correct `rule_ref`,
  `affected_resource`, `severity`, `evidence`
  (`AclAssignmentEvidence.expected_acl_name`/`actual_acl_name`/
  `interface_name`/`direction`), and `recommendation`
  (domain-model.md Section 7): ACL entirely absent
  (`MISSING_REQUIRED_ACL`, `actual_acl_name = null`); ACL present but
  unassigned in that direction (`MISSING_REQUIRED_ACL`,
  `actual_acl_name = null`); a different ACL assigned in that direction
  (`MISSING_REQUIRED_ACL`, `actual_acl_name` = that ACL's name); the
  target interface absent entirely (`TARGET_INTERFACE_MISSING`,
  `actual_acl_name = null`) — a missing interface must never be silently
  treated as satisfying the rule. The first is AC-04 / Section 19's core
  test.
- **`PolicyEvaluator` — no matching policy → no violation** regardless of
  config content.
- **Explicit time, not the system clock** —
  `test_policy_evaluator__given_observed_at__populates_violation_detected_at`:
  calling `evaluate` with an explicit `observed_at` argument asserts every
  returned `ConfigurationViolation.detected_at` equals that exact value —
  proving the evaluator never reads a clock itself (architecture.md
  Section 4.1). No `FixedClock` is involved: `PolicyEvaluator` takes
  `observed_at` as a plain argument, so the test supplies a literal
  `datetime` directly.
- **`DriftDetector` — baseline == current → empty report** (AC-06, later
  slice) — first submission's `Device.baseline_snapshot_id ==
  current_snapshot_id`, so `compare` is called with identical values.
- **`DriftDetector` — removed ACL vs. baseline → `removed` entry**
  (AC-05, later slice).
- All unit tests (Section 4) — plain values in, plain values out.

---

## 13. Incident Generation Testing

- **Unit — candidate + fingerprint (Day 4A).** `IncidentFactory.build_candidate(violation)`
  produces the exact `PolicyViolationIncidentEvidence` shape from
  domain-model.md Section 7 (no duplicated `device_id`/`rule_ref`;
  `evidence.source_snapshot_id` and `evidence.actual_acl_name` straight
  from the violation), `severity = "Medium"`, `affected_resource` and
  `recommendation` copied verbatim (plain string, not a
  `recommendation.summary` object — domain-model.md Section 13), and
  `observed_at` equal to `violation.detected_at`.
  `compute_fingerprint` is tested for both correctness (same
  inputs → identical fingerprint; any differing input → a different one)
  and **collision safety** —
  `test_compute_fingerprint__delimiter_quote_escape_and_unicode_values__remain_unambiguous`:
  distinct input tuples that *would* collide under a naive
  `"|"`-delimited join (e.g., a `"|"` split across two fields vs.
  contained within one, or values containing `":"`, quotes, backslashes,
  or non-ASCII text) must still hash to different fingerprints — proving
  the SHA-256-over-canonical-JSON construction
  (domain-model.md Section 11) actually avoids the ambiguity a
  delimiter join would risk.
- **Integration — satisfied policy, zero everything.**
  `test_config_ingestion_service__satisfied_policy__returns_zero_counts_and_creates_no_incident`:
  ingesting `cisco_required_acl_assigned.txt` (Section 8) through the full
  `ConfigIngestionService` asserts `violations_detected == 0`,
  `incidents_created == 0`, `incidents_updated == 0`, and
  `IncidentRepository` remains empty. This is the integration-level proof
  for AC-03, distinct from Section 12's unit-level `PolicyEvaluator` test.
- **Integration — the application result distinguishes create from
  update, not a leaked `IncidentUpsertResult`.**
  `test_config_ingestion_service__new_incident__reports_created_count`
  and `test_config_ingestion_service__repeated_finding__reports_updated_count`:
  `ConfigIngestionService.ingest` returns a `ConfigIngestionResult`
  (architecture.md Section 4), never the repository's
  `IncidentUpsertResult` — so these tests assert on
  `ConfigIngestionResult.incidents_created`/`incidents_updated`, not on an
  `.outcome` field the service doesn't expose. The first call for a
  violating config returns `incidents_created == 1, incidents_updated ==
  0`; the underlying `Incident` (fetched via `uow.incidents`, the same
  `UnitOfWork` the call used) has `device_id`, `source`, `rule_ref`,
  `severity`, `evidence`, `recommendation`, `created_at`, `last_seen_at`,
  `occurrence_count == 1` all populated (AC-04). A second `ingest` call
  for the identical config returns `incidents_created == 0,
  incidents_updated == 1`, and the same `Incident` now has
  `occurrence_count == 2` — still exactly **one** row (AC-11). This second
  call is the **only** place in Slice 1's suite that submits a config
  twice — the primary demonstration stays single-submission.
  **`IncidentUpsertResult.outcome` itself is asserted directly only by the
  repository conformance tests (Section 9)**, which call
  `upsert_open_incident` without going through `ConfigIngestionService`.
- **Integration — log emitted per outcome, only after commit.**
  `test_config_ingestion_service__created_incident__emits_created_log_after_commit`
  and `test_config_ingestion_service__updated_incident__emits_updated_log_after_commit`:
  each captures the in-memory log recorder (Section 5) and asserts one
  JSON line with `incident_id`, `device_id`, `rule_ref`, `severity`,
  `status`, `outcome`, `timestamp` (AC-10), emitted only after `ingest`
  returns successfully — explicitly **integration-level**, not something
  the HTTP E2E test (Section 7) can claim, since it never inspects stdout.
- **Integration — commit failure suppresses the log.**
  `test_config_ingestion_service__commit_failure__rolls_back_and_emits_no_incident_log`:
  using a `FailingUnitOfWork` (Section 9) so `commit()` raises *after*
  `PolicyEvaluator` already found a violation, the test asserts the log
  recorder captured **zero** lines, and — inspecting `uow.incidents` on
  that **same** `FailingUnitOfWork` instance after `rollback()`, not a
  separate, unrelated `IncidentRepository` that would trivially be empty
  and prove nothing — confirms no row remains for that fingerprint. This
  is the concrete proof that `rollback()` actually discards what was
  written, not just that some other repository was never touched, and it
  is what backs architecture.md Section 12's "no log without a successful
  commit" rule.

**Day 5A implementation note.** The two `test_config_ingestion_service__
..._log_after_commit`/`..._emits_no_incident_log` cases above describe the
eventual full-slice suite once structured logging exists (architecture.md
Section 4's step 12, deferred — see "Documentation corrections applied for
Day 5A" in CLAUDE.md); Day 5A does not implement logging, so no such tests
exist yet. Every other guarantee in this section — satisfied policy
yielding zero counts, `ConfigIngestionResult` distinguishing created from
updated, `occurrence_count` incrementing on a repeat, exactly one row
persisting — is covered now, with different (more granular) test names
than sketched above, in `tests/unit/application/
test_config_ingestion_service.py` (against a real `InMemoryUnitOfWork`)
and `tests/integration/application/test_config_ingestion_postgres.py`
(three focused tests proving atomic commit and atomic rollback-after-
late-failure against real PostgreSQL). `tests/unit/application/
test_config_ingestion_models.py` covers `IngestConfigurationCommand`/
`ConfigIngestionResult` validation directly.

---

## 14. Error and Failure-Path Testing

**Corrected for Day 5B (binding over the table this superseded):** no
success/error envelope; `code` is lowercase snake_case; the message field
is `detail`, not `message`; `UnsupportedVendorError`/
`ConfigurationParseError` map to 422, not 400; persistence conflicts get
their own 409 category, not folded into a generic 500. Actual test names
also differ from the `test_config_api__...` names sketched in earlier
planning — see `tests/contract/api/test_config_ingestion_api.py` and
`tests/contract/api/test_incidents_api.py` for the real ones.

| Error category | Produced at | Status | `code` | Test (actual) |
|---|---|---|---|---|
| Malformed request schema | `api` (Pydantic `RequestValidationError`) | 422 | FastAPI's own default body | `test_submit_configuration__blank_vendor__returns_422`, `..._unknown_body_field__rejected`, etc. |
| Unsupported vendor | `domain` (`AdapterRegistry.resolve`) | 422 | `unsupported_vendor` | `test_submit_configuration__unsupported_vendor__returns_422_unsupported_vendor` |
| Configuration parse failure | adapter, wrapped by `application` (`ConfigurationParseError`) | 422 | `configuration_parse_error` | Unit: Section 10's parser-contract table. Contract: `test_submit_configuration__parse_failure__returns_422_configuration_parse_error` |
| Device vendor/timestamp conflict | `persistence` (`DeviceConflictError`) | 409 | `device_conflict` | `test_submit_configuration__device_conflict__returns_409_device_conflict` |
| Duplicate snapshot | `persistence` (`SnapshotAlreadyExistsError`) | 409 | `snapshot_already_exists` | `test_submit_configuration__duplicate_snapshot__returns_409_snapshot_already_exists` |
| Referenced device missing | `persistence` (`ReferencedDeviceNotFoundError`) | 409 | `referenced_device_not_found` | `test_submit_configuration__referenced_device_not_found__returns_409` |
| Other caller/application `ValueError` | `application` | 422 | `invalid_request` | `test_submit_configuration__invalid_generated_snapshot_id__returns_422_invalid_request` |
| Resource not found | `application` | 404 | `NOT_FOUND` | **Not in Slice 1** — Slice 1 has no single-resource `GET` endpoint (architecture.md Section 10); this test is added once `GET /devices/{id}` or `GET /incidents/{id}` ships (later slice). |
| Persistence failure (base) | `persistence` (`PersistenceError`, registered after the conflict subclasses above) | 500 | `persistence_error` | `test_submit_configuration__persistence_failure__returns_generic_500` |
| Serialization failure | `persistence` (`SerializationError`) | 500 | `serialization_error` | `test_submit_configuration__serialization_failure__returns_generic_500` |
| Invalid injected clock (server-composition failure, not caller input) | `api` (`InvalidClockError`, deliberately unmapped) | 500 | none — falls through to unmapped-exception handling | `test_submit_configuration__invalid_clock__returns_generic_500_and_persists_nothing` |
| Unexpected/unmapped exception | anywhere | 500 | none — FastAPI's normal production 500 behavior | `test_submit_configuration__unexpected_exception__returns_generic_production_500` |

- Every failure-path contract test asserts the **direct `ApiErrorResponse`
  body** (`{"code", "detail"}`), not an envelope — this is Section 19's
  "structured invalid-input response" test and AC-12 generally.
- Failure paths get the same test rigor as success paths (Testing Goal 5).
- `test_submit_configuration__unexpected_exception__returns_generic_production_500`
  and the invalid-clock test above use `TestClient(app,
  raise_server_exceptions=False)` and a hand-written failing `UnitOfWork`
  double to prove the framework's own unmapped-exception 500 behavior is
  reached — Day 5B deliberately installs **no** broad catch-all handler
  that would echo exception internals, unlike the generic-envelope
  catch-all this table originally sketched.

---

## 15. CI Quality Gates (GitHub Actions)

Fail-fast, in order, matching AC-13:

1. **Static checks** — lint + type-check (`ruff`/`mypy` or equivalent; the
   exact tool choice is an implementation detail not fixed here).
2. **Unit tests** (Section 4) — must be fully green before later gates run.
3. **Integration tests** (Section 5).
4. **API contract tests** (Section 6).
5. **Repository conformance suite, SQLAlchemy side** (Section 9) —
   requires a PostgreSQL service container in the workflow.
6. **E2E vertical-slice tests** (Section 7) — builds and runs the full
   Docker Compose stack; slowest gate, runs last.

All six gates block merge to `main` — given the hackathon time constraint
(R-04), a smaller always-green suite is preferred over a larger one with a
permanently-red, ignored gate.

---

## 16. Coverage Expectations

| Layer | Target (line coverage) | Rationale |
|---|---|---|
| `domain`, `detection` | ≥ 90% | Pure logic; cheapest to test exhaustively, and correctness matters most here. |
| vendor adapters | ≥ 85% | Every field in `NormalizedConfiguration` exercised by ≥ 1 fixture. |
| `application` | ≥ 80% | Happy path + ≥ 1 failure path per use case. |
| `api` | ≥ 70% | Thin mapping layer; contract tests cover most of it. |
| `persistence` | ≥ 70% | In-memory implementations are simple; conformance tests (Section 9) cover the interesting behavior. |

Coverage is a floor, not a goal (NFR-04) — no FR/AC may rely on incidental
coverage from an unrelated test; each has a named test in Section 19 or
the relevant level section.

---

## 17. Test Naming Conventions

**pytest:** `test_<unit_under_test>__<scenario>__<expected_outcome>`

- `test_cisco_adapter__valid_config__returns_normalized_config`
- `test_cisco_adapter__malformed_interface_declaration__returns_parse_error`
- `test_adapter_registry__unknown_vendor__returns_unsupported_vendor_error`
- `test_policy_evaluator__acl_assigned_correctly__no_violations`
- `test_policy_evaluator__acl_missing__one_missing_acl_violation`
- `test_incident_repository__upsert_open_incident_then_get_by_id__returns_saved_incident`
- `test_config_ingestion_service__repeated_submission__updates_existing_open_incident_not_duplicate`
- `test_incidents_api__get_incidents__returns_created_incident`
- `test_incidents_api__empty_store__returns_empty_list`
- `test_config_api__persistence_failure__returns_controlled_500`
- `test_config_api__invalid_body__returns_structured_validation_error`
- `test_config_api__unexpected_exception__returns_internal_error`

Test files mirror the module under test.

**Playwright (E2E):**

```ts
test.describe("vertical slice: cisco config -> missing ACL -> incident", () => {
  test("should surface the missing-ACL incident via GET /incidents", async ({ request }) => { ... });
});
```

**Vitest (frontend, later slice):** same `describe`/`it("should ...")` convention.

---

## 18. Explicitly Deferred Testing Concerns

- **Load / performance testing** — no throughput/latency/scale testing;
  the MVP is a prototype, not a hyperscale deployment (product-spec
  Section 1).
- **Security / penetration testing** — no auth to test yet.
- **Browser compatibility matrices** — moot until FR-10 ships, and out of
  scope even then unless separately requested.
- **Multi-vendor breadth beyond Cisco + Arista.**
- **Chaos/fault-injection beyond `FailingRepository`** — no network
  partition or multi-instance testing (single backend instance, single
  Postgres instance).
- **Mutation / property-based testing** — ordinary example-based tests are
  sufficient given the timeframe (R-04); a reasonable post-MVP addition,
  not designed here.
- **Config replay/re-parse testing** — domain-model.md Section 4 notes
  this capability is intentionally unbuilt.

---

## 19. First Vertical Slice — Concrete Acceptance Tests

**The primary demonstration is one configuration submission.** Tests 9
and 10 below are the deliberate exception: each issues a **second**
`POST` of the identical config specifically to prove dedup outcome/count
behavior (AC-11) — the suite as a whole is not "one submission only,"
only the *headline* path is. "Real components" lists only what's notable
beyond Sections 4–7's stated defaults.

**Slice 1 uses exactly two endpoints** (product-spec Section 11):
`POST /devices/{id}/config` and `GET /incidents`. No test below targets
`GET /devices`, `GET /devices/{id}`, or `GET /incidents/{id}` — those are
deferred (architecture.md Section 10), and a not-found test is deferred
alongside them (Section 14).

| # | Test | Level | Mocked | Location |
|---|---|---|---|---|
| 1 | Valid Cisco configuration parsing — `CiscoAdapter.parse(fixture)` returns a fully-populated `NormalizedConfiguration` | Unit | none | `tests/unit/adapters/test_cisco_adapter.py` |
| 2 | `test_cisco_adapter__same_input_parsed_twice__returns_equal_normalized_config` — deterministic normalization (AC-01) | Unit | none | `tests/unit/adapters/test_cisco_adapter.py` |
| 3 | Malformed Cisco configuration rejection — one case per parser-contract category (Section 10) returns `ParseError` | Unit | none | `tests/unit/adapters/test_cisco_adapter.py` |
| 4 | Unsupported vendor rejection — `POST` with `vendor: "juniper-junos"` returns 422 / `unsupported_vendor` (Day 5B: not 400) | Contract | none | `tests/contract/api/test_config_ingestion_api.py` |
| 5 | Normalized interface and ACL assignment — the **`201` response's `normalized_config`** (architecture.md Section 10.1; Day 5B: the response body directly, no `data` wrapper), not a follow-up `GET /devices/{id}` | Contract | none | `tests/contract/api/test_config_ingestion_api.py` |
| 6 | Satisfied required-ACL policy creates no violation | Unit | none | `tests/unit/detection/test_policy_evaluator.py` |
| 7 | Missing required ACL creates exactly one violation | Unit | none | `tests/unit/detection/test_policy_evaluator.py` |
| 8 | `test_config_ingestion_service__satisfied_policy__returns_zero_counts_and_creates_no_incident` (AC-03, integration-level) | Integration | none | `tests/integration/application/test_config_ingestion_service.py` |
| 9 | `test_config_ingestion_service__new_incident__reports_created_count` — one complete `Incident`, `ConfigIngestionResult.incidents_created == 1, incidents_updated == 0` | Integration | none | `tests/integration/application/test_config_ingestion_service.py` |
| 10 | `test_config_ingestion_service__repeated_finding__reports_updated_count` — second, identical submission; `incidents_created == 0, incidents_updated == 1`, `occurrence_count == 2`, still one row (AC-11) | Integration | none | `tests/integration/application/test_config_ingestion_service.py` |
| 11 | `GET /incidents` returns the created incident | Contract (+ E2E) | none | `tests/contract/api/test_incidents_api.py`, `e2e/vertical-slice/` |
| 12 | `GET /incidents` returns an empty collection when no incidents exist | Contract | none | `tests/contract/api/test_incidents_api.py` |
| 13 | `test_config_ingestion_service__created_incident__emits_created_log_after_commit` (AC-10) | Integration | in-memory log recorder | `tests/integration/application/test_config_ingestion_service.py` |
| 14 | `test_config_ingestion_service__updated_incident__emits_updated_log_after_commit` (AC-10) | Integration | in-memory log recorder | `tests/integration/application/test_config_ingestion_service.py` |
| 15 | `test_config_ingestion_service__commit_failure__rolls_back_and_emits_no_incident_log` | Integration | `FailingUnitOfWork` | `tests/integration/application/test_config_ingestion_service.py` |
| 16 | Controlled persistence failure — `POST` with a hand-written failing `UnitOfWork` returns 500 / `persistence_error`, no database detail leaked (Day 5B: not `PERSISTENCE_ERROR`) | Contract | hand-written failing `UnitOfWork` (fails on `commit()`) | `tests/contract/api/test_config_ingestion_api.py` |
| 17 | Structured invalid-input response — missing `vendor` or empty body returns 422 via FastAPI's own `RequestValidationError` body (Day 5B: no custom envelope/code) | Contract | none | `tests/contract/api/test_config_ingestion_api.py` |
| 18 | Unexpected exception returns a generic production 500, never a raw stack trace (Day 5B: no custom `INTERNAL_ERROR` handler — FastAPI's own unmapped-exception behavior, asserted via `TestClient(..., raise_server_exceptions=False)`) | Contract | hand-written failing `UnitOfWork` | `tests/contract/api/test_config_ingestion_api.py` |
| 19 | `test_compute_fingerprint__delimiter_quote_escape_and_unicode_values__remain_unambiguous` (AC-11) | Unit | none | `tests/unit/domain/test_incident.py` — `compute_fingerprint` is a `domain` service (domain-model.md Section 17), not a `detection` one; `IncidentFactory`'s own tests live in `tests/unit/detection/test_incident_factory.py` |
| 20 | `test_policy_evaluator__given_observed_at__populates_violation_detected_at` | Unit | none | `tests/unit/detection/test_policy_evaluator.py` |

**E2E** (one Playwright test, Section 7): start `api` + `db` from
`compose.e2e.yml`, wait for both to report healthy, run migrations and
idempotent seeding (architecture.md Section 15.1), `POST`
`cisco_missing_required_acl.txt` for `spine-01` and assert its `201`
response *before* querying anything else, then `GET /incidents` and
assert the same incident is present with `device_id: "spine-01"`,
`severity: "Medium"`, `rule_ref: "policy-acl-external-in"` — proving
tests 1, 5, 7, 9, and 11 compose through a real, deployed instance. The
stack (containers and volumes) is destroyed afterward regardless of
outcome. **This E2E test does not, and must not be described as, proving
test 13, 14, or 15** — it never inspects container stdout, only HTTP
responses; structured-log verification stays an integration-level concern
(Section 13).

```ts
// e2e/vertical-slice/cisco-missing-acl.spec.ts (illustrative shape only)
test("should create and surface an incident for a missing required ACL", async ({ request }) => {
  const postRes = await request.post("/devices/spine-01/config", {
    data: { vendor: "cisco-ios-xe", config_text: fixture },
  });
  expect(postRes.status()).toBe(201);
  const postBody = await postRes.json();
  expect(postBody.data.normalized_config.hostname).toBe("spine-01");
  expect(postBody.data).toMatchObject({
    violations_detected: 1, incidents_created: 1, incidents_updated: 0,
  });

  const getRes = await request.get("/incidents");
  const getBody = await getRes.json();
  expect(getBody.data).toContainEqual(expect.objectContaining({
    device_id: "spine-01",
    severity: "Medium",
    rule_ref: "policy-acl-external-in",
  }));
});
```

### 19.1 Acceptance Criteria → Test Mapping

| AC | Test(s) |
|---|---|
| AC-01 | #1, #2, #5 |
| AC-02 | Arista equivalent of #1 (later slice) |
| AC-03 | #6, #8 |
| AC-04 | #7, #9, #11 |
| AC-05 | `DriftDetector` removed-ACL test, Section 12 (later slice) |
| AC-06 | `DriftDetector` baseline==current test, Section 12 (later slice) |
| AC-07 | RULE-CPU-HIGH test (later slice) |
| AC-08 | RULE-LINK-FLAP test (later slice) |
| AC-09 | RULE-BGP-DOWN test (later slice) |
| AC-10 | #13, #14 |
| AC-11 | #10, #19, Section 9's repository-level concurrency test |
| AC-12 | #16, #17, #18, and the remaining rows of Section 14's error table |
| AC-13 | CI gates, Section 15 |

---

## 20. Incident Resolution Testing (Day 7A)

Day 7A's first incident-lifecycle mutation (`OPEN -> RESOLVED`,
`POST /incidents/{incident_id}/resolve`) was built and verified across five
reviewable gates (7A-A domain/persistence/migration, 7A-B application,
7A-C API, 7A-D recurrence/concurrency, 7A-E this review/documentation
pass), test-first at every gate. Coverage by layer:

### 20.1 Domain (`tests/unit/domain/test_incident.py`)

`Incident.resolve(at)`: `OPEN -> RESOLVED` sets `resolved_at`/`updated_at`
to the exact supplied value; `at` must be UTC-aware; `at` must not precede
the incident's *current* `updated_at` (not `last_seen_at` — a correctness
patch applied within Gate 7A-A after an initial `last_seen_at`-only check
was found to permit moving `updated_at` backward when `last_seen_at <
updated_at`, which is otherwise a legal state); all unrelated fields
unchanged; already-`RESOLVED` is a true no-op that returns before any
timestamp validation; the dormant `ACKNOWLEDGED` status raises rather than
resolving. Constructor invariants:
`created_at <= last_seen_at <= updated_at`, `resolved_at <= updated_at`
when present, and `status == RESOLVED` iff `resolved_at` is set.

### 20.2 Application (`tests/unit/application/test_incident_resolution.py`)

`ResolveIncidentService` against hand-written fakes/spies (no mocking
library), mirroring `ConfigIngestionService`'s/`ListIncidentsService`'s
existing lifecycle-test style: an `OPEN` incident calls the injected
`Clock.now()` exactly once, calls `uow.incidents.resolve()` with the
incident ID and that exact captured value, commits once, and returns the
persisted result; an already-`RESOLVED` incident calls the `Clock` zero
times (proven with a clock that raises if invoked at all), performs no
repository write, and never commits; an unknown incident raises
`IncidentNotFoundError` (preserving `.incident_id`) with no `Clock` call
and no commit; a repository result of `None` after the initial read is
also treated as not-found; a concurrently-already-resolved result from the
repository is accepted and returned, still with only one `Clock` call;
repository and commit failures both preserve the original exception through
the existing rollback/close-with-notes convention, never a second commit.

### 20.3 In-memory and SQL repository contract
(`tests/contract/persistence/test_incident_repository_contract.py`,
parameterized over both implementations)

`IncidentRepository.resolve(incident_id, resolved_at)`: resolves an `OPEN`
incident; returns an already-`RESOLVED` incident unchanged; returns `None`
for an unknown ID; preserves every detection-owned/immutable field
(`fingerprint`, `device_id`, `rule_ref`, `affected_resource`, `severity`,
`evidence`, `created_at`, `last_seen_at`, `occurrence_count`); a naive
timestamp is rejected without mutation; a timestamp earlier than the
persisted `updated_at` is rejected without mutation; a timestamp exactly
equal to `updated_at` is accepted (boundary case). Ordering B (a committed
`upsert_open_incident` update, then a later `resolve()`) and its stale-clock
variant (a `resolve()` attempt using a timestamp earlier than an
already-ingestion-advanced `updated_at`) are both proven here, distinct
from the fresh-incident stale-timestamp case, since the comparison baseline
here was advanced by a separate ingestion call.

### 20.4 Migration (`tests/integration/persistence/test_migrations.py`)

Upgrade from revision `0001_slice1_persistence` to `0002_incident_
resolution` adds `updated_at` (backfilled from `last_seen_at` for a
pre-existing row, then non-null) and `resolved_at` (nullable); the new
`ck_incidents_updated_at_after_last_seen_at`,
`ck_incidents_resolved_at_matches_status`, and
`ck_incidents_resolved_at_before_or_equal_updated_at` CHECK constraints are
present and enforced; the partial unique index
`ux_incidents_open_fingerprint` remains present and functional, unchanged;
downgrade to `0001` drops the new columns/constraints cleanly, and
upgrading to head again succeeds a second time.

### 20.5 API/OpenAPI contract
(`tests/contract/api/test_incident_resolution_api.py`,
`tests/contract/api/test_openapi_contract.py`,
`tests/unit/api/test_clock.py`)

`POST /incidents/{incident_id}/resolve` returns `200` with a direct,
complete `IncidentResponse` (no wrapper); requires no request body;
repeated resolution returns `200` with unchanged `resolved_at`/`updated_at`
and a `Clock` that is not called again; an unknown ID returns the exact
`{"code": "incident_not_found", "detail": "Incident '<id>' was not
found."}` body at `404`; `GET /incidents` remains unfiltered, showing both
`OPEN` (`resolved_at: null`) and `RESOLVED` incidents. OpenAPI: the path
exists with `operationId: "resolve_incident"`, documents `200`
(`IncidentResponse`) and `404` (`ApiErrorResponse`), has no `requestBody`
key, and `IncidentResponse`'s schema includes `updated_at`
(required) and `resolved_at` (a required key whose value schema is
nullable — `anyOf` including `type: null`, the standard Pydantic v2 shape
for `datetime | None`). `CallableClock` (the production adapter reusing
`create_app`'s existing `clock` parameter) has its own focused unit tests:
delegates to the wrapped callable, and rejects a naive or non-UTC value via
the existing `InvalidClockError`.

### 20.6 Real PostgreSQL HTTP resolution
(`tests/integration/api/test_api_postgres.py`)

The same success/idempotent/404 behavior above, driven through the real
FastAPI app, a real `SqlAlchemyUnitOfWork`, and a real database — plus the
binding reingestion-after-resolution scenario (Section 20.7) at the HTTP
level.

### 20.7 Recurrence after resolution
(`tests/integration/application/test_incident_resolution_reingestion_postgres.py`,
`tests/integration/api/test_api_postgres.py`)

Real PostgreSQL, through the supported `ConfigIngestionService`/
`ResolveIncidentService` (and, separately, real HTTP) entry points — never
raw SQL: ingest an incident at T0, resolve it at T1, reingest the identical
still-invalid configuration at T2. Asserts the original incident is
`RESOLVED`, unchanged (`resolved_at == updated_at == T1`, `created_at`/
`last_seen_at`/`occurrence_count`/`fingerprint`/`evidence`/`severity` all
as originally created), and that a **new** `OPEN` incident is created (new
`incident_id`, same fingerprint, `occurrence_count: 1`, its own
`created_at`/`last_seen_at`/`updated_at == T2`). This is the concrete proof
that the partial unique index (`WHERE status = 'OPEN'`) already excludes a
resolved row — no index or migration change was needed for this behavior.

### 20.8 New OPEN deduplication (same test files as 20.7)

After the new `OPEN` incident exists, a third reingestion at T3 increments
its `occurrence_count` to 2, advances `last_seen_at`/`updated_at` to T3,
creates no third row, and leaves the original `RESOLVED` incident
completely untouched — proving Day 7A did not weaken the existing `OPEN`
dedup invariant.

### 20.9 Partial-index invariant (Section 20.7/20.8, plus Section 9's
existing migration/index tests)

The existing migration tests are the schema source of truth
(`ux_incidents_open_fingerprint`, `UNIQUE (fingerprint) WHERE status =
'OPEN'`, unchanged since Day 4B1). The Day 7A behavioral tests verify its
*effect*: one `RESOLVED` and one `OPEN` row may share a fingerprint; two
`OPEN` rows may never share one (enforced, unchanged, by the index itself);
repeated ingestion always targets the single `OPEN` row for that
fingerprint.

### 20.10 Concurrent resolution
(`tests/integration/persistence/test_incident_repository_concurrency.py`)

Two worker threads, each with its own connection/Session/repository
instance, synchronized with a `threading.Barrier` (never a sleep), both
call `resolve()` on the same `OPEN` incident with the identical resolution
timestamp and commit explicitly. No unhandled exception escapes either
worker; both receive a `RESOLVED` incident with identical `resolved_at`/
`updated_at`; the final database row is `RESOLVED`, with
`resolved_at == updated_at`, `occurrence_count`/`last_seen_at` unchanged
from before resolution, exactly one row for that `incident_id`/fingerprint,
and zero remaining `OPEN` rows for that fingerprint. No lock, retry, queue,
or isolation-level change was introduced — the existing atomic conditional
`UPDATE` (Section 9's own precedent) was already sufficient.

### 20.11 Committed ingestion/resolution orderings
(`tests/contract/persistence/test_incident_repository_contract.py`)

Ordering A (resolve commits first, then reingestion) is Section 20.7/20.8
above. Ordering B (a committed `upsert_open_incident` update commits
first, then a later `resolve()`): the resolution succeeds against the
already-advanced row, leaving `occurrence_count`/`last_seen_at` exactly as
ingestion left them, touching only `status`/`resolved_at`/`updated_at`.

### 20.12 Stale-clock rejection (same test file as 20.11)

A `resolve()` attempt using a timestamp earlier than an
ingestion-since-advanced `updated_at` raises `ValueError`, leaves the
incident `OPEN` and unmutated (`occurrence_count`/`updated_at`/
`last_seen_at` retain their ingestion values), and creates no duplicate
incident.

### 20.13 Frontend and browser regression (unchanged suites)

The existing 176 Vitest tests (7 files) and the existing runtime response
validator (`isIncidentResponse`) were re-verified against the expanded
`IncidentResponse` shape without any frontend source change: the validator
checks required fields only and does not enumerate/reject unknown keys, so
`updated_at`/`resolved_at` are silently accepted; `status` is validated as
a non-empty string, not a closed enum, so `"RESOLVED"` is accepted like any
other value. The existing Playwright test
(`config-submission-refresh.spec.ts`) continues to pass unchanged — it
still covers configuration submission and refresh only, and does not
resolve an incident during Day 7A. `scripts/compose_smoke.py` and
`scripts/browser_e2e.py` both continue to pass unmodified against the
migrated schema (`0002_incident_resolution` reached at container startup
via the existing `alembic upgrade head` step) and the expanded
`IncidentResponse` — no separate migration or script was added for browser
verification.

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

### 7.1 Vitest (frontend) — not applicable to the first slice

No frontend code exists until FR-10 (later slice). When it ships, its
component/unit tests live in `frontend/tests/` using Vitest, following
the same principles as Sections 2–7 (`describe`/`it("should ...")`,
real components by default).

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
  over both the in-memory and SQLAlchemy `UnitOfWork` implementations
  (domain-model.md Section 12), asserts both satisfy the identical
  contract: `save` then `get_by_id` returns what was saved; `list`
  reflects all saved items; `find_open_by_fingerprint` finds an exact
  match and nothing else; `upsert_open_incident` returns the right
  `IncidentUpsertResult.outcome` for both branches and never leaves two
  `OPEN` rows for one fingerprint, including under concurrency (below);
  `TelemetryRepository.get_recent` returns only samples within the
  requested window; `UnitOfWork.rollback()` discards everything written
  since it opened. The SQLAlchemy side runs against a real, ephemeral
  PostgreSQL instance (transaction-per-test rollback) — an
  integration-level test, since it touches a real database.
- **Test isolation.** Unit/integration/contract tests each construct a
  fresh in-memory `UnitOfWork` per test (R-05). The SQLAlchemy conformance
  suite uses a transaction-per-test rollback so tests never see another
  test's rows.
- **Failure-path testing without a real outage.** Two hand-written test
  doubles, not mocking-framework mocks: a `FailingRepository` whose
  `upsert_open_incident` raises (for testing a failure specifically at the
  incident write), and a `FailingUnitOfWork` whose `commit()` raises (for
  testing that a failed commit rolls back everything and suppresses
  logging, Section 13). Both convert to the controlled `PERSISTENCE_ERROR`
  / 500 response (architecture.md Section 12), never a leaked stack trace.
  Deliberately fast and in-process — simulating a real Postgres outage is
  out of proportion to what these tests need to prove.
- **Concurrency test proving atomic deduplication.**
  `test_incident_repository_sqlalchemy__concurrent_upsert_same_fingerprint__yields_one_open_incident`:
  two DB connections call `upsert_open_incident` with the same fingerprint
  at (as close to) the same instant as the harness can arrange. The test
  asserts exactly one `OPEN` row for that fingerprint afterward, with
  `occurrence_count == 2`. **This proves the guarantee at the repository
  level** — it is not a test of two full concurrent HTTP ingestion
  requests, which would need its own integration test and is not part of
  Slice 1. Runs against real PostgreSQL (the partial unique index,
  architecture.md Section 11, is what enforces it); a parallel,
  lock-based version runs against the in-memory implementation to confirm
  it honors the same contract without a database constraint. This is the
  named test behind AC-11's concurrency clause.
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
- **`PolicyEvaluator` — unsatisfied rule → exactly one violation.** Three
  sub-cases, each asserting one `MISSING_REQUIRED_ACL` violation with the
  correct `acl_name`/`interface_name`/`direction`: ACL entirely absent;
  ACL present but unassigned; ACL assigned to the wrong interface/direction.
  The first is AC-04 / Section 19's core test.
- **`PolicyEvaluator` — no matching policy → no violation** regardless of
  config content.
- **Explicit time, not the system clock** —
  `test_policy_evaluator__fixed_observed_at__populates_violation_detected_at`:
  calling `evaluate` with a `FixedClock`-supplied `observed_at` asserts
  every returned `ConfigurationViolation.detected_at` equals that exact
  value — proving the evaluator never reads a clock itself
  (architecture.md Section 4.1).
- **`DriftDetector` — baseline == current → empty report** (AC-06, later
  slice) — first submission's `Device.baseline_snapshot_id ==
  current_snapshot_id`, so `compare` is called with identical values.
- **`DriftDetector` — removed ACL vs. baseline → `removed` entry**
  (AC-05, later slice).
- All unit tests (Section 4) — plain values in, plain values out.

---

## 13. Incident Generation Testing

- **Unit — candidate + fingerprint.** `IncidentFactory.build_candidate(violation)`
  produces the exact evidence shape from domain-model.md Section 10 (no
  duplicated `device_id`/`policy_id`; `evidence.source_snapshot_id`
  straight from the violation), `severity = "Medium"`, and a
  `recommendation.summary` substring-matching the ACL name, interface, and
  device ID. `compute_fingerprint` is tested for both correctness (same
  inputs → identical fingerprint; any differing input → a different one)
  and **collision safety** —
  `test_compute_fingerprint__delimiter_and_unicode_values__remain_unambiguous`:
  two distinct 4-tuples that *would* collide under a naive
  `"|"`-delimited join (e.g., one where `acl_name` contains a literal
  `"|"` or `":"`, or non-ASCII text) must still hash to different
  fingerprints — proving the SHA-256-over-canonical-JSON construction
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

---

## 14. Error and Failure-Path Testing

| Error category | Produced at | Status | `error.code` | Named test |
|---|---|---|---|---|
| Malformed request schema | `api` (Pydantic) | 422 | `SCHEMA_VALIDATION_ERROR` | `test_config_api__invalid_body__returns_structured_validation_error` |
| Unsupported vendor | `application` (`AdapterRegistry`) | 400 | `UNSUPPORTED_VENDOR` | `test_config_api__unsupported_vendor__returns_unsupported_vendor_error` |
| Configuration parse failure | adapter | 400 | `CONFIG_PARSE_ERROR` | Unit: Section 10's parser-contract table. Contract: `test_config_api__malformed_config_text__returns_config_parse_error` |
| Resource not found | `application` | 404 | `NOT_FOUND` | **Not in Slice 1** — Slice 1 has no single-resource `GET` endpoint (architecture.md Section 10); this test is added once `GET /devices/{id}` or `GET /incidents/{id}` ships (later slice). Do not claim this test belongs to Slice 1. |
| Persistence failure | `persistence` (`FailingRepository`, Section 9) | 500 | `PERSISTENCE_ERROR` | `test_config_api__persistence_failure__returns_controlled_500` |
| Unexpected/unmapped exception | anywhere | 500 | `INTERNAL_ERROR` | `test_config_api__unexpected_exception__returns_internal_error` |

- Every failure-path contract test asserts the **full envelope**
  (`{"data": null, "error": {"code", "message"}}`), not just the status
  code — this is Section 19's "structured invalid-input response" test
  and AC-12 generally.
- Failure paths get the same test rigor as success paths (Testing Goal 5).
- `test_config_api__unexpected_exception__returns_internal_error` uses a
  test double (e.g., an `application` service replaced with one that
  raises a plain, unmapped `Exception`) to prove the `api` layer's
  catch-all still returns the standard envelope and 500 — never an
  unhandled framework error page or a leaked stack trace — rather than
  assuming FastAPI's default exception handling is already correct.

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
| 4 | Unsupported vendor rejection — `POST` with `vendor: "juniper-junos"` returns 400 / `UNSUPPORTED_VENDOR` | Contract | none | `tests/contract/api/test_devices_api.py` |
| 5 | Normalized interface and ACL assignment — the **`201` response's `data.normalized_config`** (architecture.md Section 10.1), not a follow-up `GET /devices/{id}` | Contract | none | `tests/contract/api/test_devices_api.py` |
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
| 16 | Controlled persistence failure — `POST` with an injected `FailingRepository` returns 500 / `PERSISTENCE_ERROR`, no stack trace leaked | Contract | `FailingRepository` (fails via `upsert_open_incident`) | `tests/contract/api/test_devices_api.py` |
| 17 | Structured invalid-input response — missing `vendor` or empty body returns 422 / `SCHEMA_VALIDATION_ERROR` | Contract | none | `tests/contract/api/test_devices_api.py` |
| 18 | Unexpected exception returns 500 / `INTERNAL_ERROR`, never a raw stack trace | Contract | exception-raising test double | `tests/contract/api/test_devices_api.py` |
| 19 | `test_compute_fingerprint__delimiter_and_unicode_values__remain_unambiguous` (AC-11) | Unit | none | `tests/unit/detection/test_incident_factory.py` |
| 20 | `test_policy_evaluator__fixed_observed_at__populates_violation_detected_at` | Unit | none | `tests/unit/detection/test_policy_evaluator.py` |

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

# Architecture — Meta RNE Platform

**Status:** Draft — Day 1 consistency correction
**Date:** 2026-07-18
**Phase:** Planning / Architecture

Describes the system architecture for the MVP defined in
[product-spec.md](./product-spec.md), using the technology decisions in
[ADR-0001](./adr/0001-modular-monolith.md) and
[ADR-0002](./adr/0002-technology-stack-and-persistence.md). Per
[CLAUDE.md](../CLAUDE.md), no application code exists yet.

---

## 1. System Context

```
              ┌───────────────────────┐        ┌───────────────────────┐
              │   Operator / Caller     │        │  React Dashboard (SPA)  │
              │ (script, curl, CI, or   │        │  read-only, later slice │
              │  the dashboard below)   │        │  (FR-10)                 │
              └───────────┬─────────────┘        └────────────┬────────────┘
                          │ HTTP/JSON                          │ HTTP/JSON
                          ▼                                    ▼
                    ┌─────────────────────────────────────────────┐
                    │           Meta RNE Platform (backend)           │
                    │       modular monolith, single process          │
                    └───────────────┬───────────────┬────────────────┘
                                    │ SQL              │ stdout
                                    ▼                   ▼
                    ┌─────────────────────┐   ┌──────────────────────────┐
                    │      PostgreSQL        │   │  Structured JSON log      │
                    │  (device/config/        │   │  stream (incidents)        │
                    │   policy/incident/      │   └──────────────────────────┘
                    │   telemetry state)      │
                    └─────────────────────┘
```

**External actors:**

- **Operator/caller** — submits configuration and telemetry, and queries
  devices/incidents/drift/telemetry via REST.
- **React Dashboard** — a distinct, read-only consumer of the REST Query
  API (FR-08/FR-10). It is **not** the REST API itself and is **not**
  built in the first vertical slice; it is shown here as a boundary
  because it is part of the final MVP. See Section 15 for its deployment
  shape once built.
- **Structured log stream** — stdout is the only alerting channel (FR-09).
  No external alert integration exists.

There is no live device in the loop (A-03): configuration and telemetry
are supplied by callers or a simulator, never polled from real hardware.

---

## 2. Major Components and Responsibilities

Modular monolith (ADR-0001): one deployable backend process, partitioned
into modules with dependencies enforced by import direction only.

```
┌──────────────────────────────────────────────────────────────────┐
│                    api  (FastAPI, Pydantic schemas)                   │
│  Route definitions, request/response (de)serialization, envelopes,    │
│  status-code mapping. Depends on: application.                       │
└──────────────────────────────┬─────────────────────────────────────┘
                                │
┌──────────────────────────────▼─────────────────────────────────────┐
│                        application  (use cases)                       │
│  ConfigIngestionService (Section 4 — owns policy evaluation and       │
│  incident upsert directly, via one UnitOfWork), DriftCheckService,    │
│  TelemetryIngestionService, IncidentQueryService, Clock, UnitOfWork   │
│  Orchestrates domain + adapters + persistence. No FastAPI/SQLAlchemy  │
│  types. Depends on: domain, vendor adapters, persistence interfaces.  │
└──────┬────────────┬────────────┬────────────┬────────────┬─────────┘
      │            │            │            │            │
┌─────▼──────┐┌────▼───────┐┌───▼────────┐┌───▼────────┐┌──▼──────────┐
│ vendor       ││ domain       ││ detection    ││ persistence  ││ observability │
│ adapters     ││ (Normalized- ││ (Policy-     ││ (SQLAlchemy  ││ (structured   │
│ - Cisco      ││  Configura-  ││  Evaluator,  ││  repos on    ││  JSON logging │
│ - Arista     ││  tion,       ││  DriftDetec- ││  Postgres;   ││  on incident  │
│              ││  Incident,   ││  tor, Rule-  ││  in-memory   ││  create/      │
│              ││  contracts)  ││  Engine)     ││  test doubles)││  update)      │
└──────────────┘└──────────────┘└──────────────┘└──────────────┘└───────────────┘
```

| Component | Responsibility | Must NOT depend on |
|---|---|---|
| `api` | HTTP routing, request/response mapping, status-code mapping | domain internals directly |
| `application` | Use-case orchestration | FastAPI, SQLAlchemy, or any specific driver type |
| `domain` | `NormalizedConfiguration`, `Incident`, `ConfigurationPolicy`, `TelemetrySample` models; adapter/repository interfaces (ports) | web framework, database — pure data + logic (NFR-02) |
| vendor adapters | Parse vendor CLI text → `NormalizedConfiguration` | each other |
| `detection` | `PolicyEvaluator`, `DriftDetector`, `RuleEngine` | `api`, persistence implementation details |
| `persistence` | SQLAlchemy repositories on PostgreSQL (production); in-memory repositories implementing the same interfaces (tests only) | `domain` must not import this |
| `observability` | Structured JSON log emission (FR-09) | — |
| `frontend` (later slice) | React/TypeScript SPA, consumes `api` over HTTP only | backend internals — it is a separate deployable |

**Dependency rule:** arrows point inward only. `api → application →
domain` + adapter/persistence *interfaces*; concrete adapters and
repositories are injected. Module boundaries are enforced by import
direction, not by process boundary — this is what keeps the monolith
"modular."

---

## 3. Request and Data Flow

```
HTTP request → api (parse, map to DTO)
             → application (orchestrate: adapter/policy/drift → persistence)
             → domain + detection (normalize / evaluate / diff — pure functions)
             → persistence (PostgreSQL: snapshot, violations→incidents)
             → observability (structured log line if an incident was created/updated)
             → HTTP response (envelope)
```

Read requests (`GET /devices`, `GET /incidents`, etc.) skip domain/detection:
`api → application → persistence → api`.

---

## 4. Configuration Ingestion Flow

Implements FR-01, FR-02, FR-03. Request body and service signature
(binding):

```
POST /devices/{id}/config
{
  "vendor": "cisco-ios-xe",
  "config_text": "..."
}
```

```
ConfigIngestionService.ingest(
    device_id: str,
    vendor: str,
    config_text: str
) -> ConfigIngestionResult

ConfigIngestionResult
  device_id
  snapshot_id
  normalized_config
  violations_detected
  incidents_created
  incidents_updated
```

`ConfigIngestionResult` is the `application`/`api`-layer response DTO
(Section 10.1) — it is not the same type as `IncidentUpsertResult`
(Section 11), which stays internal to the repository call.
`ConfigIngestionService` converts each `IncidentUpsertResult.outcome` it
receives from step 8 below into the `incidents_created`/
`incidents_updated` counts on the `ConfigIngestionResult` it returns; no
caller of `ingest()` ever sees an `IncidentUpsertResult` directly.

`ConfigIngestionService.ingest` performs exactly this sequence (binding):

1. Validate the vendor through `AdapterRegistry.resolve` — unknown vendor
   → `UnsupportedVendorError` (400), no further processing, no `UnitOfWork`
   opened yet.
2. Parse and normalize (`adapter.parse(raw_text)`) — **before** any
   persistent change. Parse failure → `ParseError` (400), nothing written.
3. Obtain `observed_at = clock.now_utc()` (Section 4.1) and open one
   `UnitOfWork` (Section 11).
4. Create or update `Device` state (`uow.devices`).
5. Save the immutable `ConfigurationSnapshot` (`uow.configuration_snapshots`)
   — `normalized_config` embedded inline, `submitted_at = observed_at`
   (Section 6: normalization itself carries no timing field).
6. Read applicable policies (`uow.configuration_policies.get_for_device`).
7. Evaluate: `PolicyEvaluator.evaluate(device_id, snapshot_id, observed_at,
   config, policies) -> list[ConfigurationViolation]` (Section 7) — every
   violation carries `source_snapshot_id = snapshot_id` and
   `detected_at = observed_at`, both passed in, never read from a clock or
   repository inside the evaluator.
8. For each violation: `IncidentFactory.build_candidate` → `uow.incidents.
   upsert_open_incident(candidate, fingerprint, observed_at) ->
   IncidentUpsertResult` (Section 9) — atomic, one call per finding.
9. `uow.commit()` — once, for every write above.
10. **Only if commit succeeds**, emit one structured log per
    `IncidentUpsertResult` (Section 13). A commit failure calls
    `uow.rollback()` instead and emits no incident log (Section 12).
11. Return the response DTO (Section 10.1), tallying
    `incidents_created`/`incidents_updated` directly from each result's
    `.outcome` — never inferred from `occurrence_count`, never a second
    lookup.

Drift detection (Section 8) is **not** one of the 11 steps above — for a
device with a prior submission, it is wired into this same sequence
(after step 7, before step 9) in the *next* slice (product-spec
Section 11), reusing the same `uow` and the same post-commit logging rule
already established here.

**A failure while writing the observability log after a successful
commit does not roll back durable data or turn the response into a
`PersistenceError`.** The database commit is authoritative; logging is a
best-effort side channel once that commit has already succeeded.

### 4.1 Clock (explicit time)

`domain`/`detection` never read the system clock — `application` reads it
once per operation and passes the value down, so both stay deterministic
and testable without real time:

```
Clock
  now_utc() -> timestamp
```

`SystemClock` (production, wraps the OS UTC clock) and `FixedClock`
(tests, returns a fixed value) are its only two implementations, injected
into `application` services the same way repositories are. Tests never
`sleep` to exercise time-dependent behavior — they advance a `FixedClock`.

---

## 5. Vendor Adapter Boundary

Satisfies NFR-01. Shared port defined in `domain`:

```
interface VendorConfigAdapter:
    vendor_id: string                 # "cisco-ios-xe" | "arista-eos"
    parse(raw_text: string) -> NormalizedConfiguration | ParseError
```

- Each adapter is a pure function; no I/O, no cross-adapter references.
- Adapters register in an `AdapterRegistry` keyed by `vendor_id`. Adding a
  vendor means one new adapter + one registry entry — `application` and
  `domain` do not change.
- Unrecognized lines are ignored, not rejected, so realistic configs with
  unsupported features still ingest (A-07's representative-subset scope).
- `resolve(vendor_id)` for a string that names no registered adapter
  raises `UnsupportedVendorError` (400), not `ParseError` — resolving the
  vendor and parsing its content are two separate failure modes (see the
  vendor validation boundary note in product-spec.md FR-01/NFR-05).
- First vertical slice: Cisco only. Arista is a later slice.

### 5.1 Cisco IOS-XE Parser Contract (representative, binding)

`CiscoAdapter.parse(raw_text)` returns `ParseError` (`CONFIG_PARSE_ERROR`,
400) for each of these — this is the full contract; Slice 1 may implement
only the starred (`*`) subset, with the rest completed before FR-02 is
considered done:

| # | Failure | Slice 1 minimum? |
|---|---|---|
| 1 | Empty or whitespace-only input | `*` |
| 2 | Missing `hostname` declaration | `*` |
| 3 | Malformed `hostname` declaration (e.g., `hostname` with no value) | |
| 4 | Malformed `interface` declaration (e.g., no interface name after the keyword) | |
| 5a | Invalid interface IP address (unparsable octets) | `*` |
| 5b | Invalid interface subnet mask (unparsable or non-contiguous) | `*` |
| 6 | Invalid `ip access-group` direction (neither `in` nor `out`) | |
| 7 | An `ip access-group` assignment referencing an ACL name never declared with `ip access-list` | |
| 8a | BGP `neighbor` line with an invalid IPv4 neighbor address | |
| 8b | BGP `neighbor` line with a non-integer or non-positive remote AS | |

**Not** the primary example: an unterminated `interface` block is one
possible instance of failure #4, not the representative case for the
contract as a whole — every category above is a first-class failure mode
in its own right, with its own named test (test-strategy.md Section 10).

Any other unrecognized-but-well-formed command (a line that doesn't match
a known directive but doesn't violate the structure above either) is
**ignored**, not rejected.

---

## 6. Normalized Configuration Representation

Canonical model, defined once in `domain`, produced by exactly one adapter
from exactly one `ConfigurationSnapshot`'s raw text, and **persisted
inline on that snapshot** (Section 4, Section 11) — never a separate
store, never re-derived on a normal read.

```
NormalizedConfiguration
├── hostname: string
├── interfaces: [Interface]
│     ├── name: string
│     ├── ip_address: string | null
│     ├── mtu: int | null
│     ├── admin_state: "up" | "down"
│     ├── acl_in: string | null        # name reference into acls[]
│     └── acl_out: string | null       # name reference into acls[]
├── routing
│     ├── static_routes: [{ prefix, next_hop }]
│     └── bgp_neighbors: [{ neighbor_ip, remote_as }]
└── acls: [{ name, entries: [{ sequence, action, protocol, source, destination }] }]
```

**Deterministic and content-only — no timing field.** `NormalizedConfiguration`
represents configuration *content* alone. It carries no `normalized_at` or
any other ingestion-time metadata: `parse(raw_text)` is a pure function of
its input, so two equivalent inputs — parsed a second apart or a year
apart — produce structurally identical output (domain-model.md
invariant 10). Ingestion time lives in exactly one place:
`ConfigurationSnapshot.submitted_at` (Section 4). This is also what makes
Section 8's drift comparison metadata-free by construction: there is no
timestamp field on `NormalizedConfiguration` for `DriftDetector` to
accidentally compare.

Vendor-agnostic: no Cisco- or Arista-specific field. Vendor attributes
that don't map cleanly are dropped at the adapter boundary. Diffing (drift
detection) and policy evaluation are structural, field-by-field, over
content only.

---

## 7. Configuration Policy Evaluation Flow

Implements FR-03. This is the **primary detection mechanism for the first
vertical slice** — unlike drift (Section 8), it needs no history, so it
fires on a device's very first submission.

```
ConfigurationPolicyRepository.get_for_device(device_id)
    -> list[ConfigurationPolicy]   (matches this device_id, or a "*" wildcard)
   │
   ▼
PolicyEvaluator.evaluate(
    device_id,
    source_snapshot_id,
    observed_at,
    config: NormalizedConfiguration,
    policies: list[ConfigurationPolicy]
) -> list[ConfigurationViolation]
```

`device_id`, `source_snapshot_id`, and `observed_at` (Section 4.1) are
passed in as plain arguments by the caller (`ConfigIngestionService`,
Section 4), which already has all three — `PolicyEvaluator` never looks
any of them up, and never reads a clock itself, and no `Clock`/
`FixedClock` port is needed inside `detection` at all — `observed_at` is
already a plain value by the time it arrives. This is what keeps it
framework-independent and deterministic (NFR-02/NFR-03): all context is
explicit input. Every `ConfigurationViolation` produced carries
`source_snapshot_id` and `detected_at = observed_at` (never a value the
evaluator generated itself), and no violation carries a generated ID
(domain-model.md Section 7).

A `RequiredAclRule` (`acl_name`, `interface_name`, `direction`, `severity`,
`recommendation` — domain-model.md Section 6) is satisfied only if that
exact ACL name is present in `config.acls` **and** assigned to that
interface's `acl_in`/`acl_out` in that direction. Two distinct outcomes,
both still required-ACL findings, are kept observable rather than
collapsed into one silent case (domain-model.md Section 7):

- the target interface itself does not exist →
  `violation_type = "TARGET_INTERFACE_MISSING"` (never treated as
  satisfying the rule);
- the interface exists but the assignment is wrong — ACL entirely absent,
  present but unassigned, or a *different* ACL assigned in that direction
  → `violation_type = "MISSING_REQUIRED_ACL"`, with
  `evidence.actual_acl_name` set to that other ACL's name, or `null` if
  nothing is assigned.

`rule_ref`, `severity`, `evidence` (`AclAssignmentEvidence`), and
`recommendation` are copied directly from the matched `RequiredAclRule`
and its owning policy — `PolicyEvaluator` computes none of these values
itself beyond selecting which rule matched. `affected_resource` is
computed, not copied: `"interface:{interface_name}:acl_in"` or
`"interface:{interface_name}:acl_out"` depending on `rule.direction`
(domain-model.md Section 7) — a distinct, evaluator-level format from
`Incident.affected_resource`'s `"acl:{name}:{interface}:{direction}"`
(Section 11), since the violation identifies *which assignment slot* is
wrong while the eventual incident additionally names which ACL was
expected.

No matching policy → zero violations. **Day 3B matches `applies_to`
against `device_id` by exact string equality only** — `"*"` wildcard
resolution (this section's opening diagram) is not implemented this
phase; the Slice 1 policy applies only to `spine-01`. Violations are
returned in a deterministic order: `policies` tuple order, then each
policy's `required_acls` tuple order — never re-sorted or
set-deduplicated. `PolicyEvaluator` is pure domain/detection logic: plain
inputs in, a tuple out, no I/O.

---

## 8. Configuration Drift Detection Flow

Implements FR-04, AC-05/AC-06. **Not part of the first vertical slice —
the next one.**

```
DriftDetector.compare(baseline: NormalizedConfiguration, current: NormalizedConfiguration)
    -> DriftReport { added: [...], removed: [...], changed: [...] }
```

- `baseline` is always `Device.baseline_snapshot_id`'s normalized config
  (Section 4) — fixed at the device's first successful submission, never
  the "previous" submission. On a device's first submission, current ==
  baseline, so the diff is empty by construction (AC-06) — there is no
  null/no-baseline case to handle once a `Device` exists.
- **Content-only comparison, by construction.** `compare` receives two
  `NormalizedConfiguration` values, which (Section 6) carry no ingestion
  timestamp or snapshot reference of their own — there is nothing on
  either input for `DriftDetector` to compare *except* configuration
  content. Two snapshots submitted seconds apart or months apart, with
  identical content, always diff to empty. `ConfigurationSnapshot`
  metadata (`snapshot_id`, `submitted_at`, `raw_text_hash`) is never
  passed into `compare` at all.
- Comparison walks each top-level collection (`interfaces`,
  `static_routes`, `bgp_neighbors`, `acls`) keyed by natural identity
  (name / neighbor IP / prefix), diffing scalar fields within matches.
- `application` decides which diff entries are incident-worthy. For the
  slice this unblocks, a removed ACL is always incident-worthy
  (`severity = Medium`, matching the policy path's severity for the same
  underlying condition). A general drift-severity table beyond "removed
  ACL" is deferred (Section 17).

---

## 9. Incident Creation and Deduplication Flow

Implements FR-07, FR-09, AC-10/AC-11.

```
finding: ConfigurationViolation | DriftFieldDiff | Anomaly
   │       (already carries source_snapshot_id and detected_at = observed_at)
   ▼
IncidentFactory.build_candidate(finding) -> IncidentCandidate:
    { device_id, source, rule_ref, affected_resource, severity, evidence,
      recommendation, observed_at }
    # evidence.source_snapshot_id copied straight from finding.source_snapshot_id
    # observed_at copied straight from finding.detected_at — the factory
    # never reads a clock or accepts a separate timestamp argument (Day 4A)

    rule_ref:            POLICY_VIOLATION → copied from ConfigurationViolation.rule_ref
                          ANOMALY         → rule_id (e.g. "RULE-CPU-HIGH")
                          DRIFT           → field path (e.g. "acls.removed")
    affected_resource:   POLICY_VIOLATION → copied verbatim from
                                             ConfigurationViolation.affected_resource,
                                             e.g. "interface:{interface_name}:acl_in" —
                                             NOT a separate "acl:{name}:{interface}:
                                             {direction}" format; there is only one
                                             affected_resource convention for policy
                                             violations (corrects an earlier draft)
                          ANOMALY (CPU)    → "device"
                          ANOMALY (FLAP)   → "interface:{interface_name}"
                          ANOMALY (BGP)    → "bgp-neighbor:{neighbor_ip}"
                          DRIFT            → "{field_path}:{entity_name}"
    recommendation:      POLICY_VIOLATION → copied verbatim from
                                             ConfigurationViolation.recommendation
                                             (plain string; no template/rewrite, Day 4A)
   │
   ▼
fingerprint = compute_fingerprint(device_id, source, rule_ref, affected_resource)
    # SHA-256 hex digest of a canonical JSON array — see domain-model.md
    # Section 11. Never a delimiter-joined string: values may themselves
    # contain "|", ":", quotes, or Unicode.
   │
   ▼
uow.incidents.upsert_open_incident(candidate, fingerprint, observed_at)
    -> IncidentUpsertResult { incident: Incident, outcome: "CREATED" | "UPDATED" }
   │  ONE atomic statement (Section 11) — never a separate find-then-save
   │  round trip. observed_at supplies created_at (on insert) and
   │  last_seen_at (both branches); the caller never computes these itself.
   │
   ├─ no existing OPEN row for this fingerprint → INSERT, outcome = CREATED
   │
   └─ existing OPEN row for this fingerprint    → UPDATE in place:
        last_seen_at = observed_at, occurrence_count += 1,
        evidence = candidate.evidence, outcome = UPDATED — this is also
        the outcome for two concurrent upsert_open_incident calls racing
        on the same fingerprint (Section 11's guarantee is scoped to this
        repository call, not to two full concurrent HTTP requests unless
        a test exercises that specifically — test-strategy.md Section 9)
   │
   ▼  ONLY once the enclosing transaction COMMITS successfully:
observability.emit_json_log(result)   — stdout, FR-09 / AC-10
   { "incident_id", "device_id", "rule_ref", "severity", "status", "outcome", "timestamp" }
   # a rolled-back transaction (uow.rollback(), e.g. commit fails →
   # PersistenceError) emits NO log line — see Section 13
```

**Vertical-slice values** (missing required ACL on `spine-01`):

| Field | Value |
|---|---|
| `source` | `POLICY_VIOLATION` |
| `rule_ref` | the seeded policy's `policy_id` (a stable string such as `"policy-acl-external-in"`, not a UUID — domain-model.md Section 16) |
| `affected_resource` | `"interface:GigabitEthernet0/1:acl_in"` — copied verbatim from `ConfigurationViolation.affected_resource` |
| `severity` | `Medium` |
| `evidence` | `{ source_snapshot_id, violation_type, expected_acl_name, actual_acl_name, interface_name, direction }` (`PolicyViolationIncidentEvidence`, domain-model.md Section 7) — **not** `device_id`/`policy_id` again, since those are already top-level `Incident` fields |
| `recommendation` | `"Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1"` — copied verbatim from `ConfigurationViolation.recommendation`, plain string (Day 4A; no `Recommendation` value object yet, domain-model.md Section 13) |
| `observed_at` | copied verbatim from `ConfigurationViolation.detected_at` |

`IncidentFactory` + `upsert_open_incident` together form the *only* path
to incident creation — no manual-creation endpoint, and no code path may
call a plain `save`/find-then-write sequence for an `Incident` (Section 11
explains why that would be racy). Dedup scope is `status = "OPEN"` only
(A-09): a resolved incident that recurs starts a new one, since there is
no reopen workflow in the MVP.

---

## 10. REST API Boundary

Satisfies FR-08 and NFR-05. Envelope:

```json
{"data": <resource or array>, "error": null}
{"data": null, "error": {"code": "string", "message": "string"}}
```

**Endpoints, first vertical slice + full MVP:**

| Method | Path | Purpose | FR | Slice |
|---|---|---|---|---|
| `POST` | `/devices/{id}/config` | Ingest a vendor config | FR-01–03 | **1** |
| `GET` | `/incidents` | List incidents, filter by `device_id`/`severity` | FR-07, FR-08 | **1** |
| `GET` | `/devices` | List devices + current normalized config | FR-08 | Later |
| `GET` | `/devices/{id}` | One device's current normalized config | FR-08 | Later |
| `GET` | `/incidents/{id}` | One incident | FR-08 | Later |
| `GET` | `/devices/{id}/drift` | Drift report vs. baseline | FR-04, FR-08 | 2 |
| `POST` | `/devices/{id}/telemetry` | Ingest a telemetry sample | FR-05 | 2 |
| `GET` | `/devices/{id}/telemetry/recent` | Recent telemetry window | FR-05, FR-08 | 2 |

**Only the two rows marked Slice 1 are required to demonstrate the first
vertical slice** (product-spec Section 11). The three "Later" rows are not
needed yet: `POST /devices/{id}/config`'s response body (Section 10.1)
already returns the full normalized configuration, so no follow-up
`GET /devices/{id}` is needed to test normalization, and `GET /incidents`
alone is sufficient without a single-incident lookup. They are listed here
because they are part of the final MVP's query surface (FR-08), not
because Slice 1 exercises them — do not write a Slice-1 test against a
`GET` endpoint that isn't in the Slice-1 row.

`api` is a thin adapter over `application` — no business logic, only
request parsing, DTO mapping, and status mapping (Section 12). No
authentication in the MVP (Section 14). The React dashboard (FR-10) is a
separate deployable that calls these same endpoints — it does not get its
own API surface.

### 10.1 `POST /devices/{id}/config` — Success Response (binding)

```json
// 201 Created
{
  "data": {
    "device_id": "spine-01",
    "snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "normalized_config": {
      "hostname": "spine-01",
      "interfaces": [ { "name": "GigabitEthernet0/1", "ip_address": "10.0.0.1/30", "mtu": null, "admin_state": "up", "acl_in": null, "acl_out": null } ],
      "routing": { "static_routes": [], "bgp_neighbors": [] },
      "acls": []
    },
    "violations_detected": 1,
    "incidents_created": 1,
    "incidents_updated": 0
  },
  "error": null
}
```

This is the JSON body of `ConfigIngestionResult` (Section 4) — an
`application`/`api`-layer DTO, not a domain entity.
`violations_detected`/`incidents_created`/`incidents_updated` are counts
computed by `ConfigIngestionService` for this one request, by tallying
each `upsert_open_incident` call's `IncidentUpsertResult.outcome`
(Section 9/11) — never inferred from `occurrence_count`, never a second
lookup. `IncidentUpsertResult` itself never leaves `ConfigIngestionService`;
callers only ever see the aggregated counts.
`incidents_created + incidents_updated == violations_detected` always
holds for the policy path. This response shape is also how Slice 1 tests
verify normalization (test-strategy.md Section 19, test 5) — never via
`GET /devices/{id}`, which is deferred.

---

## 11. Persistence Responsibilities

Satisfies NFR-06 (ADR-0002): **PostgreSQL via SQLAlchemy is the
production persistence layer**, schema-managed by **Alembic migrations**,
never `Base.metadata.create_all()` (Section 11.2). In-memory
implementations of the same repository interfaces exist only as fast test
doubles (test-strategy.md Section 9); they are never used outside tests.

### 11.1 Unit of Work

The four repositories that participate in one ingestion (Section 4) are
reached through a single `UnitOfWork`, not constructed independently:

```
UnitOfWork
  devices: DeviceRepository
  configuration_snapshots: ConfigurationSnapshotRepository
  configuration_policies: ConfigurationPolicyRepository
  incidents: IncidentRepository
  commit() -> None
  rollback() -> None
```

The **SQLAlchemy `UnitOfWork`** owns one DB session; every repository
inside it shares that session, so all writes in one `ingest()` call
(Section 4) land in one transaction. The **in-memory `UnitOfWork`**
(tests) provides the same observable `commit`/`rollback` contract —
`rollback()` discards everything written since the `UnitOfWork` was
opened, so integration tests can assert "nothing persisted" after a
simulated failure without a real database.

`TelemetryRepository` (FR-05, later slice) is not part of this grouping —
telemetry ingestion is its own operation with its own transaction once
built.

```
domain defines (interfaces/ports), Python-style snake_case throughout:
  DeviceRepository
    get_by_id(device_id); save(device)
        # save is upsert-by-device_id; every rejected lifecycle transition
        # raises DeviceConflictError and leaves the stored Device
        # unchanged (Day 4B2) — no list()
  ConfigurationSnapshotRepository        # append-only
    get_by_id(snapshot_id); add(snapshot)  # includes normalized_config inline
        # duplicate snapshot_id -> SnapshotAlreadyExistsError; unknown
        # device_id -> ReferencedDeviceNotFoundError (Day 4B2) — no
        # get_current_for_device/get_baseline_for_device on this port
  ConfigurationPolicyRepository          # read-mostly, seeded
    get_applicable_to_device(device_id)  # exact match only, no "*" wildcard
    seed_if_missing(policies)            # one call is all-or-nothing;
        # semantic equivalence (applies_to + required_acls, not
        # created_at) is a no-op, differing content raises
        # PolicySeedConflictError (Day 4B2) — no list()
  IncidentRepository
    get_by_id(incident_id); list(filter)
    # Day 4B1 binding decision: no find_open_by_fingerprint on this port —
    # dropped from the public surface; the atomic upsert below is the
    # deduplication mechanism.
    upsert_open_incident(candidate, fingerprint, observed_at) -> IncidentUpsertResult
        # THE write path — atomic create-or-update. No plain save(): every
        # write goes through this one operation, so nothing can bypass the
        # dedup guarantee via a find-then-save race.
  TelemetryRepository
    save(device_id, sample)
    get_latest(device_id)
    get_recent(device_id, since: timestamp)   # bounded recent history, FR-05

IncidentUpsertResult { incident: Incident, outcome: "CREATED" | "UPDATED" }
```

`upsert_open_incident` is atomic at two levels: the fingerprint is
computed deterministically before the call (Section 9), and PostgreSQL
enforces the invariant at the row level via a partial unique index (below)
even if two transactions attempt it at the same instant — see
domain-model.md Section 11 for the full argument. One statement decides
and reports both the write *and* which branch fired:

```sql
CREATE UNIQUE INDEX ux_incidents_open_fingerprint
  ON incidents (fingerprint)
  WHERE status = 'OPEN';
```

```sql
INSERT INTO incidents (incident_id, fingerprint, device_id, source, rule_ref,
    affected_resource, severity, status, evidence, recommendation,
    created_at, last_seen_at, occurrence_count)
VALUES (:incident_id, :fingerprint, ..., 'OPEN', ..., :observed_at, :observed_at, 1)
ON CONFLICT (fingerprint) WHERE status = 'OPEN' DO UPDATE SET
    last_seen_at = :observed_at,
    occurrence_count = incidents.occurrence_count + 1,
    evidence = EXCLUDED.evidence
RETURNING *, (xmax = 0) AS was_inserted;
```

`xmax = 0` is PostgreSQL's standard tell for "this row was freshly
inserted, not touched by the `ON CONFLICT` branch" — the SQLAlchemy
repository maps that boolean straight to `outcome` (`CREATED`/`UPDATED`)
in the same round trip. `ConfigIngestionService` never infers the outcome
from `occurrence_count` and never issues a second lookup (Section 4, step
11). The in-memory test double reproduces the same `IncidentUpsertResult`
contract, including under concurrent calls (guarded by one critical
section), verified by the conformance suite (test-strategy.md Section 9)
against both implementations.

### 11.2 Schema Migrations and Policy Seeding

- **Alembic** owns all PostgreSQL schema changes (ADR-0002). The first
  migration creates the Slice 1 tables and the partial unique index above.
  Migrations are an **explicit deployment step** (`alembic upgrade head`,
  run by the deploy tooling / CI / container entrypoint) that completes
  **before** the FastAPI process starts accepting requests — never a
  `@app.on_event("startup")` hook inside FastAPI, and never
  `Base.metadata.create_all()`. The schema's source of truth is the
  migration history, run to completion first, not something the running
  API process manages as a side effect of booting.
- **Policy seeding is idempotent and runs only after migrations succeed.**
  Once (and only once) the migration step above has completed, a separate
  seeding step calls `ConfigurationPolicyRepository.seed_if_missing`,
  which upserts each fixture policy by its stable `policy_id`
  (`INSERT ... ON CONFLICT (policy_id) DO NOTHING`) — restarting the app,
  or starting a fresh E2E run (Section 15.1), never produces duplicate
  policy rows. Unlike migrations, seeding is idempotent and app-level, so
  it may run as a FastAPI startup hook — but deployment ordering
  (migrations, then seeding, then accept traffic) is what guarantees it
  never runs against an un-migrated schema, not a check the app performs
  on itself.

- Repositories are the only components with mutable state; `domain` and
  `detection` are stateless.
- `ConfigurationSnapshotRepository` is append-only: a snapshot's
  `raw_config_text` and `normalized_config` never change after creation.
- Test isolation: unit/integration tests use a fresh in-memory `UnitOfWork`
  per test; contract tests use an in-process app with in-memory or a
  per-test Postgres transaction rollback; E2E tests use a real, disposable
  Postgres container (Section 15.1, test-strategy.md Section 9).

---

## 12. Error Handling Strategy

Layered, matching Section 2's dependency structure:

- **Adapter layer** — parse failure returns a structured `ParseError`,
  never a thrown exception.
- **Domain/detection layer** — pure functions return a valid result or an
  empty result (e.g., zero violations); no throwing for expected
  conditions.
- **Application layer** — translates domain outcomes into a fixed set of
  categories: `SchemaValidationError`, `UnsupportedVendorError`,
  `ParseError`, `NotFoundError`, `PersistenceError`. No HTTP knowledge.
- **API layer** — maps categories to status + `error.code`
  (product-spec.md NFR-05):

| Category | Status | `error.code` |
|---|---|---|
| `SchemaValidationError` (Pydantic model validation) | 422 | `SCHEMA_VALIDATION_ERROR` |
| `UnsupportedVendorError` | 400 | `UNSUPPORTED_VENDOR` |
| `ParseError` | 400 | `CONFIG_PARSE_ERROR` |
| `NotFoundError` | 404 | `NOT_FOUND` |
| `PersistenceError` | 500 | `PERSISTENCE_ERROR` |
| anything unmapped | 500 | `INTERNAL_ERROR` |

- **No silent failures.** Every error reaching the API boundary appears in
  `error`. If `uow.commit()` fails after a violation was found, the whole
  ingestion request fails with `PERSISTENCE_ERROR` and `uow.rollback()`
  runs — a config is never "saved but its incident silently lost."
- Config ingestion + violation detection + incident upsert happen inside
  one `UnitOfWork` transaction (Section 11), so a mid-flow failure rolls
  back cleanly rather than leaving partial state.
- **A rolled-back transaction emits no structured log.** Section 13's
  `emit_json_log` call happens strictly after `uow.commit()` succeeds
  (Section 4 step 10, Section 9). If `PersistenceError` aborts the
  transaction after a violation was already detected, no incident log line
  is written for it — the log is a statement about durable state, never
  about an in-memory intent that didn't survive. Conversely, a failure
  *while writing that log*, after commit already succeeded, does not roll
  back the (already-durable) database result or turn the HTTP response
  into a `PersistenceError` — the response reflects what the database
  actually holds.

---

## 13. Observability Strategy

- **Structured incident log** (FR-09, AC-10) — one JSON line per
  `IncidentUpsertResult`: `incident_id`, `device_id`, `rule_ref`,
  `severity`, `status`, `outcome` (`"CREATED"` or `"UPDATED"`),
  `timestamp`. The `outcome` field is what makes the two cases
  distinguishable in the log stream, rather than requiring a reader to
  infer creation-vs-update from surrounding context. Not batched or
  delayed, but strictly **post-commit** (Section 9/12) — a failed or
  rolled-back transaction never emits a log line. Verifying this is an
  **integration-level** concern (test-strategy.md Section 13): it requires
  inspecting what was actually logged for a given request, which a plain
  HTTP round-trip (as in the E2E test, Section 7 of test-strategy.md)
  cannot do without separately inspecting the container's stdout — the
  E2E test proves the HTTP/DB path works, not the log line's presence.
- **Structured request log** — `api` logs each request (method, path,
  status, duration) as JSON, for local debugging.
- **No metrics backend, tracing, or dashboards** in the backend. The
  operator-facing visualization is the React dashboard (FR-10), a
  separate deployable consuming the REST Query API (Section 10) — it is
  not part of the backend's observability surface.
- stdout is captured by whatever runs the process (terminal, `docker
  compose logs`). No log shipping/rotation.

---

## 14. Security Assumptions

No authentication/authorization in the MVP (product-spec Section 7):

- All endpoints are unauthenticated, plain HTTP, for a local/demo Docker
  Compose environment only — not a shared or internet-reachable one.
- Any caller that reaches the service can ingest/read for any device ID;
  no tenant or ownership concept.
- Input validation (Section 12) prevents malformed input from crashing
  the process; it is not a security boundary.
- **Deferred, not designed here:** if deployed outside a local demo,
  authentication, TLS termination, and rate limiting are required first
  (Section 17).

---

## 15. Deployment Architecture (Docker Compose)

```yaml
# docker-compose.yml (illustrative — build details are an implementation choice)
services:
  db:
    image: postgres:16
    environment:
      - POSTGRES_DB=meta_rne
      - POSTGRES_USER=meta_rne
      - POSTGRES_PASSWORD=meta_rne
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports: ["5432:5432"]

  api:
    build: .
    depends_on: [db]
    environment:
      - DATABASE_URL=postgresql://meta_rne:meta_rne@db:5432/meta_rne
      - LOG_LEVEL=info
    ports: ["8080:8080"]

  # frontend:                     # later vertical slice (FR-10)
  #   build: ./frontend
  #   depends_on: [api]
  #   ports: ["5173:5173"]

volumes:
  pgdata:
```

```
┌───────────────────────────────────────────────────────────┐
│                        docker-compose                          │
│  ┌─────────────┐   SQL   ┌─────────────┐   (later slice)      │
│  │  api          │◄───────►│  db           │   ┌─────────────┐  │
│  │  (backend,    │         │  (postgres)   │   │  frontend    │  │
│  │   1 process)  │         └─────────────┘   │  (react/vite) │  │
│  └─────────────┘                              └─────────────┘  │
└───────────────────────────────────────────────────────────┘
        host: localhost:8080 (api)   localhost:5173 (frontend, later)
```

- `pgdata` is a named volume: Postgres state **does** survive a container
  restart, unlike the earlier in-memory-only design. `docker compose down
  -v` discards it; `docker compose down` alone does not. This is the
  **local development / production shape** — a persistent volume is
  correct there.
- The `frontend` service is commented out until FR-10 is built (later
  slice) — the compose file's shape already reserves its place so adding
  it later is additive, not a restructuring.
- `docker compose up` (backend + db only, for the first slice) is
  sufficient to run the vertical slice end-to-end.

### 15.1 E2E Database Isolation (binding)

The E2E suite (test-strategy.md Section 7) must **not** reuse the local
development `pgdata` volume — a test run must never see rows left behind
by a previous run or a developer's local session. Local development and
production keep `pgdata` (Section 15) unchanged.

**Binding approach: a standalone `compose.e2e.yml`** (not an overlay) that
declares no persistent named PostgreSQL volume at all:

```yaml
# compose.e2e.yml (illustrative — a self-contained stack, not a docker-compose.yml overlay)
services:
  db:
    image: postgres:16
    environment: [POSTGRES_DB=meta_rne, POSTGRES_USER=meta_rne, POSTGRES_PASSWORD=meta_rne]
    # no `volumes:` key at all — container-local storage only, destroyed with the container
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U meta_rne"], interval: 2s, retries: 10 }
  api:
    build: .
    depends_on: { db: { condition: service_healthy } }
    environment: [DATABASE_URL=postgresql://meta_rne:meta_rne@db:5432/meta_rne]
    ports: ["8080:8080"]   # published so Playwright (host-run) can reach it at
                            # localhost:8080; running Playwright as a service
                            # inside this same network instead is the alternative
    healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8080/health"], interval: 2s, retries: 10 }
```

The suite waits on both healthchecks before sending its first request —
`api` reporting "up" is not the same as `api` having finished migrating
(Section 11.2) and being ready to accept traffic.

A standalone E2E file was chosen over an override (`volumes: !reset []`)
for clarity: the full E2E stack is readable in one file, with nothing
implied by what a base file's volume declaration was overridden away
from.

**E2E lifecycle (binding, every run):**

1. Start a fresh `api` + `db` stack from `compose.e2e.yml`; wait for both
   healthchecks above to pass.
2. Run Alembic migrations against it (Section 11.2).
3. Run idempotent policy seeding (`seed_if_missing`, Section 11.2).
4. Run the Playwright HTTP test suite (test-strategy.md Section 7) — each
   test asserts its `POST` response before making any follow-up `GET`.
5. Destroy the containers and all E2E volumes (`docker compose -f
   compose.e2e.yml down -v`), whether the suite passed or failed.

Steps 2–3 are why `seed_if_missing`'s idempotency matters even for a
database that's always empty going in — the same steps also run
unchanged if a CI retry reuses a stack that got as far as step 3 before
failing.

---

## 16. Architectural Constraints

1. **Modular monolith, not microservices** (ADR-0001). Module boundaries
   via import direction, not network calls.
2. **No Kubernetes.** Docker Compose only.
3. **No Kafka / message broker.** All flows are synchronous, in-process
   calls, transactional against Postgres.
4. **No machine learning.** All detection is deterministic (NFR-03).
5. **No authentication/authorization** (Section 14).
6. **No live network-device integration** (A-03).
7. **Domain independence.** `domain`/`detection` import no framework or
   database types (NFR-02).
8. **Vendor isolation.** New vendors = new adapter only (NFR-01).
9. **PostgreSQL is the only external service dependency.** No other
   external database, cache, or queue.
10. **Single backend instance.** No horizontal scaling/clustering of the
    `api` process; Postgres itself is a single instance too (no HA
    topology, product-spec Section 7).

---

## 17. Explicitly Deferred Components

Two different kinds of "not yet" — kept separate so nothing here is
mistaken for excluded from the MVP when it is only excluded from Slice 1:

### 17.1 Deferred from Slice 1, required for the final MVP

Not built in this architecture document's first slice, but named
functional requirements the final MVP (product-spec.md Section 6) still
needs:

- **React dashboard implementation** (FR-10) — the boundary is already
  defined (Sections 1, 2, 10, 15); the SPA itself is a later vertical
  slice, not a cut feature.
- **Arista adapter** (Section 5) — Cisco only for Slice 1.
- **Configuration drift detection** (Section 8) and **telemetry/anomaly
  detection** (FR-05/FR-06) — wired in the next slice, using the same
  `UnitOfWork` and post-commit logging rule already established here.

### 17.2 Deferred beyond the final MVP (non-goals)

Not designed or built for any MVP milestone (product-spec Section 7):

- **Repair Support System.**
- **Authentication, authorization, multi-tenancy** — Section 14.
- **Baseline re-designation** — baseline is fixed at first submission
  (A-02); an API to designate a *different* snapshot as baseline is
  deferred.
- **External alert integrations** — stdout JSON is the only channel.
- **Horizontal scaling / high availability** — for both `api` and
  Postgres.
- **Live device polling or config push** — A-03.
- **Third+ vendor adapters** — Juniper, Nokia, Huawei, etc.
- **Full drift-to-incident severity table** — beyond "removed ACL →
  Medium," a general mapping for every drift kind.
- **Incident resolution / reopen workflow** — `status` values beyond
  `OPEN` exist in the enum (domain-model.md Section 16) but nothing
  transitions an incident into them yet; this also means the dedup
  fingerprint's `OPEN`-only scope (A-09) is currently the whole story.
- **Config snapshot replay/re-parse** — the raw/normalized split makes
  this possible later; no replay mechanism is built.
- **Async/queued incident processing** — synchronous within one DB
  transaction (Section 12).

---

## Summary: First Vertical Slice Mapped to This Architecture

```
Cisco config text (spine-01, one submission)
   │  api → application.ConfigIngestionService
   ▼
CiscoAdapter.parse()                                       [Section 5]
   ▼
NormalizedConfiguration                                     [Section 6]
   │  stored on ConfigurationSnapshot; first submission →
   │  current_snapshot_id = baseline_snapshot_id = this snapshot
   ▼
PolicyEvaluator.evaluate(device_id, snapshot_id, observed_at, config, policies)  [Section 7]
   │  → one ConfigurationViolation: MISSING_REQUIRED_ACL, carrying source_snapshot_id
   ▼
IncidentFactory.build_candidate + uow.incidents.upsert_open_incident         [Section 9]
   │  → IncidentUpsertResult{incident: {severity: Medium, source: POLICY_VIOLATION, ...}, outcome: CREATED}
   ▼
uow.commit() → stdout JSON log (includes outcome)             [Sections 9, 11, 12, 13]
   ▼
201 response with normalized_config + violation/incident counts  [Section 10.1]
   ▼
GET /incidents                                                [Section 10]
   ▼
Operator sees the incident with evidence + recommendation
```

Drift (Section 8), telemetry/anomaly (not in this document's scope until
a later slice), Arista, and the React dashboard are explicitly **not**
part of this slice.

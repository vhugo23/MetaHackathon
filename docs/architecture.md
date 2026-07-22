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
    command: IngestConfigurationCommand
) -> ConfigIngestionResult

IngestConfigurationCommand
  device_id: string
  vendor: string             # external, unresolved caller string — never
                              # trusted as a domain VendorType directly
                              # (Day 5A binding correction; see below)
  raw_config_text: string    # preserved exactly, never stripped
  observed_at: timestamp     # caller-supplied, timezone-aware UTC

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
receives from step 10 below into the `incidents_created`/
`incidents_updated` counts on the `ConfigIngestionResult` it returns; no
caller of `ingest()` ever sees an `IncidentUpsertResult` directly.

`ConfigIngestionService.ingest` performs exactly this sequence (binding,
implemented Day 5A):

1. Validate the command (non-empty `device_id`/`vendor`, non-empty
   `raw_config_text`, timezone-aware UTC `observed_at`).
2. Resolve the vendor through `AdapterRegistry.resolve(command.vendor)` —
   unknown vendor → `UnsupportedVendorError`, no further processing, no
   `UnitOfWork` opened yet.
3. Parse and normalize (`adapter.parse(command.raw_config_text)`, exactly
   once) — **before** any persistent change. A `ParseError` result raises
   `ConfigurationParseError` (the `ParseError` preserved verbatim on
   `.parse_error`), nothing written, no `UnitOfWork` opened.
4. Derive the canonical `VendorType` from `adapter.vendor_id` (never from
   `command.vendor` directly), and generate exactly one snapshot ID via an
   injected `snapshot_id_factory` — an empty/whitespace-only generated ID
   raises `ValueError`, again before any `UnitOfWork` is opened.
5. Only now, open one `UnitOfWork` (Section 11).
6. Create or update `Device` state (`uow.devices`) — a new device is
   staged with null references, then re-saved once the snapshot exists
   (step 7); an existing device is saved with *this call's* canonical
   vendor (never silently preserving the stored vendor), the new
   `current_snapshot_id`, and its existing `baseline_snapshot_id`/
   `created_at` unchanged — a vendor change therefore surfaces as
   `DeviceConflictError` from `uow.devices.save` itself, after the
   snapshot has already been staged, relying on whole-transaction
   rollback rather than a duplicate service-level check.
7. Save the immutable `ConfigurationSnapshot` (`uow.configuration_snapshots`)
   — `normalized_config` embedded inline, `submitted_at = command.
   observed_at` (Section 6: normalization itself carries no timing field).
8. Read applicable policies
   (`uow.configuration_policies.get_applicable_to_device(device_id)`).
9. Evaluate: `PolicyEvaluator.evaluate(device_id, snapshot_id, observed_at,
   config, policies) -> tuple[ConfigurationViolation, ...]` (Section 7) —
   every violation carries `source_snapshot_id = snapshot_id` and
   `detected_at = observed_at`, both passed in, never read from a clock or
   repository inside the evaluator.
10. For each violation: `IncidentFactory.build_candidate` → `uow.incidents.
    upsert_open_incident(candidate, fingerprint, observed_at) ->
    IncidentUpsertResult` (Section 9) — atomic, one call per finding.
11. `uow.commit()` — once, for every write above. Any exception raised
    after step 5 triggers `uow.rollback()` then `uow.close()` (each
    attempted exactly once; a secondary rollback/close failure is recorded
    as an exception note rather than replacing the original exception),
    and the original exception is re-raised unchanged.
12. Return the response DTO (Section 10.1), tallying
    `incidents_created`/`incidents_updated` directly from each result's
    `.outcome` — never inferred from `occurrence_count`, never a second
    lookup.

Drift detection (Section 8) is **not** one of the steps above — for a
device with a prior submission, it is wired into this same sequence
(after step 9, before step 11) in the *next* slice (product-spec
Section 11), reusing the same `uow`.

**Structured logging (originally planned as step 10/13 here) is deferred
past Day 5A**, along with the `POST /devices/{id}/config` HTTP layer
itself (Day 5B) — `ConfigIngestionService` implements steps 1–12 above and
nothing beyond them. When logging is added, the same rule already
documented in Section 13 applies: a failure while writing the log after a
successful commit must not roll back durable data or turn the response
into a `PersistenceError` — the database commit is authoritative; logging
is a best-effort side channel once that commit has already succeeded.

### 4.1 Clock (explicit time) — deferred past Day 5A

Originally planned as an injected `Clock` port so `application` reads
`now_utc()` once per operation. Day 5A's binding correction instead makes
`observed_at` a direct, caller-supplied field on
`IngestConfigurationCommand` — `ConfigIngestionService` takes no `Clock`
dependency this phase; a future `POST /devices/{id}/config` handler
(Day 5B) is expected to supply `observed_at` (e.g. via a `Clock` at the
API boundary). `domain`/`detection` still never read the system clock
either way (NFR-02/NFR-03) — `PolicyEvaluator` continues to receive
`observed_at` as a plain passed-in argument (Section 7).

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
  raises `UnsupportedVendorError` — a `domain`-level exception, not
  `ParseError` (resolving the vendor and parsing its content are two
  separate failure modes); the `api` layer translates it to HTTP 422 with
  `code: "unsupported_vendor"` (Section 12, Day 5B) — never 400.
- First vertical slice: Cisco only. **Arista EOS is now implemented as of
  Day 8A** (`AristaAdapter`, `meta_rne.adapters.arista`) — a second,
  independent adapter registered alongside `CiscoAdapter` in the
  production `AdapterRegistry`, implementing the narrow EOS subset
  documented in `CLAUDE.md`'s "Current Phase" — never sharing code with
  `CiscoAdapter`. A third vendor remains unimplemented.

### 5.1 Cisco IOS-XE Parser Contract (representative, binding)

`CiscoAdapter.parse(raw_text)` returns a `ParseError` **value** (never a
thrown exception) for each of these — this is the full contract; Slice 1
may implement only the starred (`*`) subset, with the rest completed
before FR-02 is considered done. At the `api` layer, `application` wraps
any such `ParseError` in `ConfigurationParseError`, which `api/errors.py`
translates to HTTP **422** with `code: "configuration_parse_error"`
(Section 12, Day 5B) — the parser itself has no HTTP status, only the
translation at the API boundary does:

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
│     ├── static_routes: [{ prefix, next_hop }]  # deferred (Day 3A) — not yet
│     │                                          # a field on NormalizedRouting;
│     │                                          # see domain-model.md Section 5
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
ConfigurationPolicyRepository.get_applicable_to_device(device_id)
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
phase; the Slice 1 policy applies only to `spine-01`. **Day 8A adds a
second, independent, exact-match policy scoped to `leaf-02`** (see Section
18 below) — this remains unchanged exact-match evaluation, never a
shared or wildcard-applicable policy; the diagram's `"*"` case is still
unimplemented. Violations are
returned in a deterministic order: `policies` tuple order, then each
policy's `required_acls` tuple order — never re-sorted or
set-deduplicated. `PolicyEvaluator` is pure domain/detection logic: plain
inputs in, a tuple out, no I/O.

---

## 8. Configuration Drift Detection Flow

Implements FR-04, AC-05/AC-06. **Implemented as of Day 9** — see Section 20
for the full implementation record (application service, HTTP route, error
mapping, and verification).

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
- Comparison walks each top-level collection that actually exists on
  `NormalizedConfiguration` today — `interfaces` and `bgp_neighbors`
  (keyed by name / neighbor IP, diffing scalar fields within matches) and
  `acls` (keyed by name, whole-ACL addition/removal only — a matched
  ACL's `entries` collection is not compared in this slice, since no
  ACL-entry-level diff contract is defined). `static_routes` is not
  compared, since `NormalizedRouting` does not implement it yet (Section
  5). `hostname` is a top-level scalar, not a collection, and is likewise
  not compared. See Section 20 for the exact resource-naming and
  scalar-value conventions.
- **Incident creation from a drift finding is not implemented.** As of
  Day 9, `GET /devices/{device_id}/drift` is a pure, on-demand query —
  `application` does not yet decide which diff entries are
  incident-worthy, no `IncidentSource.DRIFT` exists, and no
  `Incident`/`UnitOfWork.commit()` is ever produced by this flow. A
  general drift-severity table (and the incident-emission decision this
  paragraph previously described as part of "the slice this unblocks")
  remains deferred (Section 17).

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

Satisfies FR-08. **No success envelope (Day 5B binding correction,
superseding this section's original NFR-05-derived design;
`docs/product-spec.md`'s NFR-05 table was corrected to match in a
follow-up documentation patch):** a successful response body *is* the
resource itself — `POST /devices/{id}/config` returns
`SubmitConfigurationResponse` directly, `GET /incidents` returns
`list[IncidentResponse]` directly, never wrapped
in `{"data": ..., "error": null}`. A failed response is a bare
`ApiErrorResponse`:

```json
{"code": "string", "detail": "string"}
```

`code` is lowercase snake_case (e.g. `unsupported_vendor`,
`configuration_parse_error`, `device_conflict`), and the message field is
named `detail`, not `message` — see Section 12 for the full, corrected
category-to-status-to-code mapping.

**Stable OpenAPI contract (Day 6A).** Each route declares an explicit
`operation_id` — `health_check`, `submit_device_configuration`,
`list_incidents` — so a generated frontend client's method names survive a
future path or handler rename. `POST /devices/{device_id}/config` and
`GET /incidents` also declare their real error responses (`409`/`422`/`500`
with `ApiErrorResponse`, plus FastAPI's own `HTTPValidationError` for
request-schema `422`s) in the generated OpenAPI document (`GET
/openapi.json`), not only FastAPI's default `201`/`200` + validation-only
`422`. See `docs/frontend-api-contract.md` for the full, current
frontend-facing contract (request/response shapes, error catalog,
examples) — this section states the boundary's design, that document
states its current concrete shape.

**CORS (Day 6A).** Disabled by default — `create_app`'s
`cors_allowed_origins: tuple[str, ...] = ()` and the production
entrypoint's `META_RNE_CORS_ALLOWED_ORIGINS` environment variable
(`meta_rne.api.cors`) both default to non-permissive. When configured,
`CORSMiddleware` is registered with the exact given origin list (never a
wildcard), `allow_credentials=False`, `allow_methods=["GET", "POST",
"OPTIONS"]`, `allow_headers=["Content-Type"]`. `docker-compose.yml`'s
local-development default sets it to `http://localhost:5173` — the future
Vite dev server's origin (FR-10 dashboard, not yet built) — so CORS stays
disabled everywhere else composition doesn't explicitly opt in.

**Endpoints, first vertical slice + full MVP:**

| Method | Path | Purpose | FR | Slice |
|---|---|---|---|---|
| `POST` | `/devices/{id}/config` | Ingest a vendor config | FR-01–03 | **1** |
| `GET` | `/incidents` | List incidents, filter by `device_id`/`severity` | FR-07, FR-08 | **1** |
| `POST` | `/incidents/{id}/resolve` | Explicitly resolve one `OPEN` incident (`OPEN -> RESOLVED` only, idempotent) | FR-08 | Day 7A |
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

### 10.1 `POST /devices/{id}/config` — Success Response (binding, Day 5B)

```json
// 201 Created — the response body IS this object, no envelope
{
  "device_id": "spine-01",
  "snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "normalized_config": {
    "hostname": "spine-01",
    "interfaces": [
      {
        "name": "GigabitEthernet0/1",
        "description": null,
        "ip_address": "10.0.0.1/30",
        "mtu": null,
        "admin_state": "up",
        "acl_in": null,
        "acl_out": null
      }
    ],
    "routing": { "bgp_neighbors": [] },
    "acls": []
  },
  "violations_detected": 1,
  "incidents_created": 1,
  "incidents_updated": 0
}
```

Note `routing` has no `static_routes` key — `NormalizedRouting` (Section
6) carries no such field yet (Day 3A's documented gap), and the response
schema (`SubmitConfigurationResponse`/`NormalizedConfigurationResponse`,
`api/schemas.py`) invents no field the domain type doesn't actually have.

This is the JSON body of `SubmitConfigurationResponse` (`api/schemas.py`,
built via `.from_domain(ConfigIngestionResult)`) — an `api`-layer schema
explicitly converted from the `application`-layer `ConfigIngestionResult`
(Section 4), never a domain entity serialized directly.
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

### 10.2 `POST /incidents/{id}/resolve` — Success Response (binding, Day 7A)

```json
// 200 OK — the response body IS the resolved incident, no envelope
{
  "incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "fingerprint": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "device_id": "spine-01",
  "source": "POLICY_VIOLATION",
  "rule_ref": "policy-acl-external-in",
  "affected_resource": "interface:GigabitEthernet0/1:acl_in",
  "severity": "Medium",
  "status": "RESOLVED",
  "evidence": { "...": "unchanged" },
  "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
  "created_at": "2026-07-18T10:00:00Z",
  "last_seen_at": "2026-07-18T10:00:00Z",
  "occurrence_count": 1,
  "updated_at": "2026-07-18T11:00:00Z",
  "resolved_at": "2026-07-18T11:00:00Z"
}
```

This is exactly `IncidentResponse` (`api/schemas.py`) — the same schema
`GET /incidents` returns — with `updated_at`/`resolved_at` now populated
(both equal to the one `Clock` value the resolution captured); no wrapper,
no separate "resolve response" type. No request body: `incident_id` is
the only input, taken from the path. `application.ResolveIncidentService`
(Section 11.3) is the only place a resolution is performed — the route
itself does not touch `status`, `resolved_at`, or `updated_at` directly.

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
  close() -> None
```

The **SQLAlchemy `UnitOfWork`** (Day 4B3) is constructed from a `session_
factory: Callable[[], Session]` — never an already-created `Session` — and
creates exactly one `Session` internally, shared by all four repositories,
so every write in one `ingest()` call (Section 4) lands in one transaction.
`commit()` calls the real `Session.commit()`; on any exception it rolls
back and re-raises the original exception unchanged (never swallowed or
replaced). `rollback()`/`close()` delegate directly to the `Session`. No
context-manager (`__enter__`/`__exit__`) syntax is added this phase. The
**in-memory `UnitOfWork`** (tests) provides the same observable `commit`/
`rollback`/`close` contract via an isolated *working* `InMemoryStore`,
copied (with fresh locks, never the committed store's lock instances) from
a shared *committed* `InMemoryStore` at construction time: `commit()`
publishes all four collections into the committed store at once, under
the committed store's own lock; `rollback()` discards the working store's
changes by resetting it back to the committed store's current state and
publishes nothing; `close()` performs no I/O. A second `UnitOfWork`
constructed against the same committed store afterward sees exactly what
was committed — no more, no less.

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
    get_by_id(incident_id); list_all()
        # list_all() returns every stored Incident ordered ascending by
        # created_at then incident_id — no filter parameter; device_id/
        # severity filtering is deferred to the application/API layer
        # (Day 4B3 binding decision, corrects this section's earlier
        # `list(filter)` — domain/ports.py has declared list_all() with no
        # filter since Day 4B1)
    # Day 4B1 binding decision: no find_open_by_fingerprint on this port —
    # dropped from the public surface; the atomic upsert below is the
    # deduplication mechanism.
    upsert_open_incident(candidate, fingerprint, observed_at) -> IncidentUpsertResult
        # THE write path — atomic create-or-update. No plain save(): every
        # write goes through this one operation, so nothing can bypass the
        # dedup guarantee via a find-then-save race. Rejects (ValueError,
        # before any mutation): fingerprint/observed_at inconsistent with
        # candidate, an unsupported candidate.source, or an empty generated
        # incident_id; a stale observed_at (older than the existing OPEN
        # incident's last_seen_at) is also a ValueError with no mutation —
        # equal timestamps are accepted (Day 4B3).
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
    last_seen_at = EXCLUDED.last_seen_at,
    occurrence_count = incidents.occurrence_count + 1,
    severity = EXCLUDED.severity,
    evidence = EXCLUDED.evidence,
    recommendation = EXCLUDED.recommendation
WHERE EXCLUDED.last_seen_at >= incidents.last_seen_at
RETURNING incident_id, fingerprint, device_id, source, rule_ref,
    affected_resource, severity, status, evidence, recommendation,
    created_at, last_seen_at, occurrence_count, (xmax = 0) AS was_inserted;
```

`incident_id`, `fingerprint`, `device_id`, `source`, `rule_ref`,
`affected_resource`, `status`, and `created_at` are never in the `DO UPDATE
SET` list — an update only ever refreshes `last_seen_at`, `occurrence_count`,
`severity`, `evidence`, and `recommendation` (Day 4B3), preserving every
identity/classification field of the existing row. The `WHERE
EXCLUDED.last_seen_at >= incidents.last_seen_at` guard makes a stale
observation (an `observed_at` older than the stored row's `last_seen_at`)
affect no row at all — equal timestamps still pass the `>=` guard and
increment `occurrence_count`. When that guard suppresses the update,
`RETURNING` yields no row; the repository then issues one internal,
non-public follow-up `SELECT` (never exposed as a port method) solely to
distinguish "genuinely stale" (raises `ValueError`, no mutation) from an
unexpected empty result (raises `PersistenceError`) — this is the only
read the repository ever performs as part of `upsert_open_incident`, and it
never substitutes for the atomic statement above (no read-before-write).

`xmax = 0` is PostgreSQL's standard tell for "this row was freshly
inserted, not touched by the `ON CONFLICT` branch" — the SQLAlchemy
repository maps that boolean straight to `outcome` (`CREATED`/`UPDATED`)
in the same round trip, returning explicit named columns (never the whole
ORM row or the raw `xmax` value) and keeping the `xmax` expression private
to that one module. `ConfigIngestionService` never infers the outcome
from `occurrence_count` and never issues a second lookup (Section 4, step
11). `incident_id` is generated by the repository via an injected
`incident_id_factory: Callable[[], str]` (production default:
`str(uuid4())`) once per call, before the statement executes — an upsert
that loses the insert race may generate an unused id, which is acceptable
(Day 4B3). A `device_id` that does not reference an existing `Device`
raises `ReferencedDeviceNotFoundError`, translated from the `incidents.
device_id` foreign-key violation inside a SAVEPOINT
(`session.begin_nested()`), leaving the caller's Session fully usable
afterward — the same pattern `ConfigurationSnapshotRepository.add` already
uses (Day 4B2).

The in-memory test double reproduces the same `IncidentUpsertResult`
contract, including under concurrent calls (guarded by one critical
section spanning its own find-OPEN-by-fingerprint -> decide -> mutate
sequence, plus the same Device-existence check), verified by the
conformance suite (test-strategy.md Section 9) against both
implementations.

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

### 11.3 Incident Resolution (Day 7A)

The first incident-lifecycle mutation: an operator explicitly transitions
an `OPEN` incident to `RESOLVED` via `POST /incidents/{id}/resolve`
(Section 10.2). Three layers, each with one narrow responsibility:

```
application.ResolveIncidentService.resolve(incident_id) -> Incident
    │  one UnitOfWork per call, same exception-preserving rollback/close
    │  lifecycle as ConfigIngestionService/ListIncidentsService
    ▼
uow.incidents.get_by_id(incident_id)
    ├─ None                    → IncidentNotFoundError, no Clock call, no commit
    ├─ already RESOLVED        → return unchanged, zero Clock calls, no commit
    └─ OPEN                    → Clock.now() called exactly once, then:
         uow.incidents.resolve(incident_id, resolved_at) -> Incident | None
             │  one atomic conditional SQL UPDATE (SQLAlchemy) or one
             │  lock-guarded domain-method call (in-memory) — never a
             │  generic full-row save(); only status/resolved_at/updated_at
             │  are ever written, so a concurrent upsert_open_incident on
             │  the same row can never be clobbered, or vice versa
             ▼
         uow.commit() → return the repository's persisted result
```

**`Clock` (application-layer port, distinct from Section 4.1's deferred
domain-level `Clock`).** A minimal structural protocol,
`meta_rne.application.incident_resolution.Clock` (`.now() -> datetime`),
that `ResolveIncidentService` depends on — `application` still never
imports `meta_rne.api.clock` or calls the system clock directly. The API
composition layer (`api/app.py`) supplies a `CallableClock`
(`meta_rne.api.clock`) that adapts `create_app`'s existing
`clock: Callable[[], datetime]` parameter — the **same** injected time
source already used for `POST /devices/{id}/config`'s `observed_at`, never
a second, independent clock — validating each read via the existing
`require_utc`.

**`IncidentRepository.resolve(incident_id, resolved_at) -> Incident | None`**
(both SQLAlchemy/PostgreSQL and in-memory) is deliberately narrow, not a
generic `save()` — a full-row save risked clobbering a concurrent
`upsert_open_incident`'s writes to `occurrence_count`/`evidence`/
`last_seen_at`/`severity` on the same row. The SQLAlchemy implementation is
one atomic conditional statement, the same "never a read-before-write"
idiom as `upsert_open_incident` (Section 9):

```sql
UPDATE incidents
SET status = 'RESOLVED', resolved_at = :resolved_at, updated_at = :resolved_at
WHERE incident_id = :incident_id
  AND status = 'OPEN'
  AND updated_at <= :resolved_at
RETURNING <explicit columns>;
```

The `updated_at <= :resolved_at` guard — checked against `updated_at`, not
`last_seen_at` — is what makes resolution monotonic: an `OPEN` incident may
legally already have `last_seen_at < updated_at` (nothing else currently
produces that gap, but the invariant is defended unconditionally), and a
`last_seen_at`-only check would let a resolution move `updated_at`
backward. When the statement affects no row, one internal, non-public
follow-up `SELECT` (`populate_existing=True`, so a Session's earlier
identity-map-cached object is never returned stale) distinguishes exactly
which of four cases occurred — never a read-before-write, and never an
apparent-success return for an unresolved conflict:

| Follow-up finds | Repository returns |
|---|---|
| no row | `None` (caller raises `IncidentNotFoundError`) |
| `RESOLVED` | that incident, unchanged (idempotent success) |
| still `OPEN` (supplied `resolved_at` was stale) | raises `ValueError` |
| any other status (the dormant `ACKNOWLEDGED`) | raises `ValueError` |

The in-memory repository performs the equivalent transition under its
existing `incidents_lock`, delegating the actual invariant enforcement to
`Incident.resolve(at)` (domain-model.md Section 10).

**Recurrence and the partial unique index.** `ux_incidents_open_fingerprint`
(`UNIQUE (fingerprint) WHERE status = 'OPEN'`, Section 9, unchanged since
Day 4B1) already excludes any `RESOLVED` row — no migration or index change
was needed for Day 7A. This is what lets the same fingerprint recur: once
an incident is resolved, `upsert_open_incident`'s `ON CONFLICT` target no
longer sees a conflicting row, so reingesting the identical finding inserts
a **new** `OPEN` incident (new `incident_id`, `occurrence_count: 1`) rather
than reopening the resolved one — the historical `RESOLVED` row is left
completely unchanged, and further reingestion against the new `OPEN`
incident deduplicates exactly as it always has (Section 9). Proven by real
PostgreSQL tests (test-strategy.md Section 9), not by any behavior change
to `upsert_open_incident` itself.

**Concurrency guarantees and explicit limits.** Two concurrent
`resolve()` calls against one incident: the atomic conditional `UPDATE`
means only one transaction's `WHERE status = 'OPEN'` can match; the other
falls through to the follow-up `SELECT`, finds the row already `RESOLVED`,
and returns it unchanged — both callers receive a consistent, persisted
`RESOLVED` result, never a corrupted row or a duplicate. A committed
ingestion update that lands between a resolve's read and its write is
similarly safe: `resolve()` only ever writes `status`/`resolved_at`/
`updated_at`, so `occurrence_count`/`last_seen_at`/`evidence` from that
ingestion are preserved. No advisory lock, distributed lock, queue, Redis,
optimistic-version column, retry loop, or `SERIALIZABLE`-isolation change
was introduced to achieve any of this — the existing atomic-statement
design (Section 9's own precedent) was already sufficient.

---

## 12. Error Handling Strategy

Layered, matching Section 2's dependency structure:

- **Adapter layer** — parse failure returns a structured `ParseError`,
  never a thrown exception.
- **Domain/detection layer** — pure functions return a valid result or an
  empty result (e.g., zero violations); no throwing for expected
  conditions.
- **Application layer** — raises specific, typed exceptions (never a bare
  `Exception`): `UnsupportedVendorError` (domain), `ConfigurationParseError`
  (application, wrapping a `ParseError` verbatim), `IncidentNotFoundError`
  (application, Day 7A — preserves `incident_id` as structured data),
  `DeviceConflictError`/`SnapshotAlreadyExistsError`/
  `ReferencedDeviceNotFoundError` (persistence), plain `ValueError` for
  other caller/application invariants (including the repository's own
  resolution-transition/monotonicity failures, Section 11.3 — e.g. the
  dormant `ACKNOWLEDGED` status, or a stale resolution timestamp),
  `PersistenceError` (persistence, base), and `SerializationError`
  (persistence). No HTTP knowledge.
- **API layer** — maps categories to status + `code` (`api/errors.py`,
  Day 5B binding correction — supersedes this table's original
  `product-spec.md` NFR-05 values, which `product-spec.md` itself was not
  updated to match this phase):

| Category | Status | `code` |
|---|---|---|
| Request schema validation (FastAPI/Pydantic `RequestValidationError`) | 422 | FastAPI's own default body — no custom `ApiErrorResponse` |
| `UnsupportedVendorError` | 422 | `unsupported_vendor` |
| `ConfigurationParseError` | 422 | `configuration_parse_error` |
| `DeviceConflictError` | 409 | `device_conflict` |
| `SnapshotAlreadyExistsError` | 409 | `snapshot_already_exists` |
| `ReferencedDeviceNotFoundError` | 409 | `referenced_device_not_found` |
| other caller/application `ValueError` | 422 | `invalid_request` |
| `PersistenceError` (registered after the three conflict subclasses above) | 500 | `persistence_error` (generic public detail) |
| `SerializationError` | 500 | `serialization_error` (generic public detail) |
| `IncidentNotFoundError` (Day 7A) | 404 | `incident_not_found` — `detail` built from `exc.incident_id`, never `str(exc)` |
| `InvalidClockError` (injected clock returned non-UTC/naive — a server-composition failure, not caller input) | 500 | no custom handler — falls through to FastAPI's normal unmapped-exception behavior |
| anything else unmapped | 500 | no custom handler — FastAPI's normal production 500 behavior, never a broad catch-all echoing exception internals |

`Resource not found` (404) was **not in Slice 1** (no single-resource `GET`
endpoint existed) but is no longer hypothetical as of Day 7A:
`POST /incidents/{incident_id}/resolve` is the first endpoint to produce it,
mapped from `IncidentNotFoundError` above — the dormant `ACKNOWLEDGED`
status and a stale resolution timestamp are deliberately **not** mapped to
404; both remain internal invariant failures (`ValueError` → 422
`invalid_request`, Section 11.3), since neither means "the incident doesn't
exist."

- **No silent failures.** Every error reaching the API boundary produces a
  structured `ApiErrorResponse{code, detail}` body (or, for request-schema
  validation, FastAPI's own structured 422 body) — never an unhandled
  stack trace. If `uow.commit()` fails after a violation was found, the
  whole ingestion request fails with `persistence_error`/500 and
  `uow.rollback()` runs — a config is never "saved but its incident
  silently lost."
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
- **Overridable host ports (Day 6A).** `db`'s and `api`'s host-side ports
  are `${META_RNE_DB_HOST_PORT:-5432}`/`${META_RNE_API_HOST_PORT:-8080}` —
  defaults unchanged for ordinary use; a repeatable, isolated smoke run
  (below) overrides both so it never collides with a developer's own
  Postgres or a prior `docker compose up` session.

**Compose smoke validation (Day 6A, distinct from the E2E suite in
Section 15.1 below).** `scripts/compose_smoke.py` (repo root,
Python-standard-library only) proves the deployed shape works: real image
build, real `db`/`api` startup and healthchecks, real Alembic migration
before Uvicorn (confirmed via container logs and `alembic_version`),
real idempotent Slice 1 policy seeding, three real HTTP config
submissions against `spine-01` (the device the seeded policy applies
to) proving `violations_detected`/`incidents_created`/`incidents_updated`
counts and incident-evidence fields, a real `docker compose restart api`
proving PostgreSQL-backed state survives an API process restart with no
database reset, and project-scoped cleanup (`--project-name`, never
`COMPOSE_PROJECT_NAME`) so it can never affect a differently-named
Compose project or an unrelated container. This is **not** a substitute
for the still-unbuilt Playwright E2E suite (Section 15.1) — it proves the
deployed shape boots and serves real traffic correctly, not the full
acceptance-test matrix. Invocation:

```
python scripts/compose_smoke.py \
  --project-name meta-rne-smoke \
  --api-port 58080 \
  --db-port 55432
```

Run identically by a developer and by CI's `compose-smoke` job
(`.github/workflows/ci.yml`) — the script is the single authoritative
implementation; the CI job only invokes it.

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

### 15.2 Browser (Playwright/Chromium) End-to-End Path (Day 6D, binding)

**Distinct from Section 15.1 above.** Section 15.1 describes a still-
unbuilt *HTTP-mode* Playwright suite (`e2e/vertical-slice/`,
`compose.e2e.yml`, test-strategy.md Section 7) that drives the API directly
via `request` — no browser, no frontend. Day 6D instead built a *browser*-
mode suite, a genuinely different thing, that exists specifically to prove
the browser-level wiring (CORS, real cross-origin fetches, the rendered
React UI) that an HTTP-only suite cannot:

```
Playwright (Chromium)
  → real `vite preview` production build (frontend/, real static assets)
  → real React frontend (IncidentDashboard / ConfigurationSubmissionForm)
  → cross-origin HTTP (real fetch(), real CORS preflight/response headers)
  → real FastAPI (Section 4/10: POST /devices/{id}/config, GET /incidents)
  → real, disposable PostgreSQL (Section 11)
```

**Orchestration ownership (`scripts/browser_e2e.py`, Python standard
library only — the single authoritative implementation, same discipline as
`scripts/compose_smoke.py`):**

```
scripts/browser_e2e.py
  → reserve three host ports simultaneously (db, api, frontend preview)
  → create an isolated, uniquely-named Compose project
  → release the db/api reservations, start db + api (existing
    docker-compose.yml — unchanged, no new Compose file)
  → wait for container health, then GET /health
  → build the frontend with VITE_API_BASE_URL baked in
  → release the frontend-port reservation, start `vite preview` directly
    through node (never npm)
  → wait for GET / to return 200
  → run the Playwright suite (Section 15.3, Day 7C: two scenarios,
    discovered from frontend/e2e/*.spec.ts with no file-path argument —
    adding the second scenario required no change here),
    PLAYWRIGHT_BASE_URL == the frontend origin,
    META_RNE_CORS_ALLOWED_ORIGINS == the same origin
  → terminate the preview process (bounded, cross-platform)
  → docker compose down --volumes --remove-orphans (always, in `finally`)
  → verify no project-scoped container or volume remains
```

**Explicitly not containerized.** The frontend is never built into a Docker
image and no `frontend` Compose service was added — `docker-compose.yml`
(Section 15) is reused as-is, exactly as `scripts/compose_smoke.py` already
reuses it; only `db` and `api` run under Docker, while the frontend runs as
a plain `vite preview` Node process on the host, matching how a developer
would actually run it locally.

**Observation, never fulfillment.** Both Playwright specs
(`frontend/e2e/config-submission-refresh.spec.ts`, and, as of Day 7C,
`frontend/e2e/incident-resolution.spec.ts`, Section 15.3) count and assert
on real requests/responses (`page.on("request")`,
`page.waitForRequest()`/`page.waitForResponse()`) — neither ever calls
`page.route()` to intercept or fulfill an API response. Every assertion
(POST/response bodies, refreshed incident lists, reload persistence)
reflects what the real backend and real database actually returned.

**One worker, by design.** The suite runs with exactly one Playwright
worker against exactly one disposable database populated by exactly one
idempotently-seeded policy (Section 11.2) — there is no proof that two
workers could safely share one fresh database concurrently, so no such
claim is made or relied upon. As of Day 7C, this single-worker execution
is **not** the sole isolation mechanism between the suite's two scenarios
— see Section 15.3's "Isolation" subsection for the actual guarantee
(stable-identity-plus-exact-lifecycle-status card selection), which holds
regardless of worker count or discovery order, and was verified directly
under both possible execution orders, not merely inferred from
`workers: 1`.

**Scope, explicitly bounded.** Day 6D is Chromium only (no Firefox/WebKit
project), no mobile/device-emulation project, and no visual-regression
snapshot testing. It is distinct from, and does not anticipate the design
of, any future multi-browser suite, visual-regression suite, or production-
deployment pipeline — none of those are designed here.

### 15.3 Second Browser Scenario: Incident Resolution (Day 7C, binding)

Day 7C adds a second Playwright scenario
(`frontend/e2e/incident-resolution.spec.ts`) against the **same**
orchestrated stack Section 15.2 already builds — no new Compose service,
no second orchestration invocation, no change to
`scripts/browser_e2e.py`/`scripts/test_browser_e2e.py`/
`frontend/playwright.config.ts`. `npm run test:e2e:direct` (`playwright
test`, no file-path argument) already discovers every `*.spec.ts` file
under `frontend/e2e/`, and `workers: 1`/`fullyParallel: false` already
serialize whatever it discovers — adding a second file changed the number
of tests the existing invocation runs, not the invocation itself.

```
UI submission (ConfigurationSubmissionForm)
  → real POST /devices/spine-01/config → establishes/updates one OPEN
    incident (Section 9's existing dedup contract — no new behavior)
   │
   ▼
UI resolution (IncidentCard's "Resolve incident" button)
  → real POST /incidents/{incident_id}/resolve (Section 10.2/11.3 —
    unchanged endpoint) → no request body, Accept: application/json
   │
   ▼
frontend applies the POST response directly (useIncidents.resolveIncident,
Day 7B) — the returned IncidentResponse replaces the matching array
element; no GET /incidents is issued as a consequence of resolving
   │
   ▼
page.reload() → real GET /incidents → confirms the RESOLVED state was
persisted in PostgreSQL, not merely held in React state
```

Every step above reuses existing, unchanged product behavior (Day 7A
backend, Day 7B frontend) — Day 7C adds no new endpoint, no new frontend
state, and no new database write path; it is proof, not construction.

**Isolation.** The two scenarios share one disposable PostgreSQL database
for the lifetime of one `playwright test` invocation (Section 15.2's
single orchestrated run). Neither scenario relies on the other's absence
or on running in a particular order:

- Each scenario independently establishes its own `OPEN` incident by
  submitting through the real UI form — never a direct API call, never
  raw SQL, never a test-only backend endpoint, never database truncation.
- Incident cards are selected through **stable identity fields
  (`device_id`/`rule_ref`/`affected_resource`) plus an exact visible
  lifecycle status** (`OPEN` or `RESOLVED`), never a bare/unscoped article
  locator and never a page-wide article count. A historical `RESOLVED`
  row is therefore always excluded when a scenario is locating its `OPEN`
  target, and a current `OPEN` row (e.g. one the other scenario is
  mid-flow with) is always excluded when locating a persisted `RESOLVED`
  target.
- Because `ux_incidents_open_fingerprint` (Section 9/11.3) excludes any
  `RESOLVED` row, a scenario that finds the shared `spine-01`/
  `policy-acl-external-in` fingerprint already `RESOLVED` creates a
  **new** `OPEN` recurrence when it submits (the same recurrence behavior
  Section 11.3's Gate 7A-D tests already proved at the repository level)
  rather than colliding with the historical row — this is what makes
  "historical RESOLVED incident" a safe, handled case rather than an edge
  case requiring cleanup.
- No cleanup logic runs between scenarios, and no scenario's assertions
  depend on the other scenario having run, having not run, or having run
  in a particular order.

This was verified directly against the real stack, not merely reasoned
about: the resolution scenario alone against a fresh database; the
resolution scenario discovered before the configuration scenario (so the
configuration scenario's own submission runs against a database that
already holds a historical `RESOLVED` incident with the same identity);
and the standard discovery order (configuration scenario first). All
three real orchestrated runs passed with cleanup verified.

**No production or contract change.** Day 7C changes no
`frontend/src/`/`backend/` file and no API contract
(`docs/frontend-api-contract.md` is unchanged) — every request/response
shape Section 15.3 exercises was already established by Day 7A/7B.

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
  slice, not a cut feature. **Day 6B built the first slice of it**
  (`frontend/`): a read-only dashboard consuming `GET /incidents` only,
  with loading/empty/error/populated states. **Day 6C built the second
  slice**: a configuration-submission form integrated into that same
  dashboard, POSTing to the already-existing
  `POST /devices/{device_id}/config` endpoint (Section 4, Section 10.1) and
  triggering exactly one incident refresh on success (Section 17.1.1
  below). Incident mutations, filtering/pagination, and authentication
  remain deferred — see `docs/frontend-api-contract.md` and README.md's
  "Current Day 6C scope". **Day 7A added the backend resolution endpoint
  (Section 10.2, Section 11.3) with no corresponding frontend change at
  all** — the dashboard still only read (`GET /incidents`) and submitted
  configuration; it had no resolve button or other way to call
  `POST /incidents/{id}/resolve`. **Day 7B built that third frontend
  vertical slice**: a "Resolve incident" control on every `OPEN` incident
  card, calling the same endpoint through a dedicated no-body transport
  primitive and a per-incident pending/error lifecycle (Section 17.1.2
  below) — see `docs/frontend-api-contract.md` Section 7 and README.md's
  "Current Day 7B scope". No new infrastructure, Docker Compose service, or
  CI job was added for either Day 7A or Day 7B.
- **Arista adapter** (Section 5) — Cisco only for Slice 1; **implemented as
  of Day 8A** (see CLAUDE.md's "Current Phase"), registered in the
  production `AdapterRegistry` alongside `CiscoAdapter` with no change to
  `ConfigIngestionService`, `api/routes.py`, or `api/schemas.py`. A third
  vendor remains later-slice scope.
- **Configuration drift detection** (Section 8) — implemented as of Day 9
  as a read-only query (`GetDeviceDriftService`, `GET
  /devices/{device_id}/drift`, Section 20); it does not write to
  `UnitOfWork` or emit a post-commit log, unlike the policy-violation
  path. **Telemetry/anomaly detection** (FR-05/FR-06) remains deferred.

### 17.1.1 Frontend ownership and data flow (Day 6C)

The frontend consumes both Slice 1 endpoints now, each behind its own
narrow ownership chain — neither shares state with the other, by design
(below):

```
IncidentDashboard
├── ConfigurationSubmissionForm
│   └── useConfigurationSubmission
│       └── submitDeviceConfiguration
│           └── postJson
└── useIncidents
    └── fetchIncidents
```

`IncidentDashboard` is the only component that calls `useIncidents()` — it
renders `ConfigurationSubmissionForm` as a child, but the form never reaches
into incident state, and `IncidentDashboard` never reaches into submission
state. The two branches meet at exactly one point: a callback prop.

**Successful submission flow:**

1. The user fills the controlled form (device ID, the single supported
   vendor, raw configuration text) and submits.
2. Local validation passes (non-blank device ID, non-empty configuration
   text) — no network call is made until this succeeds.
3. `useConfigurationSubmission` starts one `POST` with a fresh
   `AbortController`'s signal, transitioning to `submitting`.
4. `submitDeviceConfiguration` → `postJson` performs the request; on a `2xx`
   response, the API layer's runtime structural validators
   (`isConfigurationSubmissionResponse` and its nested per-field checks,
   `frontend-api-contract.md` Section 5) validate the complete body,
   including every field of `normalized_config` — never a bare cast.
5. The hook transitions to `success`, carrying the validated response.
6. The *latest committed* `onSuccess` callback (Section 17.1.1 below
   explains why "latest") is invoked exactly once — `IncidentDashboard`
   supplies `() => { refresh(); }`, the same `refresh` function already
   used by the dashboard's own Refresh/Retry controls.
7. `useIncidents` owns everything from here: it performs exactly one new
   `GET /incidents`, using its own existing abort-and-supersede request
   lifecycle (below) — nothing about that lifecycle is submission-aware.

**Why submission and incident state are intentionally separate.** A failed
`GET /incidents` refresh that happens to follow a successful `POST` is a
second, independent outcome — `useIncidents` transitions to its own `error`
state exactly as it would after a failed manual Refresh click, while
`useConfigurationSubmission`'s state remains `success`, untouched. Merging
the two into one state machine would make a refresh failure look like a
submission failure, which is not what happened. There is deliberately no
effect watching submission state to trigger a refresh — the `onSuccess`
callback is the only integration point, so it is straightforward to reason
about exactly when (and how many times) a refresh can occur.

**Concurrency guarantees, precisely scoped.** Both `useIncidents` and
`useConfigurationSubmission` pair one `AbortController` with a monotonically
increasing request ID per operation, checked alongside a mounted-ref before
any state update: a stale completion (superseded by a newer call, or
arriving after unmount) can never overwrite newer state, and a superseded
request's own `AbortError` never surfaces as a visible error. This guarantee
is scoped to *each hook's own* request sequence — `useConfigurationSubmission`
does not know about, and does not participate in, `useIncidents`'s request
ID or vice versa; the only thing crossing that boundary is the single
`refresh()` call made from `onSuccess`, which `useIncidents` then handles
exactly like any other externally-triggered refresh (aborting a pending
manual refresh if one happens to be in flight, per its own existing rules).
`useConfigurationSubmission` additionally holds its `onSuccess` callback in
a ref synced via `useLayoutEffect` (not a plain `useEffect`) so that a
callback-identity change committed just before a POST resolves is always
observed — a passive effect can still be pending when an already-in-flight
POST's promise settles, which would risk invoking a stale callback instead
of the latest one. This does not mean callback identity is watched or
compared to restart anything: `submit()` itself has no dependency on
`onSuccess` and never restarts or duplicates a POST because the callback
prop changed.

### 17.1.2 Frontend incident resolution (Day 7B)

Adds a third narrow ownership chain alongside the two in Section 17.1.1,
still reporting into `useIncidents` rather than a new hook — the incident
array has exactly one owner:

```
IncidentDashboard
├── ConfigurationSubmissionForm → useConfigurationSubmission → ...
└── useIncidents
    ├── fetchIncidents            (GET /incidents)
    └── resolveIncident           (POST /incidents/{id}/resolve)
        └── postNoBody
```

**API client.** `postNoBody` (`src/api/client.ts`) is a focused sibling to
`getJsonArray`/`postJson`: `method: "POST"`, `Accept: application/json`,
`credentials: "omit"`, an optional `AbortSignal`, and deliberately no `body`
key at all (not even `{}`) and no `Content-Type` header, since there is
nothing to describe the content type of. It reuses the same non-OK/
malformed-response handling every other transport function uses. `src/api/
incidents.ts`'s `resolveIncident` layers two validation passes on top: the
shared, forward-compatible `isIncidentResponse` structural check (Section
17.1.1's same validator `GET /incidents` uses), then this endpoint's own
narrower semantic check — the response is trusted only if `incident_id`
matches the request, `status` is exactly `"RESOLVED"`, and `resolved_at` is
non-null. Failing either check produces the same controlled
malformed-response error every other endpoint uses; the shared validator
itself is never tightened to make this endpoint-specific check redundant.

**Hook ownership — extended, not duplicated.** `useIncidents` gains
`resolvingIds: ReadonlySet<string>`, `resolveErrors: Readonly<Record<string,
string>>`, and a `resolveIncident(incidentId)` action, all colocated with
the existing `state`/`refresh` — no second `useResolveIncident` hook and no
second copy of the incident array. A `useRef<Map<string, AbortController>>`
keyed by `incident_id` is the *authoritative* same-incident duplicate
guard, populated synchronously before any `await`/`.then()`, so two calls
issued in the same tick (before React commits a `resolvingIds` update)
still can't both start a POST; two *different* incident IDs get independent
controllers, independent pending flags, and independent error entries, so
one incident's failure can never cancel or clear another's in-flight
request. Every completion path (`.then`/`.catch`) re-checks that its own
controller is still the map's current entry for that ID before doing
anything — a settled-but-superseded attempt (an old request whose retry has
already started) can never clear a newer retry's state.

**Successful resolution — local, atomic, no refresh.** On success, the
complete `IncidentResponse` the POST returned is applied via a functional
`setState` that finds the one matching `incident_id` in the current array
and replaces only that element — array order is preserved, and every other
element keeps its original object reference (no re-render-triggering copy
of unrelated incidents). `lastUpdatedAt` is left untouched, since it
represents the last list fetch/refresh, not a local resolution clock. No
`GET /incidents` is ever issued as a consequence of a resolution, success or
failure — the POST response is already the persisted, authoritative state.

**Stale-result reconciliation (GET and POST alike).** A shared, unexported
helper, `pickIncident(current, incoming)`, decides which of two
representations of the same `incident_id` is authoritative, using
`Date.parse` on both `updated_at` values — never lexicographic string
comparison, since two valid ISO timestamps do not always compare correctly
as strings once formatting varies. A definite instant ordering wins
outright; at equal instants, a `RESOLVED` value always outranks a
non-`RESOLVED` one (lifecycle safety — resolution is terminal in this UI),
then the higher `occurrence_count`, then the incoming value; if either
timestamp fails to parse, the function falls back to the same
`RESOLVED`-wins-or-prefer-incoming lifecycle rule rather than ever guessing
from raw string ordering. The identical helper is used in two places:
applying a resolve POST response against the currently-held incident (so a
resolution response that raced behind a concurrent refresh's newer data
never reverts it), and reconciling a fresh `GET /incidents` response against
the list already in state (`mergeIncidentLists`) — matching by
`incident_id`, preserving the incoming response's order, and *appending any
current-only incident the incoming response omitted*, in its prior order.
Retaining current-only incidents is correct specifically because
`GET /incidents` is unfiltered and append-only in the current scope and
there is no deletion endpoint — an older or narrower GET response is never
evidence that an incident was removed, only that this particular read raced
ahead of some other write. No generic optimistic-concurrency/versioning
scheme, global state library, or distributed synchronization primitive was
introduced to achieve any of this — the existing per-hook abort-and-supersede
idiom (Section 17.1.1) plus this one small timestamp-comparison helper were
sufficient. One residual, accepted limitation: if a manual refresh's GET is
*sent* before a concurrent resolution commits but its *response* arrives
after the resolution's, the refresh's (now-stale) data can transiently
redisplay the incident as `OPEN` until the next refresh corrects it — fixing
this fully would require a cross-request version/sequence number the
current scope does not need.

**Presentation.** `IncidentCard` remains purely presentational — it receives
`isResolving`/`resolveError`/`onResolve` as props, calls
`onResolve(incident.incident_id)`, and owns no asynchronous state itself;
`IncidentDashboard` only wires `resolvingIds`/`resolveErrors`/
`resolveIncident` from the hook down to each card, adding no local copy of
any of the three. A "Resolve incident" control renders only when
`incident.status === "OPEN"` exactly — `RESOLVED`, the dormant
`ACKNOWLEDGED`, and any unrecognized future status all render no action at
all. Pending state uses the native `disabled` attribute plus a visible
"Resolving…" label (the native attribute making a second click physically
inert, same idiom as the existing Refresh button); a per-incident error uses
`role="alert"` scoped inside that incident's own card, never affecting
another card's controls. `updated_at` renders unconditionally as "Updated";
`resolved_at` renders as "Resolved" only when populated — both via the same
`<time dateTime=... title=...>` convention `last_seen_at` already uses, no
new date library.

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
- **Incident reopen workflow, acknowledgment, assignment, comments/notes,
  audit history, and bulk resolution** — Day 7A added explicit `OPEN ->
  RESOLVED` resolution (Section 11.3, `POST /incidents/{id}/resolve`), the
  first and only transition; the dormant `ACKNOWLEDGED` status still has no
  public transition into it, and there is no reopen path back to `OPEN`
  from `RESOLVED`. A resolved incident's fingerprint recurring simply
  creates a new `OPEN` incident (Section 11.3) rather than reopening the
  old one — this remains the whole story for A-09's dedup scope.
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

---

## 18. Multi-Vendor Configuration Support (Day 8A)

Adds the platform's second vendor, Arista EOS, without changing the
architecture established above — this section documents what changed and,
more importantly, what deliberately did not.

**Two adapters, one registry, no vendor branch downstream.**
`CiscoAdapter` (Section 5.1) and `AristaAdapter`
(`meta_rne.adapters.arista`) are both concrete `VendorConfigAdapter`
implementations (Section 5) — independent modules, no shared code, no
import from one into the other. The production `AdapterRegistry`
(`build_production_adapter_registry`, `api/dependencies.py`) registers
both:

```python
AdapterRegistry([CiscoAdapter(), AristaAdapter()])
```

`ConfigIngestionService`, `api/routes.py`, and `api/schemas.py` are
**unchanged** — none contains a vendor-specific branch. Vendor resolution
still flows entirely through the one existing `AdapterRegistry.resolve`
call (Section 5); an unregistered vendor string still raises
`UnsupportedVendorError` → HTTP 422 `unsupported_vendor`, unchanged.

**Vendor-specific syntax terminates at the adapter boundary.** Both
adapters parse their own vendor's raw CLI text into the identical
`NormalizedConfiguration` shape (Section 6) — the same type, not a
vendor-tagged variant. `AristaAdapter` implements a narrow, explicitly
bounded EOS subset (hostname; `interface`/`description`/CIDR `ip
address`/`shutdown`/`no shutdown`/`ip access-group ... in|out`; named-only
`ip access-list` with optional-or-implicit ACL sequence numbers; `router
bgp`/`neighbor ... remote-as`), reusing every existing `ParseErrorCode`
value with no new member added — it makes no claim of complete Arista EOS
coverage. Once normalized, `domain`, `detection`, `persistence`, and the
REST API operate on the same vendor-neutral model regardless of which
adapter produced it — nothing downstream of the adapter boundary can tell
which vendor a given `NormalizedConfiguration` came from, by construction.

**`Device.vendor` remains immutable.** `SqlAlchemyDeviceRepository`'s
existing `_validate_transition` (unchanged) still rejects any attempt to
change a stored device's vendor with `DeviceConflictError` — submitting a
different vendor against an already-registered `device_id` is, and
remains, a hard conflict, never a supported "vendor migration" path. The
two demo devices (`spine-01`/Cisco, `leaf-02`/Arista) are therefore
necessarily distinct devices, never the same `device_id` under two
vendors.

**Two exact-match, device-specific policies — not one shared policy.**
`build_slice1_policies` (Section 11.2, name kept as accepted Day 4B2
technical debt) now returns two `ConfigurationPolicy` rows: the original
`policy-acl-external-in` (`applies_to="spine-01"`, `GigabitEthernet0/1`)
and a new `policy-acl-external-in-leaf-02` (`applies_to="leaf-02"`,
`Ethernet1`). Both express the same logical required-ACL requirement —
`ACL-EXTERNAL-IN` inbound, `Medium` severity — but remain two genuinely
separate rows, each seeded and retrieved independently. `PolicyEvaluator`
(Section 7) is **unchanged**: still plain `applies_to == device_id`
string equality, no wildcard, no shared-applicability mechanism. A
missing-required-ACL condition on `leaf-02` therefore produces a real
`OPEN` incident through the identical Section 9 pipeline Cisco already
used — proven equivalent only at the semantic level (violation type, ACL
name, direction, severity), never as full incident-object equality, since
`device_id`/`rule_ref`/`affected_resource`/`incident_id`/`fingerprint`
legitimately differ between the two policy rows.

**No migration, no schema, no CI change.** The `devices`/
`configuration_snapshots` vendor `CHECK` constraints already permitted
`'arista-eos'` since migration `0001` (Day 1) — confirmed, not assumed,
before this phase began. No new migration was written; no CI job was
added, removed, or modified.

## 19. Frontend Visual and Theme Architecture (Day 8B)

Restyles and reorganizes the existing frontend (Section 17.1.1/17.1.2) with
a design-token system and a user-selectable theme — no backend, API, or
domain-layer change.

**Composition, unchanged at the component-responsibility level.**
`IncidentDashboard` remains the sole owner of `useIncidents()` and page-level
layout (header, the configuration workspace, and the incident-results
section); `ConfigurationSubmissionForm` remains the single vendor-neutral
submission form; `IncidentCard` remains the presentational incident view,
receiving `isResolving`/`resolveError`/`onResolve` from `IncidentDashboard`
exactly as before. Day 8B's changes to these three files are markup/CSS
reorganization (e.g. `IncidentCard`'s identity/badges/metadata regions,
`IncidentDashboard`'s header and toolbar), not new state, props, or data
flow.

**Centralized semantic design tokens.** `frontend/src/styles.css` defines
one CSS custom-property vocabulary — surfaces, text tiers, accent, semantic
status colors, radius, shadow, spacing, and type scale — consumed by every
component's styling. No component defines its own hard-coded color; a
palette or contrast correction is made once, in `styles.css`, and applies
everywhere that token is used.

**Theme selection is a frontend-only concern.** `frontend/src/theme.ts`
exposes pure functions (`getSystemTheme`, `getStoredTheme`, `storeTheme`,
`resolveInitialTheme`) with no backend call and no new API endpoint.
`App.tsx` resolves the initial theme once, in a `useState` initializer
(`resolveInitialTheme()`), and applies it via `useLayoutEffect` by setting
`document.documentElement.dataset.theme` (`"light"` or `"dark"`) before the
browser paints — never a `prefers-color-scheme` media query, so an explicit
selection always wins regardless of the current OS setting.
`styles.css` mirrors this with two themed blocks, `:root, :root[data-theme="light"]`
and `:root[data-theme="dark"]`, each defining the same token names with
theme-appropriate values.

**Persistence and system fallback.** A user's explicit choice is persisted
in `localStorage` (`storeTheme`) and takes precedence over the OS preference
on every subsequent load (`resolveInitialTheme`'s `getStoredTheme() ??
getSystemTheme()`). No cookie, session, or server-side state is involved —
theme selection survives a reload or a new tab on the same browser profile,
never across devices or accounts, since there is no user identity in this
MVP.

---

## 20. Configuration Drift Detection — Implementation (Day 9)

Implements FR-04, AC-05, AC-06, backend-only (no frontend consumption; see
Section 19's own scope for the last frontend-facing change). The request
flow, end to end:

1. `GET /devices/{device_id}/drift` (`api/routes.py`, `operation_id =
   "get_device_drift"`) receives the path parameter `device_id`.
2. `GetDeviceDriftService.get_drift(device_id)`
   (`application/device_drift.py`) opens one `UnitOfWork` and loads the
   `Device` via `uow.devices.get_by_id(device_id)`. A missing device
   raises `DeviceNotFoundError` (`application/errors.py`), mapped by
   `api/errors.py` to HTTP 404 with the direct `{"code":
   "device_not_found", "detail": str(exc)}` body (`str(exc)` is
   `"device not found: '<device_id>'"` — the same not-found convention
   `IncidentNotFoundError` already uses, one level down in casing only:
   `device_not_found` is lowercase, matching every other error code in
   this API).
3. It follows the persisted `Device.baseline_snapshot_id` and
   `Device.current_snapshot_id` — never a repository iteration order,
   never a "most recent timestamp" query — and loads both
   `ConfigurationSnapshot`s via the existing
   `ConfigurationSnapshotRepository.get_by_id`. No new repository method
   or migration was required for this gate; both ports already existed.
4. It compares `baseline_snapshot.normalized_config` against
   `current_snapshot.normalized_config` through `DriftDetector.compare`
   (Section 8) exactly once, and returns the resulting `DriftReport`
   unchanged — no reclassification, no reordering, no incident produced.
5. `DriftReportResponse.from_domain`/`DriftEntryResponse.from_domain`
   (`api/schemas.py`) map the domain `DriftReport`/`DriftEntry` tuples
   directly to a JSON body (no envelope, matching every other success
   response in this API): `{"added": [...], "removed": [...], "changed":
   [...]}`, each entry `{"resource": string, "field": string | null,
   "old_value": string | null, "new_value": string | null}`.
6. **No data is written.** `GetDeviceDriftService` never calls
   `uow.commit()`, `uow.devices.save()`, or
   `uow.configuration_snapshots.add()` — proven directly by spy-repository
   tests. On a device with exactly one submission,
   `baseline_snapshot_id == current_snapshot_id`, so both `get_by_id`
   calls resolve the same snapshot and the diff is empty by construction
   (AC-06). A later submission is always compared against the original,
   fixed baseline (never "the previous submission") — there is no
   baseline-redesignation API, and none is planned for this gate.
7. **No incident is created.** Drift detection and incident emission are
   deliberately decoupled in this gate — see Section 8's closing note and
   Section 17.1. `IncidentSource.DRIFT` does not exist.

**Corrupted-reference behavior.** A `Device` whose `baseline_snapshot_id`
or `current_snapshot_id` points at a `ConfigurationSnapshot` that does not
exist is an internal invariant violation — this can only happen through
direct persistence tampering, never through the public API's own write
path (`DeviceRepository.save` validates every non-null snapshot reference
before allowing it). This case raises a plain `RuntimeError`, which has no
registered exception handler and therefore falls through to FastAPI/
Starlette's own generic response: HTTP 500, `Content-Type: text/plain;
charset=utf-8`, body `Internal Server Error` — never this API's
`ApiErrorResponse` JSON schema, never a 404, and never a leaked traceback.

**Comparison scope, exactly as implemented** (see Section 8 and
`domain-model.md` Section 20 for the full contract):

| Collection | Keyed by | Changed scalar fields | Whole-resource add/remove |
|---|---|---|---|
| `interfaces` | `name` | `description`, `ip_address`, `mtu`, `admin_state`, `acl_in`, `acl_out` | Yes |
| `routing.bgp_neighbors` | `neighbor_ip` | `remote_as` | Yes |
| `acls` | `name` | — (`entries` not compared) | Yes |

**Explicitly out of scope for this gate:** `hostname`-only drift;
`static_routes` (the normalized static-route model itself remains
deferred, Section 5); ACL-entry/rule-level diffing; drift severity
assignment and recommendations; drift-triggered incident creation;
drift acknowledgment or remediation; frontend rendering of drift data;
telemetry ingestion and anomaly detection (FR-05/FR-06, unrelated to this
gate).

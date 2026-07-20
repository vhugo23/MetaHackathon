# Product Specification — Meta RNE Platform

**Status:** Draft — Day 1 consistency correction
**Date:** 2026-07-18
**Phase:** Planning / Architecture

---

## 1. Product Overview

The Meta RNE Platform is a **working data-center network-operations prototype, inspired by hyperscale requirements**. It ingests device configurations from multiple vendors, normalizes them into a canonical model, evaluates them against required-configuration policies, detects drift against a stored baseline, ingests or simulates operational telemetry, applies deterministic rules to detect anomalies, and generates structured, deduplicated incidents with evidence and remediation recommendations. A read-only React operator dashboard surfaces this state through the platform's REST API.

**The MVP does not operate at hyperscale.** It runs as a single modular-monolith service against a single PostgreSQL database, on a laptop or a small Docker Compose stack. It demonstrates the patterns that hyperscale operations require — vendor-neutral normalization, policy-driven validation, drift detection, deterministic anomaly detection, deduplicated incidents — at prototype scale, not the scale itself.

It addresses three problems shared by data-center network operators generally: fragmented multi-vendor configuration management, reactive/manual anomaly detection, and slow, undirected incident investigation.

---

## 2. Target Users

| Role | Primary Need |
|---|---|
| Network Operations Engineer (NOC) | Detect and triage anomalies across all devices without switching between vendor consoles |
| Site Reliability Engineer (SRE) | Investigate incidents quickly using structured evidence and actionable recommendations |
| Data Center Operator | Monitor device health KPIs (CPU, memory, link state) at a glance |
| Network Administrator | Audit configuration state and catch drift or policy violations before they cause outages |

---

## 3. User Problems

**P-01 — Multi-vendor configuration complexity.** Devices from different vendors each require vendor-specific configuration syntax. Operators have no single interface to reason about fleet-wide configuration state.

**P-02 — Required configuration is not enforced.** Nothing checks that a device's configuration satisfies baseline security/operational requirements (e.g., a required ACL) independent of whether the config changed.

**P-03 — Configuration drift goes undetected.** Devices diverge from their baseline through manual edits, failed rollouts, or hardware replacements, and drift accumulates silently.

**P-04 — Anomaly detection is reactive and fragmented.** Each vendor's tooling emits alerts independently; operators correlate signals manually.

**P-05 — Telemetry coverage is inconsistent.** Hardware, software, and configuration state live in separate systems.

**P-06 — Incident investigation is undirected, and repeat findings flood the queue.** Engineers manually parse logs and correlate state, and the same unresolved condition can be reported as a new incident every time it's re-detected, obscuring what's actually still open.

---

## 4. Functional Requirements

### FR-01 — Configuration Ingestion
Accept device configuration as raw text (vendor CLI format) via a REST endpoint, associated with a device ID and a `vendor` field. **`vendor` is validated in two stages, not one:** the HTTP schema layer only checks that it is present and a non-empty string (any other shape is a schema failure, not a vendor failure); whether that string names a *supported* vendor is a separate, later check against `AdapterRegistry` (FR-02). See NFR-05 for the resulting status-code split.

### FR-02 — Configuration Normalization
Parse vendor-specific configuration text into a canonical `NormalizedConfiguration` model. Each vendor's parsing logic is an isolated adapter. The canonical model represents at least: hostname, interfaces (name, IP, MTU, admin-state, inbound/outbound ACL assignment), routing (static routes, BGP neighbors), and ACLs (name, entries).

**MVP vendors:** Cisco IOS-XE (first vertical slice), Arista EOS (later slice).

### FR-03 — Configuration Policy Evaluation
Evaluate a device's `NormalizedConfiguration` against every applicable `ConfigurationPolicy` and produce a `ConfigurationViolation` for each unsatisfied rule. The MVP's rule type is a required ACL assignment: a named ACL must exist and be assigned to a specific interface in a specific direction. Policies are seeded at startup (fixture data); there is no policy-authoring endpoint in the MVP. A device with no applicable policy produces zero violations.

### FR-04 — Configuration Drift Detection
Compare a device's current normalized configuration against its **baseline** — the first successfully ingested configuration for that device, fixed at ingestion time and never silently replaced by later submissions — and report added, removed, and changed fields as a structured diff. A device with only one submission has current == baseline, so it produces zero drift changes.

### FR-05 — Telemetry Ingestion and Simulation
Accept telemetry samples for a device (CPU %, memory %, interface error rate, per-interface link state, per-neighbor BGP session state) via a REST endpoint. A telemetry simulator generates realistic streams from a fixture in the absence of live device polling. The platform retains a bounded recent history per device (not only the latest sample), so window-based rules can evaluate a device's recent trajectory.

### FR-06 — Deterministic Anomaly Detection
Evaluate telemetry against deterministic, individually-registered rules, each returning a structured finding or none. MVP rules:

| Rule ID | Trigger |
|---|---|
| RULE-CPU-HIGH | CPU utilization > 90% for 2 consecutive samples |
| RULE-LINK-FLAP | An interface records more than 3 state transitions within a 60-second window (i.e., ≥ 4 transitions) |
| RULE-BGP-DOWN | A BGP neighbor session transitions from a non-down state (e.g., Established, Connect, OpenSent, OpenConfirm) to Idle or Active |

### FR-07 — Incident Generation and Deduplication
When a policy violation, drift finding, or anomaly fires, create or update an `Incident`: unique ID, timestamps, severity (Critical/High/Medium/Low), device ID, `rule_ref` (the source policy/rule reference), structured evidence, and a human-readable recommendation.

**Deduplication is mandatory, not optional.** Each finding has a deterministic **fingerprint** derived from `(device_id, source, rule_ref, affected_resource)`. If an `OPEN` incident with the same fingerprint already exists, the finding updates that incident's `last_seen_at` and `occurrence_count` instead of creating a new one. The same unresolved finding never produces two `OPEN` incidents.

### FR-08 — REST Query API
Expose REST endpoints so any authorized caller — including the operator dashboard (FR-10) — can query: registered devices and their current normalized configuration; incidents, filterable by device and severity; a device's drift report; a device's recent telemetry. **These endpoints are a query API, not the dashboard itself** — FR-10 is a distinct consumer of this API, not a synonym for it.

### FR-09 — Structured Alert Emission
When an incident is created or its occurrence is updated, emit a structured JSON log record to stdout. This is the MVP's alert channel; no external integration is required.

### FR-10 — Operator Dashboard (React)
The final MVP includes a read-only React + TypeScript single-page application that consumes the REST Query API (FR-08) to display devices, incidents, drift, and telemetry. It introduces no backend logic of its own. **Implementation is scoped to a later vertical slice; the first vertical slice does not require it** (see Section 11).

---

## 5. Non-Functional Requirements

**NFR-01 — Vendor Isolation.** Each vendor parser is a distinct adapter behind a shared interface. Adding a vendor must not require modifying domain logic.

**NFR-02 — Framework Independence.** Domain models, policy/drift/anomaly detection logic, and incident deduplication must not import FastAPI, SQLAlchemy, or any other framework/driver. These layers are testable with no server and no database running.

**NFR-03 — Deterministic Detection.** All detection (policy, drift, anomaly) is deterministic. No probabilistic or ML-based detection in the MVP.

**NFR-04 — Testability.** Every FR has at least one automated named test (see [test-strategy.md](./test-strategy.md)). The project builds cleanly and all tests pass before a task is complete.

**NFR-05 — REST API Consistency.** All external operations are HTTP/JSON.
**A successful response body is the resource itself — no `{"data": ...,
"error": null}` envelope** (Day 5B binding correction, `api/schemas.py`):
`POST /devices/{device_id}/config` returns `SubmitConfigurationResponse`
directly; `GET /incidents` returns `list[IncidentResponse]` directly, a
bare JSON array. `GET /health` is unaffected and remains exactly
`{"status": "ok"}`. A failed response is a bare
`ApiErrorResponse`, also unwrapped:

```json
{"code": "<stable_code>", "detail": "<public_detail>"}
```

`code` is a lowercase, stable, snake_case string; the message field is
named `detail`, not `message`. At minimum, these categories are
distinguished by status code and `code`:

| Category | HTTP Status | `code` |
|---|---|---|
| Malformed request schema (FastAPI/Pydantic `RequestValidationError`) | 422 | FastAPI's own default body — no custom `ApiErrorResponse` |
| Unsupported vendor (`UnsupportedVendorError`) | 422 | `unsupported_vendor` |
| Configuration parse failure (`ConfigurationParseError`) | 422 | `configuration_parse_error` |
| Device vendor/timestamp conflict (`DeviceConflictError`) | 409 | `device_conflict` |
| Duplicate snapshot (`SnapshotAlreadyExistsError`) | 409 | `snapshot_already_exists` |
| Referenced device missing (`ReferencedDeviceNotFoundError`) | 409 | `referenced_device_not_found` |
| Other caller/application `ValueError` | 422 | `invalid_request` |
| Resource not found | 404 | `NOT_FOUND` — **not in Slice 1**, no single-resource `GET` endpoint exists yet |
| Persistence failure (`PersistenceError`) | 500 | `persistence_error` (generic public detail — no SQL, constraint name, or stack trace) |
| Serialization failure (`SerializationError`) | 500 | `serialization_error` (generic public detail) |
| Unexpected/unmapped exception | 500 | normal production 500 behavior — no custom envelope, no leaked exception representation |

`POST` endpoints that create a resource return `201`; `GET` endpoints return `200`.

**The request-validation/`unsupported_vendor` split is deliberate, not
redundant** (FR-01): `vendor` is a plain non-empty string at the Pydantic
schema layer, never a `Literal`/enum of known vendors — a missing field,
wrong JSON type, or blank/whitespace-only value fails FastAPI's own
request-validation path (422), decided before any domain code runs; a
well-formed string that names no registered adapter (e.g.,
`"juniper-junos"`) is `unsupported_vendor` (422, not 400), decided by
`AdapterRegistry.resolve`. An internal `VendorType` enum exists, but only
for vendors that have already resolved successfully — it is never the type
of the raw request field.

**NFR-06 — Persistent Storage.** The final MVP persists state in **PostgreSQL** via SQLAlchemy repositories. In-memory implementations of the same repository interfaces are permitted **only** as fast test doubles for unit/integration tests (see [test-strategy.md](./test-strategy.md) Section 9); they are not a production persistence option. End-to-end tests run against a real API + PostgreSQL container pair.

**NFR-07 — Modular Monolith.** One deployable backend process, internally partitioned into modules (`api`, `application`, `domain`, `detection`, vendor adapters, `persistence`, `observability`) with dependencies enforced by import direction, not network calls. No microservices, no message broker.

---

## 6. MVP Scope

### Final MVP (all capabilities, across all vertical slices)

- Vendor configuration ingestion and normalization (Cisco, Arista)
- Required-configuration policy evaluation
- Configuration drift detection
- Telemetry ingestion and simulation
- Deterministic anomaly detection (3 rules)
- Incident creation and deduplication
- REST API
- Read-only React operator dashboard

### Technology stack (binding — see [ADR-0002](./adr/0002-technology-stack-and-persistence.md))

Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, React, TypeScript, Vite, pytest, Vitest, Playwright, Docker Compose, GitHub Actions. Modular monolith (see [ADR-0001](./adr/0001-modular-monolith.md)).

### First vertical slice (see Section 11)

Cisco only, single configuration submission, policy evaluation only — **excludes telemetry, drift detection, Arista, and the React dashboard.**

---

## 7. Explicit Non-Goals

- **Repair Support System** — automated remediation is out of scope for any MVP milestone.
- **Live device configuration push** — NETCONF, RESTCONF, gRPC/gNMI, or SSH-based deployment to real devices.
- **Machine learning or AI-based detection** — all detection is deterministic.
- **External alert integrations** — PagerDuty, Slack, email, webhooks.
- **Authentication and authorization** — no login, RBAC, or API keys.
- **Multi-tenancy** — single-tenant.
- **High-availability deployment** — no clustering or replication; a single PostgreSQL instance with no HA topology.
- **Third+ vendor adapters** — Juniper, Nokia, Huawei, etc.
- **Configuration push or rollback** — read + analyze only.
- **Kubernetes** — Docker Compose only.

---

## 8. Assumptions

> Items marked **[ASSUMPTION]** are decisions made in the absence of an explicit external requirement.

**A-01** — Device configurations are submitted as vendor CLI text; JSON-format configs are not required.

**A-02** — A device's baseline is fixed at its first successful configuration submission and is not silently replaced by later submissions. Re-designating a baseline explicitly (other than "the first one") is a post-MVP capability (Section 12 of [architecture.md](./architecture.md)).

**A-03** — Telemetry is delivered by a caller or the simulator; the platform does not poll live devices.

**A-04** — Device identifiers are arbitrary caller-supplied strings (e.g., hostname); no discovery or inventory sync.

**A-05** — PostgreSQL is required for the final MVP (NFR-06). In-memory storage is acceptable **only** inside the test suite.

**A-06** — The backend runs as a single process/container; horizontal scaling is not required. PostgreSQL runs as its own container, but this does not make the backend itself multi-instance.

**A-07** — Cisco IOS-XE and Arista EOS syntax is represented by a representative subset (interfaces, BGP, static routes, ACLs).

**A-08** — `ConfigurationPolicy` records are seeded fixture data for the MVP; there is no runtime authoring UI or endpoint.

**A-09** — Incident deduplication fingerprints are scoped to `OPEN` incidents only: if a fingerprint's incident has been resolved, a subsequent matching finding creates a new incident rather than reopening the old one. (There is no resolution workflow in the MVP — see [domain-model.md](./domain-model.md) — so this case does not arise in practice yet, but the rule is stated so it is unambiguous once resolution exists.)

---

## 9. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | Real vendor config syntax varies from the assumed subset | Medium | High | Minimal canonical model; one concrete fixture per vendor in tests |
| R-02 | Simulated telemetry doesn't reproduce real anomaly patterns | Low | Medium | Parameterized simulator, edge-case injection per rule |
| R-03 | False-positive incidents erode trust | Medium | High | Conservative thresholds; ≥ 2 consecutive samples for threshold rules; deduplication prevents queue flooding |
| R-04 | Hackathon time constraint limits depth | High | Medium | Vertical-slice completeness over breadth; Arista and telemetry deprioritized past slice 1 |
| R-05 | Shared PostgreSQL state makes tests order-dependent | Low | Medium | Fresh schema/transaction rollback per test; in-memory doubles for unit/integration; real Postgres only at E2E |

---

## 10. Measurable Acceptance Criteria

**AC-01** — Given a valid Cisco IOS-XE configuration, `POST /devices/{id}/config` returns `201` with a response body whose `normalized_config` includes hostname, at least one interface, and at least one BGP neighbor (if present in input) — see Section 11 for the full response shape. Because normalization is deterministic (domain-model.md invariant 10), submitting the same configuration text at two different times produces byte-for-byte identical `normalized_config` values.

**AC-02** — Given a valid Arista EOS configuration, `POST /devices/{id}/config` returns `201` and produces a `normalized_config` with the same fields as AC-01. *(Later slice.)*

**AC-03** — Given a `NormalizedConfiguration` that satisfies every `RequiredAclRule` of its applicable `ConfigurationPolicy`, `PolicyEvaluator` produces zero `ConfigurationViolation`s and no incident is created; the POST response reports `violations_detected: 0, incidents_created: 0, incidents_updated: 0`.

**AC-04** — Given a `ConfigurationPolicy` requiring `ACL-EXTERNAL-IN` inbound on `GigabitEthernet0/1` for `spine-01`, and a Cisco configuration for `spine-01` that does not assign that ACL, `POST /devices/spine-01/config` returns `201` with `violations_detected: 1, incidents_created: 1, incidents_updated: 0`, and `GET /incidents` subsequently returns exactly one `Incident` with `severity = Medium`, `source = POLICY_VIOLATION`, and `rule_ref` equal to the policy's ID.

**AC-05** — Given a device whose baseline configuration contains an ACL and a later submission that removes it, `GET /devices/{id}/drift` returns a diff with at least one `removed` entry referencing that ACL. *(Later slice.)*

**AC-06** — Given a device with exactly one configuration submission, `GET /devices/{id}/drift` returns an empty diff with zero changes (current == baseline). *(Later slice.)*

**AC-07** — Given telemetry with CPU > 90% on 2 consecutive samples, `GET /incidents` returns an incident with `rule_ref = "RULE-CPU-HIGH"` and populated device ID, severity, and evidence. *(Later slice.)*

**AC-08** — Given a telemetry sequence with at least 4 interface state transitions within 60 seconds (e.g., up→down→up→down→up), `GET /incidents` returns an incident with `rule_ref = "RULE-LINK-FLAP"` referencing that interface. *(Later slice.)*

**AC-09** — Given a BGP neighbor transitioning from a non-down state to Idle or Active, `GET /incidents` returns an incident with `rule_ref = "RULE-BGP-DOWN"` and evidence containing `neighbor_ip`, `state`, and `previous_state`. *(Later slice.)*

**AC-10** — When an incident is created or updated, a JSON log line is written to stdout containing `incident_id`, `device_id`, `rule_ref`, `severity`, `status`, `outcome` (`CREATED` or `UPDATED`, distinguishing which case occurred), and `timestamp`.

**AC-11** — Given a finding whose fingerprint (a SHA-256 digest, domain-model.md Section 11 — not a delimiter-joined string) matches an existing `OPEN` incident, repeated detection updates that incident's `last_seen_at` and `occurrence_count` rather than creating a second `OPEN` incident with the same fingerprint. This holds even under concurrent `upsert_open_incident` calls (enforced by a PostgreSQL partial unique index, not application logic alone) — that guarantee is at the repository level; it is not itself a claim that Slice 1 has an integration test of two full concurrent HTTP requests. The second `POST /devices/spine-01/config` submission of the identical config reports `data.violations_detected: 1, data.incidents_created: 0, data.incidents_updated: 1`, and `GET /incidents` still returns exactly one incident, now with `occurrence_count: 2`.

**AC-12** — Each of the mapped error categories in NFR-05 returns its documented HTTP status and the direct `{"code", "detail"}` error body; no unhandled exception reaches the caller as a raw stack trace.

**AC-13** — `make test` (or equivalent) runs all unit, integration, and contract tests and exits `0`. End-to-end tests run separately against a real API + PostgreSQL container pair.

---

## 11. First End-to-End Vertical Slice

**Goal:** Prove the policy-evaluation pipeline end-to-end for one vendor, one config submission, one violation, one incident, and one query — before drift, telemetry, Arista, or the dashboard are built.

**Primary successful demonstration is one configuration submission** (steps 1–8 below run exactly once). Deduplication (AC-11) is still implemented and tested within Slice 1 — its test issues a **second** submission of the identical config solely to prove a repeat finding updates rather than duplicates the incident. That one additional test does not change the primary demonstration: the slice's headline path is still "submit once, get one incident."

**Slice 1 requires exactly two HTTP endpoints:**

- `POST /devices/{id}/config`
- `GET /incidents`

`GET /devices`, `GET /devices/{id}`, and `GET /incidents/{id}` are **not** required for Slice 1 and are deferred (architecture.md Section 10) — the POST response itself (below) carries the normalized configuration, so no follow-up `GET /devices/{id}` is needed to verify normalization, and a single incident list is sufficient without a single-incident lookup.

**Sequence (binding):**

1. A required-ACL policy is seeded for device `spine-01`, requiring `ACL-EXTERNAL-IN` inbound on `GigabitEthernet0/1`.
2. A Cisco IOS-XE configuration is submitted: `POST /devices/spine-01/config`.
3. `CiscoAdapter.parse(text)` produces a `NormalizedConfiguration`. *(FR-01, FR-02)*
4. The submitted configuration does not contain or assign the required ACL.
5. `PolicyEvaluator` creates exactly one `ConfigurationViolation`, carrying the triggering snapshot's ID. *(FR-03)*
6. `IncidentFactory` builds exactly one incident candidate, `severity = Medium`; the repository's atomic upsert finds no existing `OPEN` incident with this fingerprint and inserts a new one. *(FR-07)*
7. Once the enclosing database transaction commits, the incident is emitted as a structured JSON log line to stdout. *(FR-07, FR-09)*
8. The `POST` response itself (`201`) already reports the outcome; `GET /incidents` additionally returns the incident on any later query. *(FR-08)*

```
POST /devices/spine-01/config          ← Cisco IOS-XE config, no ACL-EXTERNAL-IN assigned
    │  FR-01 ingestion
    ▼
CiscoAdapter.parse(text) → NormalizedConfiguration     FR-02
    │  stored on the ConfigurationSnapshot; this is spine-01's first
    │  submission, so it becomes both current and baseline
    ▼
PolicyEvaluator.evaluate(device_id, source_snapshot_id, observed_at, config, policies)  FR-03
    -> list[ConfigurationViolation]
    │  one violation: MISSING_REQUIRED_ACL, ACL-EXTERNAL-IN, GigabitEthernet0/1, in
    ▼
IncidentFactory.build_candidate(violation) → IncidentCandidate     FR-07
    │  severity=Medium, source=POLICY_VIOLATION, rule_ref=<policy_id>
    ▼
uow.incidents.upsert_open_incident(candidate, fingerprint, observed_at)  FR-07
    -> IncidentUpsertResult { incident, outcome: "CREATED" | "UPDATED" }
    │  atomic (PostgreSQL partial unique index on (fingerprint) WHERE status='OPEN')
    │  no existing OPEN incident with this fingerprint → outcome = CREATED
    ▼
uow.commit() → stdout JSON log (includes outcome)       FR-07, FR-09
    ▼
201 response (the resource itself, no envelope): { device_id, snapshot_id,
    normalized_config, violations_detected: 1, incidents_created: 1,
    incidents_updated: 0 }                                             FR-08
    │  counts computed from IncidentUpsertResult.outcome, never inferred
    ▼
GET /incidents  →  [the incident, with full evidence + recommendation] FR-08
```

**`POST /devices/{id}/config` success response (binding, `201`, the
resource itself — no envelope):**

```json
{
  "device_id": "spine-01",
  "snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "normalized_config": { "hostname": "spine-01", "interfaces": [ /* ... */ ], "routing": { "bgp_neighbors": [] }, "acls": [] },
  "violations_detected": 1,
  "incidents_created": 1,
  "incidents_updated": 0
}
```

`routing` has no `static_routes` key — the current normalized domain
model (`NormalizedRouting`) carries no such field yet.

`violations_detected` is the count of `ConfigurationViolation`s produced by this submission; `incidents_created` and `incidents_updated` split that count by whether each violation's fingerprint matched an existing `OPEN` incident (Section 10's dedup rule) — `incidents_created + incidents_updated == violations_detected` always holds for the policy path.

**Explicitly excluded from this slice:** telemetry (FR-05), anomaly detection (FR-06), drift detection (FR-04 — the **next** vertical slice), the Arista adapter, and the React dashboard (FR-10).

**Definition of done:** AC-01, AC-03, AC-04, AC-10, AC-11, AC-12 (for this slice's error paths), AC-13 pass. `GET /incidents` returns the incident with all required fields. No application code exists that is not covered by at least one named test (see [test-strategy.md](./test-strategy.md) Section 19).

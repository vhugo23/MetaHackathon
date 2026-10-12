# Frontend API Contract — Meta RNE Platform

**Status:** Day 6A stabilized this contract; Day 6B built the first
frontend consumer (`frontend/`) against it, exercising `GET /incidents`
only; Day 6C added the second frontend consumer, exercising
`POST /devices/{device_id}/config` — see README.md's "Current Day 6C
scope". Day 7A added a new endpoint, `POST /incidents/{incident_id}/resolve`
(Section 7), and two new `IncidentResponse` fields, `updated_at`/
`resolved_at` (Section 6), as a backend-only addition with no frontend
consumer yet. **Day 7B added the third frontend consumer: the dashboard now
calls `POST /incidents/{incident_id}/resolve` for every `OPEN` incident's
"Resolve incident" control** — see README.md's "Current Day 7B scope". The
"Frontend consumption notes" subsections added to Sections 5–7 below
describe how the *current* frontend uses this contract, including Day 7B's
resolution flow. **Day 9 added a new endpoint, `GET
/devices/{device_id}/drift` (Section 8), as a backend-only addition with
no frontend consumer yet** — same pattern as Day 7A's backend-only
addition of the resolve endpoint. **Day 9b added two more endpoints,
`POST /devices/{device_id}/telemetry` and `GET
/devices/{device_id}/telemetry/recent` (Section 9), also backend-only with
no frontend consumer yet.** **Day 9c added no new endpoint — `GET
/incidents` (Section 6) now also returns `"ANOMALY"`-sourced incidents,
still with no frontend consumer.** **Day 11B added the frontend's fourth
and fifth consumers: `GET /devices/{device_id}/telemetry/recent` (Section
9) and `"ANOMALY"`-sourced incidents from `GET /incidents` (Section 6),
both rendered in a new, read-only `Device telemetry` workspace on the
existing dashboard — see README.md's "Frontend Telemetry Workspace"
section. `POST /devices/{device_id}/telemetry` remains backend-only; the
frontend never submits telemetry.** **Day 12 added two read-only
backend endpoints, `GET /devices` and `GET /devices/{device_id}` (Section
8A), with no frontend consumer yet.**
**Derived from:** `backend/src/meta_rne/api/schemas.py`, `api/routes.py`,
`api/errors.py`, `api/cors.py` (current source, not a planning aspiration)

This document is the frontend-facing contract for the first vertical
slice's two business endpoints plus `/health`. It exists so a future React
session can build against a fixed, current contract instead of re-deriving
it from backend source or from stale planning docs. It does not make any
frontend implementation decisions (no component structure, no state
management, no routing) — those remain out of scope until the React app is
actually scaffolded. (Day 6C's implementation decisions — the submission
hook, form component, and their tests — live in the frontend source and
`docs/architecture.md` Section 17.1.1, not here.)

---

## 1. Base URL and CORS

The base URL is whatever the API is deployed at — no path prefix (e.g.
`/api`) exists. Locally via Docker Compose:

```
http://localhost:8080
```

(or whatever `META_RNE_API_HOST_PORT` was overridden to — see
`docker-compose.yml`).

**CORS is disabled by default.** It is enabled only when the server process
has a non-empty `META_RNE_CORS_ALLOWED_ORIGINS` environment variable
(comma-separated origin list, whitespace around each entry trimmed, empty
entries ignored). `docker-compose.yml`'s local development default is:

```
META_RNE_CORS_ALLOWED_ORIGINS=http://localhost:5173
```

— the Vite dev server's default origin, so a locally-run frontend dev
server can call this API once it exists. When enabled: `allow_methods` is
exactly `GET, POST, OPTIONS`; `allow_headers` is exactly `Content-Type`;
`allow_credentials` is `false`; **no wildcard origin is ever used** — a
frontend origin not in the configured list receives no
`Access-Control-Allow-Origin` header at all (the browser blocks the
response, not the server).

## 2. OpenAPI document

The full machine-readable schema is served at `GET /openapi.json` (and an
interactive form at `GET /docs`) by the running API — always the
authoritative, current source; this document is a human-readable
companion, not a replacement. Stable `operationId`s (safe to key a
generated client off of):

| Endpoint | `operationId` |
|---|---|
| `GET /health` | `health_check` |
| `POST /devices/{device_id}/config` | `submit_device_configuration` |
| `GET /incidents` | `list_incidents` |
| `POST /incidents/{incident_id}/resolve` | `resolve_incident` |
| `GET /devices/{device_id}/drift` | `get_device_drift` |
| `GET /devices` | `list_devices` |
| `GET /devices/{device_id}` | `get_device` |

## 3. Identifiers, timestamps, and enums

- **All IDs are opaque strings** — `device_id` (caller-supplied path
  segment), `snapshot_id`, `incident_id`, `fingerprint`, `policy_id`
  internally. No UUID-shape validation, no format guarantee beyond
  non-empty. Do not parse or derive meaning from their contents.
- **All timestamps are ISO-8601 UTC**, e.g. `"2026-07-18T10:00:00Z"`
  (`created_at`, `last_seen_at`).
- **Enum values are current, not exhaustive of the final MVP** — additional
  values may be added in later phases (e.g. more `ViolationType`s, more
  `IncidentSource`s as drift/telemetry ship). Treat unknown values
  defensively.

## 4. `GET /health`

Liveness only — does not query PostgreSQL.

```
GET /health
```

```json
// 200 OK
{"status": "ok"}
```

## 5. `POST /devices/{device_id}/config`

Ingests one vendor configuration submission. `device_id` (path) is
authoritative — never read from the request body.

**Request** (`Content-Type: application/json`, unknown fields rejected):

```json
{
  "vendor": "cisco-ios-xe",
  "raw_config_text": "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"
}
```

| Field | Type | Notes |
|---|---|---|
| `vendor` | `string` | Non-empty, non-whitespace-only. As of Day 8A, both `"cisco-ios-xe"` and `"arista-eos"` resolve to a registered adapter; any other value (well-formed or not) still returns the existing `unsupported_vendor` 422 contract (Section 9) — a third vendor is not a frontend concern to guard against, since the backend already rejects it deterministically. |
| `raw_config_text` | `string` | Non-empty. The literal device configuration text. |

`device_id` and `observed_at` in the body are **rejected** (422) —
`device_id` comes from the path, `observed_at` is always server-generated.

**Success response** — `201 Created`, the resource itself, no envelope:

```json
{
  "device_id": "spine-01",
  "snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "normalized_config": {
    "hostname": "spine-01",
    "interfaces": [
      {
        "name": "GigabitEthernet0/1",
        "description": null,
        "ip_address": null,
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

`normalized_config.routing` has no `static_routes` key — not implemented
yet. `incidents_created + incidents_updated` always equals
`violations_detected`.

**Frontend consumption notes (Day 6C).** `submitDeviceConfiguration`
(`src/api/configurations.ts`) constructs the request body itself — always
exactly `{vendor, raw_config_text}`, built fresh from the two known fields
rather than forwarding a caller-supplied object directly, so a stray
`device_id`/`observed_at`/other property on the caller's value can never
leak into the serialized body. `device_id` is taken from the path
parameter, URL-encoded via `encodeURIComponent` as one opaque segment, and
otherwise passed through completely unchanged (never trimmed, never parsed
for meaning). The complete `201` response — including every field of
`normalized_config` (`interfaces[]`, `routing.bgp_neighbors[]`,
`acls[].entries[]`) — is validated by `isConfigurationSubmissionResponse`
and its nested per-field runtime guards before the frontend trusts it;
`admin_state`, `action`, and `protocol` are checked as non-empty strings
only (not closed enums), matching this document's Section 3 guidance to
treat enum-like values defensively. A structurally malformed `2xx` body is
rejected into the same controlled error path a network failure would use —
never partially rendered.

**Frontend consumption notes (Day 8A).** `ConfigurationSubmissionRequest.
vendor` (`src/api/types.ts`) is now `SupportedVendor = "cisco-ios-xe" |
"arista-eos"` — the frontend supports exactly the two currently
registered production vendors, never an arbitrary string, and no third
value was widened in. The configuration-submission form's vendor
`<select>` renders both options (`cisco-ios-xe`/"Cisco IOS-XE",
`arista-eos`/"Arista EOS"), with Cisco IOS-XE as the default/initial
selection; a real component-state vendor-change handler replaces the
previous no-op. Both the selected vendor and the entered raw
configuration text are forwarded to `submitDeviceConfiguration` unchanged
— `src/api/configurations.ts` and `src/hooks/useConfigurationSubmission.ts`
required no code change, since both were already generic over
`ConfigurationSubmissionRequest`. No vendor-specific client-side syntax
validator exists — a malformed-but-syntactically-plausible EOS or IOS-XE
submission is still validated only by the backend adapter, surfaced
through the existing `configuration_parse_error` contract (Section 9)
unchanged.

## 6. `GET /incidents`

Returns every incident, no filtering/pagination/sorting query parameters
yet — always the full list.

```
GET /incidents
```

**Success response** — `200 OK`, a bare JSON array:

```json
[
  {
    "incident_id": "8f14e45f-ceea-4c1d-8f1e-1234567890ab",
    "fingerprint": "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
    "device_id": "spine-01",
    "source": "POLICY_VIOLATION",
    "rule_ref": "policy-acl-external-in",
    "affected_resource": "acl:ACL-EXTERNAL-IN:GigabitEthernet0/1:in",
    "severity": "Medium",
    "status": "OPEN",
    "evidence": {
      "source_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "violation_type": "MISSING_REQUIRED_ACL",
      "expected_acl_name": "ACL-EXTERNAL-IN",
      "actual_acl_name": null,
      "interface_name": "GigabitEthernet0/1",
      "direction": "in"
    },
    "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
    "created_at": "2026-07-18T10:00:00Z",
    "last_seen_at": "2026-07-18T10:00:00Z",
    "occurrence_count": 1,
    "updated_at": "2026-07-18T10:00:00Z",
    "resolved_at": null
  }
]
```

`fingerprint` is present and treated as a normal, non-internal field.
`evidence`'s exact shape depends on `source` (Day 9c): for
`"POLICY_VIOLATION"` it is the shape shown above; for `"ANOMALY"` it is one
of three shapes, selected by `rule_ref`:

```json
// rule_ref: "RULE-CPU-HIGH"
{"samples": [{"timestamp": "2026-07-18T10:00:00Z", "cpu_utilization_pct": 95.0}, "..."]}

// rule_ref: "RULE-LINK-FLAP"
{"interface_name": "GigabitEthernet0/1", "transitions": [{"timestamp": "2026-07-18T10:00:00Z", "oper_state": "down"}, "..."]}

// rule_ref: "RULE-BGP-DOWN"
{"neighbor_ip": "10.0.0.1", "previous_state": "Established", "state": "Idle"}
// previous_state is null when the neighbor was first observed already degraded
```

An `"ANOMALY"` incident's `severity` is always `"High"`
(`RULE-CPU-HIGH`/`RULE-LINK-FLAP`) or `"Critical"` (`RULE-BGP-DOWN`), and
`affected_resource` is `"device"`, `"interface:{interface_name}"`, or
`"bgp-neighbor:{neighbor_ip}"` respectively — fixed per rule, never
computed per-instance.

**Frontend consumption notes (Day 11B).** The telemetry workspace
(`TelemetryPanel.tsx`) consumes this same, unfiltered `GET /incidents`
response — no server-side filtering exists — and filters it client-side
(`filterAnomalyIncidentsForDevice`, `src/api/incidents.ts`) to exactly the
incidents matching the selected `device_id` and `source === "ANOMALY"`;
both `OPEN` and `RESOLVED` matches render. The existing global incident
list (`IncidentCard.tsx`) is unchanged and still renders only the
`"POLICY_VIOLATION"` evidence shape; the workspace does not duplicate its
"Resolve incident" control, and resolution remains available only there.
Evidence rendering for all three `"ANOMALY"` shapes
(`RULE-CPU-HIGH`/`RULE-LINK-FLAP`/`RULE-BGP-DOWN`) uses the existing
exported structural guards (`isCpuHighEvidenceResponse`/
`isLinkFlapEvidenceResponse`/`isBgpDownEvidenceResponse`), never an unsafe
cast. **An incident whose `source` is `"ANOMALY"` but whose `rule_ref` is
not one of these three known values is rejected by the frontend's own
runtime validation (`hasEvidenceConsistentWithRule`,
`src/api/incidents.ts`) before it ever reaches the UI** — the component
itself has a safe generic fallback for an unrecognized rule, but that
fallback is currently unreachable given the stricter client-side
validation; end-to-end support for a future, unrecognized `ANOMALY` rule
does not yet exist.

**`updated_at`/`resolved_at` (Day 7A).** `updated_at` is a required
`datetime` field — the most recent persisted mutation to this incident of
any kind (creation, a repeated-detection update, or an explicit
resolution), distinct from `last_seen_at` (detection-only). `resolved_at`
is a required *key* whose value is `datetime` or `null` — `null` while
`status` is `"OPEN"`, populated once `status` is `"RESOLVED"`. `GET
/incidents` includes both `OPEN` and `RESOLVED` incidents — there is still
no filtering/pagination/sorting query parameter, and no query parameter
hides either status.

## 7. `POST /incidents/{incident_id}/resolve` (Day 7A backend; Day 7B frontend)

Explicitly resolves one existing `OPEN` incident. `OPEN -> RESOLVED` is the
only transition; there is no acknowledgment, reopening, assignment, or
bulk-resolution endpoint.

```
POST /incidents/{incident_id}/resolve
```

No request body.

**Success response** — `200 OK`, the resource itself, no envelope: the
identical `IncidentResponse` shape `GET /incidents` returns (Section 6),
with `status: "RESOLVED"` and both `updated_at`/`resolved_at` populated
(equal to each other):

```json
{
  "incident_id": "8f14e45f-ceea-4c1d-8f1e-1234567890ab",
  "fingerprint": "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
  "device_id": "spine-01",
  "source": "POLICY_VIOLATION",
  "rule_ref": "policy-acl-external-in",
  "affected_resource": "acl:ACL-EXTERNAL-IN:GigabitEthernet0/1:in",
  "severity": "Medium",
  "status": "RESOLVED",
  "evidence": { "...": "unchanged from before resolution" },
  "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
  "created_at": "2026-07-18T10:00:00Z",
  "last_seen_at": "2026-07-18T10:00:00Z",
  "occurrence_count": 1,
  "updated_at": "2026-07-18T11:00:00Z",
  "resolved_at": "2026-07-18T11:00:00Z"
}
```

**Idempotent.** Resolving an already-`RESOLVED` incident also returns
`200` with the incident unchanged — the same `resolved_at`/`updated_at` as
the first successful call, never a new timestamp and never an error.

**Unknown incident** — exact `404` body (see the error table below):

```json
{"code": "incident_not_found", "detail": "Incident 'missing-incident' was not found."}
```

**Frontend consumption notes (Day 7B).** The dashboard now calls this route
for every `OPEN` incident's "Resolve incident" control
(`resolveIncident`, `src/api/incidents.ts`). `incident_id` is encoded via
`encodeURIComponent` as one opaque path segment, exactly like
`submitDeviceConfiguration`'s `device_id` handling; the request is issued
through a dedicated no-body transport primitive (`postNoBody`,
`src/api/client.ts`) — `method: "POST"`, `Accept: application/json`,
`credentials: "omit"`, no `body` key at all (not even `{}`), no
`Content-Type` header, and an optional `AbortSignal`.

The successful `2xx` body is validated in two layers: `isIncidentResponse`
(Section 6's shared, forward-compatible structural validator — the same one
`GET /incidents` uses) first, then this endpoint's own stricter semantic
check — the response is trusted only if its `incident_id` matches the one
requested, `status` is exactly `"RESOLVED"`, and `resolved_at` is a
non-null string. A response that is structurally valid but fails any of
those three checks (wrong incident, still `OPEN`, `resolved_at` still
`null`) is rejected as the same controlled malformed-response error every
other endpoint uses — never returned to the caller. The complete validated
object is then used directly as the new persisted incident state; no
follow-up `GET /incidents` is ever issued after a resolution, success or
failure.

The `404` `incident_not_found` body (above) surfaces through the existing
`ApiRequestError` abstraction unchanged — `code`/`detail` preserved
verbatim — with no special-casing of the status code anywhere in the
frontend; a per-incident controlled error is shown next to the affected
card, and another click retries.

The control itself is rendered only for an incident whose `status` is
exactly `"OPEN"` — `RESOLVED`, the dormant `ACKNOWLEDGED`, and any
unrecognized future status all render no action, matching Section 3's
"treat unknown values defensively" guidance without ever exposing a control
for a status this endpoint can't legally accept.

## 8. `GET /devices/{device_id}/drift` (Day 9, backend-only)

Compares a device's current normalized configuration against its fixed
baseline (the first successfully accepted configuration for that device)
and returns the structural diff. Read-only — no request body, no query
parameters, no write of any kind. **No frontend consumer exists yet** —
the current React dashboard does not call this endpoint. For a snapshot-less
FRR Lab 1 router (NPE-1C3B1) the endpoint returns `409` `ApiErrorResponse`
with `code: "drift_not_applicable"`: drift is unavailable, not "no drift".

```
GET /devices/{device_id}/drift
```

Required path parameter: `device_id` (string). No query parameters.

**Success response** — `200 OK`, the resource itself, no envelope:

```json
{
  "added": [
    {
      "resource": "interface:Ethernet1",
      "field": null,
      "old_value": null,
      "new_value": "Ethernet1"
    }
  ],
  "removed": [],
  "changed": []
}
```

Each entry in `added`/`removed`/`changed`:

| Field | Type | Meaning |
|---|---|---|
| `resource` | `string` | `"interface:<name>"`, `"acl:<name>"`, or `"bgp_neighbor:<neighbor_ip>"` |
| `field` | `string \| null` | The exact changed attribute name (`"description"`, `"ip_address"`, `"mtu"`, `"admin_state"`, `"acl_in"`, `"acl_out"`, `"remote_as"`) for a `changed` entry; `null` for a whole-resource `added`/`removed` entry |
| `old_value` | `string \| null` | `null` for an `added` entry |
| `new_value` | `string \| null` | `null` for a `removed` entry |

A device with exactly one configuration submission always returns
`{"added": [], "removed": [], "changed": []}` (baseline == current, AC-06).
A later submission is always compared against the original, fixed
baseline, never "the previous submission" — there is no
baseline-redesignation endpoint.

**Unknown device** — exact `404` body (see the error table below):

```json
{"code": "device_not_found", "detail": "device not found: 'missing-device'"}
```

**Internal invariant failure** (a corrupted persisted snapshot reference —
not reachable through this API's own write paths): `500`, but **not** this
API's `ApiErrorResponse` JSON shape — the framework's own generic response,
`Content-Type: text/plain; charset=utf-8`, body `Internal Server Error`.
No traceback or other internal detail is included.

Comparison scope: interfaces (by name; scalar fields `description`,
`ip_address`, `mtu`, `admin_state`, `acl_in`, `acl_out`), BGP neighbors (by
`neighbor_ip`; scalar field `remote_as`), and whole-ACL additions/removals
(by name) — a matched ACL's `entries` are not diffed. `hostname` and
`static_routes` are not compared. No severity, recommendation, or incident
is ever produced by this endpoint.

## 8A. `GET /devices` and `GET /devices/{device_id}` (Day 12, backend-only)

Both are available backend contracts. **Frontend consumption is not
implemented yet** — the current React dashboard calls neither. Both are
read-only and return the resource directly: no `data`/`error` envelope.

```
GET /devices
```

No query parameters (no filtering, pagination, or sorting). `200` returns a
bare array of `DeviceSummaryResponse`, ordered by `created_at` ascending
then `device_id` ascending; an empty array when no device exists. Fields:
`device_id`, `vendor`, `current_snapshot_id`, `baseline_snapshot_id`,
`created_at`, `updated_at`. `500` is a documented `ApiErrorResponse`
(`persistence_error`, generic public detail).

```
GET /devices/{device_id}
```

Only the `device_id` path parameter; no request body. `200` returns
`DeviceDetailResponse`: `device_id`, `vendor`, `current_snapshot_id`,
`baseline_snapshot_id`, `created_at`, `updated_at`, and `normalized_config`
— the device's **current** normalized configuration, using the same
normalized-configuration schema as Section 5's `normalized_config`. No raw
configuration is returned. An unknown device returns `404`
`ApiErrorResponse` with `code: "device_not_found"`.

**NPE-1C3B1 amendment.** A registered Lab 1 FRR router (`vendor: "frr"`) has
no configuration snapshot: `current_snapshot_id` and `baseline_snapshot_id`
are `null` and `normalized_config` is `null`. `normalized_config` therefore
is a required-but-nullable field (OpenAPI `anyOf: [NormalizedConfigurationResponse,
null]`); it is `null` only for an FRR router and never for Cisco/Arista. A
Cisco/Arista device without a snapshot is still a broken invariant (`500`).
`GET /devices` lists FRR routers like any other device. No frontend code
consumes `GET /devices/{device_id}` or the drift endpoint, so no frontend
change is required.

## 9. `POST /devices/{device_id}/telemetry` (Day 9b, backend-only) and `GET /devices/{device_id}/telemetry/recent` (Day 9b backend; Day 11B frontend)

Ingests telemetry samples and runs three deterministic anomaly rules
against them. Neither response contains any incident field (no
`incident_id`, `severity`, `recommendation`, or `fingerprint`). **As of
Day 9c, a fired anomaly does create or update an incident** — as an
internal side effect of the same atomic `POST` operation, never reflected
in this endpoint's own response. The resulting incident is visible only
through `GET /incidents` (Section 6). **`POST` remains backend-only — the
frontend never submits telemetry, invokes the simulator, or invokes
Docker. `GET /devices/{device_id}/telemetry/recent` now has a frontend
consumer as of Day 11B** — see below.

**`POST /devices/{device_id}/telemetry`** — `device_id` (path) is
authoritative; `observed_at` is always server-generated, never accepted in
the request body.

```json
// Request
{
  "sampled_at": "2026-07-18T10:00:00Z",
  "cpu_utilization_pct": 95.0,
  "memory_utilization_pct": 60.0,
  "interface_error_rate": 0.0,
  "interface_states": [
    {"name": "GigabitEthernet0/1", "oper_state": "up"}
  ],
  "bgp_sessions": [
    {"neighbor_ip": "10.0.0.1", "state": "Established"}
  ]
}
```

**Success response** — `201 Created`, the resource itself, no envelope:

```json
{
  "sample": { "...": "the persisted TelemetrySample, same shape as the GET response item below" },
  "anomalies": [
    {
      "rule_id": "RULE-CPU-HIGH",
      "device_id": "spine-01",
      "detected_at": "2026-07-18T10:00:00Z",
      "evidence": { "samples": ["...", "..."] }
    }
  ]
}
```

`anomalies` reflects exactly the three deterministic rules evaluated in a
fixed order (`RULE-CPU-HIGH`, `RULE-LINK-FLAP`, `RULE-BGP-DOWN`) — it may
be empty. No severity or recommendation is present on an anomaly.

**`GET /devices/{device_id}/telemetry/recent`** — requires a `since` query
parameter (timezone-aware UTC `datetime`; naive or non-UTC-offset values
are rejected with `invalid_request`, Section 10). No `limit`/`minutes`/
`page`/`cursor` parameter exists — every matching sample is returned, in
persisted order, with no API-clock-based filtering.

```
GET /devices/{device_id}/telemetry/recent?since=2026-07-18T09:55:00Z
```

**Success response** — `200 OK`, a bare JSON array (no envelope):

```json
[
  {
    "device_id": "spine-01",
    "sampled_at": "2026-07-18T10:00:00Z",
    "cpu_utilization_pct": 95.0,
    "memory_utilization_pct": 60.0,
    "interface_error_rate": 0.0,
    "interface_states": [
      {"name": "GigabitEthernet0/1", "oper_state": "up"}
    ],
    "bgp_sessions": [
      {"neighbor_ip": "10.0.0.1", "state": "Established"}
    ]
  }
]
```

Both endpoints share the existing `device_not_found`/404 and
`invalid_request`/422 error mapping (Section 10) — no new error class or
API code was introduced for telemetry.

**Frontend consumption notes (Day 11B).** `fetchRecentTelemetry`
(`src/api/telemetry.ts`) calls `GET
/devices/{device_id}/telemetry/recent` with a fixed, internal
`since` value — the exported constant `TELEMETRY_HISTORY_SINCE =
"1970-01-01T00:00:00Z"` (`src/hooks/useTelemetryWorkspace.ts`), never the
browser's current clock, and not currently exposed as a UI input. The
response's bare-array shape, empty-array-is-valid-success semantics, and
persisted (ascending) ordering are all consumed unmodified —
`fetchRecentTelemetry` performs no client-side sorting, deduplication, or
row limiting. `selectLatestTelemetrySample` selects the final array
element as the latest sample, per this section's documented ascending
order. Every sample is validated at runtime by `isTelemetrySampleResponse`
before the frontend trusts it, following the same forward-compatible,
structural-only convention as the rest of this document (interface/BGP
state values are checked as non-empty strings, not closed enums). The
telemetry workspace (`TelemetryPanel.tsx`) requests this endpoint and `GET
/incidents` (Section 6) concurrently, on one shared `AbortSignal`, and
handles their success/failure independently — see README.md's "Frontend
Telemetry Workspace" section for the full user-facing behavior.

## 8B. `GET /network/state` (NPE-1D1B, backend-only)

Read-only view of the **latest successfully ingested** live operational state
of the one fixed Lab 1 (`meta-rne-bgp`). **The frontend consumer is the NPE-1D2 live network workspace** (see
the NPE-1D2 notes below). There is no request body, no query parameter and no lab selector,
and no public write endpoint: `POST`/`PUT`/`PATCH`/`DELETE /network/state` do
not exist (`405`). Ingestion stays the trusted CLI
`scripts/lab_ingest_operational_state.py`. `operation_id` is
`get_network_state`; CORS is unchanged (`GET` is already allowed).

`200` returns `NetworkStateResponse` (no envelope):

```json
{
  "collection_id": "...",
  "collected_at": "2026-10-07T12:00:00Z",
  "retrieved_at": "2026-10-07T12:00:07Z",
  "source": {"collector": "containerlab-frr", "lab_name": "meta-rne-bgp"},
  "nodes": [ {"node_id": "leaf-1", "device_id": "lab1-leaf-1", "role": "router",
              "interfaces": [...], "bgp": {...}, "routes": [...],
              "reachability": null, "unavailable": []} ]
}
```

- `collected_at` is when the collector observed the fabric (stored).
  `retrieved_at` is the API server's UTC clock at request time: it is not
  stored, not part of the state, and not a freshness threshold. A client may
  compare `retrieved_at - collected_at` for presentation; the API defines no
  stale threshold, health score, healthy flag or per-node status.
- `source` is derived from the nodes' common source identity; stored data
  without a single source fails closed (`500 serialization_error`).
- `nodes` keep the collector's node set (six in a full Lab 1 collection).
  `device_id` is the platform device ID for the four routers
  (`spine-1`->`lab1-spine-1`, `spine-2`->`lab1-spine-2`, `leaf-1`->`lab1-leaf-1`,
  `leaf-2`->`lab1-leaf-2`) and **`null` for hosts** (hosts are not registered
  devices). The fixed mapping is not read from the `devices` table.
- `interfaces[]`: `name`, `oper_state` (`up`/`down`/`unknown`), `addresses[]`.
- `bgp`: `local_as`, `router_id`, `established_count` (derived), `neighbors[]`
  with `neighbor_ip`, `remote_as`, `state` (`Idle`/`Connect`/`Active`/
  `OpenSent`/`OpenConfirm`/`Established`/`Unknown`), `raw_state` (nullable),
  `prefixes_received` and `uptime_ms` (nullable; only set for `Established`).
- `routes[]`: `prefix`, `protocol`, `ecmp_path_count` (= `next_hops.length`),
  `next_hops[]` of `{ip, interface}` (each nullable, never both). Next hops are
  the **active** ones only; inactive paths are not represented.
- `reachability[]` (hosts): `from_node`, `target`, `sent`, `received`,
  `succeeded`. `received: 0` is an observed total loss, not unavailable.
- **`UNKNOWN`** is an explicit value the device reported or that could not be
  mapped (`oper_state: "unknown"`, BGP `state: "Unknown"` with the original text
  in `raw_state`, e.g. `Clearing`). It is never rewritten as down.
- **`unavailable[]`** (`{facet, reason}`) lists facets the collector could not
  observe. The matching facet is then `null`, never `[]`.
- **Missing / not applicable** are also `null`, distinguished by role:

  | facet | router | host |
  |---|---|---|
  | `interfaces` | array; `null` if unavailable | array; `null` if unavailable |
  | `bgp` | object; `null` if unavailable | `null` (not applicable) |
  | `routes` | array (may be empty = observed none); `null` if unavailable | `null` (not collected) |
  | `reachability` | `null` (not applicable) | array; `null` if unavailable |

- No topology or links are returned; links are never inferred from BGP.

`404` `ApiErrorResponse` `code: "operational_state_not_found"`, `detail`
`"No operational state is available for lab 'meta-rne-bgp'."`, when nothing has
been ingested; an absent state is never `200` with an empty/`null` body.
`500` is `persistence_error` / `serialization_error` with generic public detail.


**Frontend consumption notes (NPE-1D2, live network workspace).** The default
view of the app (`#network`; the previous dashboard is `#tools`) reads only
`GET /network/state` and `GET /incidents`; the existing manual resolve call is
the only write, and the browser never collects, ingests, registers or injects.

- *Fixed declared topology.* The six nodes / six physical links come from a
  frontend constant (`src/network/lab1Topology.ts`) copied from
  `lab/topology.clab.yml` and the Lab 1 addressing; a Vitest drift test fails if
  it diverges from the topology file, `frr.conf` and `lab1_identity.py`. Links
  are never inferred from BGP, subnets or next hops.
- *Physical vs BGP.* A link is DOWN if either endpoint interface reports
  `down`, UP only if both report `up`, otherwise UNKNOWN (unavailable facet,
  `unknown`, missing interface/node). BGP is a separate per-link adjacency over
  the four leaf-spine links (not eight directional neighbors): Established if
  both ends are, Degraded if either end is a known non-Established state,
  otherwise Unknown. BGP never colours the physical link.
- *Five meanings kept apart:* observed healthy, observed failure, UNKNOWN,
  unavailable/not observed, and not applicable (e.g. BGP on a host), each with
  a text label and glyph (never colour alone). A missing ECMP route is "not
  observed", never 0 paths; an unavailable probe is not a reachability failure.
- *Summary.* BGP adjacencies, ECMP path count per leaf toward the other host
  network (count from the API; expected 2 is declared metadata), host probe
  `received/sent`, OPEN incident count, and observation age. A frontend-only
  "presentation status" is derived ONLY from `GET /network/state` observations:
  Degraded on any explicit failure (a link down, a known non-Established
  adjacency, reduced ECMP, a failed probe), Healthy only when every required
  observation is present and healthy, else Unknown. Incident lifecycle is never
  an input: the two endpoints are independent reads and may briefly disagree
  (e.g. network recovered while an incident still reads OPEN), and the view
  shows exactly that. It is not persisted, not a score and not RCA.
- *Incident scope.* The workspace lists and counts only ANOMALY incidents on the
  four fixed Lab 1 router device IDs (OPEN and RESOLVED history; OPEN count =
  OPEN only). Unrelated incidents are untouched and remain, unchanged, in the
  global `#tools` view.
- *Observation age* is `retrieved_at - collected_at` (server-anchored); no
  freshness threshold is applied.
- *Polling.* Every 5 s (`LIVE_REFRESH_INTERVAL_MS`, presentation only, not the
  collector cadence) both endpoints are re-read, independently; a tick skips a
  request still in flight; the timer and requests are cancelled on unmount.
  The tools view does not poll. A failed refresh keeps the last snapshot
  visible with a notice.
- *Stale protection.* Responses are reconciled by parsed `collected_at`: newer
  wins, older is ignored, same instant + same `collection_id` refreshes
  metadata, same instant + different `collection_id` keeps the current state.
- *States.* `404 operational_state_not_found` is the "No live network snapshot
  yet" empty state; unreachable / malformed / API-error are distinct error
  states. No-incident is never read as healthy.
- Incidents are listed individually (device, resource, rule, severity,
  occurrences, status, manual resolve); there is no RCA, remediation or AI.

## 8C. `GET /network/diagnosis` (NPE-1E1B, backend-only)

Read-only, on-demand deterministic root-cause analysis of the **latest ingested**
operational state of Lab 1. Computed per request, never stored: no diagnosis ID,
status, history or write method (`POST`/`PUT`/`PATCH`/`DELETE` do not exist).
No body, no query parameter. `operation_id` is `get_network_diagnosis`. There is
no frontend consumer yet (NPE-1E1C).

`200` returns `NetworkDiagnosisResponse` (no envelope); a healthy network is
`200` with `"diagnoses": []`, never `404`/`204`/`null`:

```json
{
  "collection_id": "run-1",
  "collected_at": "2026-10-07T12:00:00Z",
  "diagnoses": [
    {
      "diagnosis_type": "PHYSICAL_LINK_FAILURE",
      "confidence": "HIGH",
      "resource": {
        "resource_id": "leaf-1:eth1--spine-1:eth1",
        "endpoint_a": {"node_id": "leaf-1", "interface": "eth1"},
        "endpoint_b": {"node_id": "spine-1", "interface": "eth1"}
      },
      "evidence": [
        {"kind": "INTERFACE_STATE", "role": "SUPPORTS", "node_id": "leaf-1",
         "subject": "eth1", "observed": {"interface_name": "eth1", "state": "DOWN"}}
      ],
      "symptom_keys": [
        {"device_id": "lab1-leaf-1", "affected_resource": "bgp-neighbor:10.255.0.0"}
      ],
      "linked_incidents": [
        {"incident_id": "...", "device_id": "lab1-leaf-1",
         "affected_resource": "bgp-neighbor:10.255.0.0", "severity": "Critical",
         "status": "OPEN", "created_at": "...", "last_seen_at": "...",
         "occurrence_count": 1}
      ]
    }
  ]
}
```

- `diagnosis_type`: `PHYSICAL_LINK_FAILURE` | `BGP_SESSION_FAILURE` |
  `INSUFFICIENT_EVIDENCE`. `confidence`: `HIGH` | `MEDIUM` | `LOW`, and `null`
  exactly for `INSUFFICIENT_EVIDENCE`. `PHYSICAL_LINK_FAILURE` means the declared
  link is the failed resource, not why it failed.
- `evidence[]`: `kind` (`INTERFACE_STATE`, `BGP_SESSION`, `ADDRESS_MATCH`,
  `ROUTE_NEXT_HOP`, `ECMP_PATHS`, `REACHABILITY`), `role` (`SUPPORTS`,
  `CONTRADICTS`, `UNKNOWN`), `node_id`, `subject`, and one `observed` shape per
  kind: interface `{interface_name, state}`; BGP `{neighbor_ip, state,
  raw_state}`; address `{interface_name, declared_address, observed_addresses,
  matches}`; next hop `{next_hop_ip, interface_name, active_route_count}`; ECMP
  `{prefix, observed_paths, expected_paths, link_next_hop_ip,
  link_next_hop_present}`; reachability `{target, sent, received}`. Unobserved
  values are `null`. Engine ordering of diagnoses and evidence is preserved.
- `symptom_keys` are correlation keys, always present. `linked_incidents` are
  OPEN RULE-BGP-DOWN incidents matched by the existing fingerprint, in
  symptom-key order, without duplicates; it may be empty (RESOLVED incidents, no
  incident yet, or an incident newer than the diagnosed snapshot are not linked).
  Diagnosis evidence always comes from the network state, never from incidents.

`404` `ApiErrorResponse` `code: "operational_state_not_found"` when nothing has
been ingested. `500` is `persistence_error` / `serialization_error`.

## 10. Errors

Every failure response is a bare object, no envelope:

```json
{"code": "<stable_snake_case_code>", "detail": "<public_detail>"}
```

**except** malformed request-schema validation, which stays FastAPI's own
default `{"detail": [...]}` shape (`HTTPValidationError`) — this is why
`POST /devices/{device_id}/config`'s `422` response documents **both**
body shapes in its OpenAPI schema (a `oneOf` of `HTTPValidationError` and
`ApiErrorResponse`); which one you get depends on where the failure
occurred, not on the status code alone:

| Status | `code` | Body shape | Cause |
|---|---|---|---|
| 422 | — (`HTTPValidationError`) | FastAPI default | Malformed request: missing/blank `vendor`, empty `raw_config_text`, unknown field (`device_id`/`observed_at`/anything else), wrong JSON type. |
| 422 | `unsupported_vendor` | `ApiErrorResponse` | `vendor` is well-formed but names no registered adapter. |
| 422 | `configuration_parse_error` | `ApiErrorResponse` | Adapter rejected `raw_config_text`; `detail` includes the parser message and, when available, `(line N)`. |
| 422 | `invalid_request` | `ApiErrorResponse` | Any other caller/application `ValueError`. |
| 409 | `device_conflict` | `ApiErrorResponse` | Vendor/timestamp conflict against the stored `Device`. |
| 409 | `snapshot_already_exists` | `ApiErrorResponse` | Duplicate `snapshot_id` (should not occur under normal use — server-generated). |
| 409 | `referenced_device_not_found` | `ApiErrorResponse` | Internal referential-integrity failure. |
| 500 | `persistence_error` | `ApiErrorResponse` | Database failure. `detail` is generic — never SQL, constraint names, or a stack trace. |
| 500 | `serialization_error` | `ApiErrorResponse` | Internal data-encoding failure. `detail` is generic. |
| 404 | `incident_not_found` | `ApiErrorResponse` | (Day 7A) `POST /incidents/{incident_id}/resolve` with an `incident_id` that does not exist. `detail` is exactly `"Incident '<incident_id>' was not found."`. |
| 404 | `operational_state_not_found` | `ApiErrorResponse` | (NPE-1D1B) `GET /network/state` and (NPE-1E1B) `GET /network/diagnosis` before any operational state has been ingested. |
| 500 | — (no custom body) | none | Any unmapped/unexpected server failure — normal production 500 behavior, no leaked internals. |

`GET /incidents` can only produce the `persistence_error`/500 row above
(read-only, no request body to validate). `POST /incidents/{incident_id}/
resolve` can only produce the `incident_not_found`/404 row (no request
body to validate) or an unmapped 500.

**Frontend consumption notes (Day 6C).** Both `GET /incidents` and
`POST /devices/{device_id}/config` failures are funneled through the same
`parseErrorDetail` logic in `src/api/client.ts`, so every error category in
the table above is presented safely and consistently:

- **`ApiErrorResponse`** (`{code, detail}`) — `detail` is shown as
  controlled visible text; `code` is preserved on the thrown
  `ApiRequestError` and, in the configuration-submission form, displayed
  alongside the message when present.
- **FastAPI's `HTTPValidationError`** (`{"detail": [...]}`, the request-
  schema-validation row above) — recognized narrowly by `detail` being an
  array (never by inspecting its contents) and mapped to one stable safe
  message; the array itself, its field locations (`loc`), messages, and any
  echoed rejected input are never rendered.
- **Malformed, empty, or non-JSON bodies (e.g. an HTML error page from a
  proxy)** — fall back to the same stable generic message; the raw body
  text is never surfaced.
- **A structurally malformed successful (`2xx`) response** — rejected by
  the relevant runtime validator (`isIncidentResponse`/
  `isConfigurationSubmissionResponse`) into the identical controlled-error
  presentation, never partially rendered and never a raw parser exception
  message.

No response is ever rendered via `dangerouslySetInnerHTML`, and no stack
trace, SQL detail, or other internal value this document's error table
already says the backend keeps generic is ever exposed further by the
frontend.

## 11. Example: missing-ACL submission

```bash
curl -X POST http://localhost:8080/devices/spine-01/config \
  -H 'Content-Type: application/json' \
  -d '{"vendor": "cisco-ios-xe", "raw_config_text": "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"}'
```

Produces the `201` response shown in Section 5 with
`violations_detected: 1, incidents_created: 1, incidents_updated: 0` — no
ACL is assigned inbound on `GigabitEthernet0/1`, and a
`policy-acl-external-in` policy applies to `spine-01`.

## 12. Not implemented yet

Do not build frontend features assuming any of the following exist:

- A frontend consumer of `GET /network/diagnosis` (Section 8C, backend-only
  until NPE-1E1C), diagnosis persistence or history, remediation
- A topology API, an operational-state history, or any operational-state write
  endpoint (`GET /network/state` has a frontend consumer since NPE-1D2; the
  browser never writes operational state)
- Authentication or authorization (every endpoint is unauthenticated)
- Pagination, filtering, or sorting query parameters on `GET /incidents`
- Incident acknowledgment, reopening, assignment, comments/notes, audit
  history, or bulk resolution — `POST /incidents/{incident_id}/resolve`
  (Section 7, Day 7A backend / Day 7B frontend) is the one narrow exception:
  explicit, single-incident `OPEN -> RESOLVED` resolution, now called by the
  dashboard's "Resolve incident" control for exact `OPEN` incidents only
- `GET /incidents/{id}` (`GET /devices` and `GET /devices/{device_id}`
  exist as backend endpoints as of Day 12, Section 8A, but no frontend
  consumer exists yet)
- A frontend consumer of configuration drift detection
  (`GET /devices/{device_id}/drift`, Section 8, implemented as of Day 9)
  — still backend-only; the dashboard does not call this endpoint
- Frontend telemetry **submission** — `POST
  /devices/{device_id}/telemetry` remains backend-only; the browser never
  submits telemetry, invokes the simulator, or invokes Docker
- Automatic polling, WebSockets, or server-sent events anywhere in the
  telemetry workspace — refresh is manual only (Day 11B)
- Server-side filtering of `GET /incidents` by device or source — the
  telemetry workspace filters the existing unfiltered response client-side
  (Day 11B)
- A chart, sparkline, or any other data-visualization dependency for
  telemetry — the recent-sample history renders as a semantic table only
  (Day 11B)
- Display of AC-10's structured incident-event log lines through the
  browser — those remain API-process stdout only, never requested or
  parsed by the frontend
- End-to-end frontend support for an unrecognized future `ANOMALY`
  `rule_ref` — the frontend's own API-client validation rejects it before
  the telemetry workspace's safe generic fallback would ever render it
  (Day 11B; see Section 6's Day 11B consumption notes)
- Any vendor besides `cisco-ios-xe`/`arista-eos` (both now supported as of
  Day 8A — see Section 5's `vendor` row and CLAUDE.md's "Current Phase")
- Wildcard or shared policy applicability, and policy authoring/CRUD

See `docs/product-spec.md` Section 6/7 and `CLAUDE.md`'s "Current Phase"
for the authoritative, current scope.

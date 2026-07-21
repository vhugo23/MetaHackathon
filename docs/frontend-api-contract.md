# Frontend API Contract — Meta RNE Platform

**Status:** Day 6A stabilized this contract; Day 6B built the first
frontend consumer (`frontend/`) against it, exercising `GET /incidents`
only; Day 6C added the second frontend consumer, exercising
`POST /devices/{device_id}/config` — see README.md's "Current Day 6C
scope". **This document describes the existing backend contract only; Day
6C did not modify any backend schema or endpoint** — every shape and error
mapping below was already true as of Day 6A/6B and remains unchanged. The
"Frontend consumption notes" subsections added to Sections 5–7 below
describe how the frontend uses this contract, not a change to the contract
itself.
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
| `vendor` | `string` | Non-empty, non-whitespace-only. Currently only `"cisco-ios-xe"` resolves to a registered adapter. |
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
    "occurrence_count": 1
  }
]
```

`fingerprint` is present and treated as a normal, non-internal field.
`evidence` is currently always the `POLICY_VIOLATION` shape shown above —
the only `IncidentSource` any current detection path produces.

## 7. Errors

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
| 500 | — (no custom body) | none | Any unmapped/unexpected server failure — normal production 500 behavior, no leaked internals. |

`GET /incidents` can only produce the `persistence_error`/500 row above
(read-only, no request body to validate).

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

## 8. Example: missing-ACL submission

```bash
curl -X POST http://localhost:8080/devices/spine-01/config \
  -H 'Content-Type: application/json' \
  -d '{"vendor": "cisco-ios-xe", "raw_config_text": "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"}'
```

Produces the `201` response shown in Section 5 with
`violations_detected: 1, incidents_created: 1, incidents_updated: 0` — no
ACL is assigned inbound on `GigabitEthernet0/1`, and a
`policy-acl-external-in` policy applies to `spine-01`.

## 9. Not implemented yet

Do not build frontend features assuming any of the following exist:

- Authentication or authorization (every endpoint is unauthenticated)
- Pagination, filtering, or sorting query parameters on `GET /incidents`
- Incident acknowledgment/resolution commands
- `GET /devices`, `GET /devices/{id}`, `GET /incidents/{id}`
- Drift detection, telemetry ingestion, or anomaly rules
- Any vendor besides `cisco-ios-xe`

See `docs/product-spec.md` Section 6/7 and `CLAUDE.md`'s "Current Phase"
for the authoritative, current scope.

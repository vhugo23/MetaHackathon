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
| Frontend (later slice) | React, TypeScript, Vite |
| Testing | pytest, Vitest, Playwright |
| Deployment | Docker Compose |
| CI | GitHub Actions |
| Architecture | Modular monolith (see [ADR-0001](./docs/adr/0001-modular-monolith.md)) |

Full rationale: [ADR-0001](./docs/adr/0001-modular-monolith.md) and
[ADR-0002](./docs/adr/0002-technology-stack-and-persistence.md).

## Planning Documents

- [docs/problem-statement.md](./docs/problem-statement.md) — original hackathon brief
- [docs/product-spec.md](./docs/product-spec.md) — requirements, acceptance criteria, vertical slice
- [docs/architecture.md](./docs/architecture.md) — modular monolith design, flows, deployment
- [docs/domain-model.md](./docs/domain-model.md) — entities, invariants, deduplication
- [docs/test-strategy.md](./docs/test-strategy.md) — test levels, fixtures, first-slice test plan
- [docs/adr/0001-modular-monolith.md](./docs/adr/0001-modular-monolith.md)
- [docs/adr/0002-technology-stack-and-persistence.md](./docs/adr/0002-technology-stack-and-persistence.md)

## Current Project Status

**Planning phase.** The documents above went through two consistency
correction passes on 2026-07-18: the first resolved cross-document
conflicts (positioning, technology stack, baseline semantics, incident
deduplication, error taxonomy, FR/AC numbering); the second was an
implementation-readiness pass (deterministic normalization with no
`normalized_at` field, the two-stage vendor-validation boundary, atomic
incident deduplication enforced by a PostgreSQL partial unique index —
not find-then-save, the exact Slice 1 endpoint list and `POST` response
shape, concrete identifier-format rules, the full Cisco parser-failure
contract, and log-emission-after-commit semantics). Per
[CLAUDE.md](./CLAUDE.md), **no application code exists yet**, and none
will be written until these corrected documents are reviewed and
approved. No dependency manifests (`requirements.txt`, `package.json`,
etc.) exist yet either.

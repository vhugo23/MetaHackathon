# ADR 0001: Modular Monolith Over Microservices

**Status:** Accepted
**Date:** 2026-07-18

## Context

The Meta RNE Platform (product-spec.md) needs to ingest configuration and
telemetry, normalize it, evaluate policies and drift, detect anomalies,
generate incidents, and serve a REST API and later a React dashboard. The
problem statement describes a hyperscale motivating context, but this
project is a working prototype built on a hackathon timeline (R-04), by a
small team, with no production traffic.

Two structural options were considered:

1. **Microservices** — separate deployables per capability (ingestion,
   detection, incident store, API gateway), communicating over a network
   or message broker.
2. **Modular monolith** — one deployable process, internally partitioned
   into modules with dependencies enforced by import direction.

## Decision

Build a **modular monolith**: one backend process (`api`, `application`,
`domain`, `detection`, vendor adapters, `persistence`, `observability`),
plus one separately deployable React frontend later (FR-10). See
architecture.md Section 2 for the module boundaries and Section 16 for the
constraints this implies (no Kubernetes, no Kafka/message broker).

Module boundaries are enforced by **import direction** (`api` depends on
`application`, `application` depends on `domain`/`detection` and on
persistence *interfaces*; nothing depends outward) — not by network calls
or separate repositories. Vendor isolation (NFR-01) and framework
independence (NFR-02) are achieved the same way a microservice boundary
would achieve them, without paying for network calls, service discovery,
or distributed transactions.

## Consequences

- **Positive:** one process to build, test, and deploy; one database
  transaction can span config ingestion, policy evaluation, and incident
  creation (architecture.md Section 12), so there is no distributed
  transaction or eventual-consistency problem to design around. Fast
  local iteration; simple Docker Compose deployment (architecture.md
  Section 15).
- **Positive:** the module boundaries are still real — `domain`/`detection`
  cannot import FastAPI or SQLAlchemy types, so extracting a module into
  its own service later (if ever justified) is a matter of adding a
  network boundary at an already-clean seam, not a rewrite.
- **Negative:** no independent scaling or independent deployment of, say,
  the anomaly-detection rule engine versus the API layer. This is
  accepted (product-spec Section 7): the MVP explicitly excludes
  high-availability and horizontal scaling.
- **Negative:** a bug in one module can crash the whole process, unlike an
  isolated microservice failure. Accepted for a single-instance prototype
  with no HA requirement.

## Alternatives Considered

- **Microservices** — rejected: adds network/serialization overhead,
  service discovery, and distributed-transaction complexity with no
  corresponding benefit at this scale or timeline; explicitly excluded by
  product-spec Section 7.
- **Serverless functions per capability** — rejected for the same
  reasons, plus added complexity in local development and testing
  (test-strategy.md's E2E tests need a coherent, single stack to start
  via Docker Compose).

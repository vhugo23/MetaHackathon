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

We are in the planning and architecture phase.

On 2026-07-18, product-spec.md, architecture.md, domain-model.md, and
test-strategy.md went through two Day 1 correction passes: the first
resolved cross-document conflicts (positioning, technology stack,
configuration baseline semantics, incident deduplication, error taxonomy,
FR/AC numbering); the second was an implementation-readiness pass
(deterministic normalization, the vendor-validation boundary, atomic
incident deduplication via a database constraint, the exact Slice 1
endpoint list and API response shapes, identifier-format rules, the
Cisco parser-failure contract, log-emission-after-commit semantics). See
README.md's "Current Project Status" for the full list.

Do not write application code, and do not create dependency manifests
(`requirements.txt`, `package.json`, etc.), until the corrected product
specification, architecture, domain model, and test strategy are
reviewed and approved.
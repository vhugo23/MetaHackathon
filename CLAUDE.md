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

**Day 3B — Configuration Policy Domain and Deterministic Evaluation.**

Day 1 planning, Day 2 scaffolding, and Day 3A are complete and approved.
See README.md's "Current Project Status" for the full history.

Application code is currently allowed **only** for the completed Day 3A
and Day 3B capabilities:

- normalized configuration domain objects (Day 3A)
- vendor adapter contracts, `AdapterRegistry`, Cisco IOS-XE parsing and
  normalization (Day 3A)
- `ConfigurationPolicy`/`RequiredAclRule`, `ConfigurationViolation`/
  `AclAssignmentEvidence`, and the deterministic `PolicyEvaluator` (Day 3B)
- unit tests and representative fixtures
- documentation corrections explicitly approved for Day 3A/3B (see below)

**Documentation corrections applied for Day 3A:**

1. `docs/domain-model.md` — added `description: string | null` to the
   normalized interface model.
2. `docs/architecture.md` and `docs/test-strategy.md` — split "invalid
   interface IP address or subnet mask" into two separate parser
   failures (address vs. mask).

**Documentation corrections applied for Day 3B:**

1. `docs/domain-model.md` §6/§7/§16/§18 — `RequiredAclRule` gained
   `severity`/`recommendation`; `ConfigurationViolation` restructured to
   `rule_ref`/`affected_resource`/`severity`/`evidence`/`recommendation`
   with a new `AclAssignmentEvidence` value object; `ViolationType` split
   into `MISSING_REQUIRED_ACL` and `TARGET_INTERFACE_MISSING`; `"*"`
   wildcard `applies_to` matching scoped out of Day 3B (exact
   `applies_to == device_id` only).
2. `docs/architecture.md` §7/§9 — evaluator narrative updated to match
   (no `FixedClock`, deterministic violation ordering, computed vs.
   copied violation fields); one stale `policy_id` reference corrected
   to `rule_ref`.
3. `docs/test-strategy.md` §12/§19 — policy-evaluator sub-case list and
   one test name updated to match.

**Still prohibited**: `IncidentFactory`/`Incident`, fingerprinting and
deduplication, persistence (SQLAlchemy repositories, tables, Alembic
business migrations), API ingestion endpoints, `DriftDetector`,
`RuleEngine`, telemetry, and React. Those begin on a later day, against
the domain model and architecture already documented, with tests written
first per the Development Rules above.
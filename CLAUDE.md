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

**Day 4A — Incident Domain, Deterministic Fingerprinting, and
IncidentFactory.**

Day 1 planning, Day 2 scaffolding, Day 3A, and Day 3B are complete and
approved. See README.md's "Current Project Status" for the full history.

Application code is currently allowed **only** for the completed Day 3A,
Day 3B, and Day 4A capabilities:

- normalized configuration domain objects (Day 3A)
- vendor adapter contracts, `AdapterRegistry`, Cisco IOS-XE parsing and
  normalization (Day 3A)
- `ConfigurationPolicy`/`RequiredAclRule`, `ConfigurationViolation`/
  `AclAssignmentEvidence`, and the deterministic `PolicyEvaluator` (Day 3B)
- `IncidentSource`/`IncidentStatus`, `IncidentCandidate`/
  `PolicyViolationIncidentEvidence`, `IncidentFactory.build_candidate`,
  and `compute_fingerprint` (Day 4A)
- unit tests and representative fixtures
- documentation corrections explicitly approved for Day 3A/3B/4A (see
  below)

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

**Documentation corrections applied for Day 4A:**

1. `docs/domain-model.md` §7/§10/§11/§13/§16/§17/§18 — resolved a
   conflict where §7 said `IncidentFactory` copies `recommendation`
   verbatim while §13 and the §18 worked example showed it templated
   into different wording; `Incident.recommendation`/
   `IncidentCandidate.recommendation` are now documented as a plain
   `string`, copied verbatim, with a `Recommendation{summary, details}`
   value object and template generation explicitly deferred. Also
   resolved a second conflict where `ConfigurationViolation.
   affected_resource` (interface-centered) and `Incident.
   affected_resource` (`"acl:{name}:{interface}:{direction}"`) were
   documented as two different formats needing an undefined derivation;
   `affected_resource` is now copied verbatim end-to-end (only one
   format), which also corrects a pre-existing §18 worked example that
   didn't match §7's own contract. Documented the new
   `PolicyViolationIncidentEvidence` value object (adds `violation_type`/
   `source_snapshot_id`, keeps `actual_acl_name`, renames
   `expected_acl_name`) and the `IncidentCandidate.observed_at` field
   (= `violation.detected_at`, not read from a clock).
2. `docs/architecture.md` §9 — the `IncidentFactory.build_candidate` flow
   and the vertical-slice value table updated to match (verbatim
   `affected_resource`/`recommendation`, added `observed_at`).
3. `docs/test-strategy.md` §13/§19 — `IncidentFactory`/fingerprint test
   descriptions updated to match the verbatim-copy contract and the
   actual test name/location (`compute_fingerprint` lives in
   `tests/unit/domain/test_incident.py`, a `domain` service per
   domain-model.md §17, not `detection`).

**Still prohibited**: the persisted `Incident` dataclass, `IncidentRepository`/
`upsert_open_incident`, deduplication itself (fingerprint is computed but
nothing yet enforces uniqueness), persistence (SQLAlchemy repositories,
tables, Alembic business migrations), API ingestion endpoints,
`DriftDetector`, `RuleEngine`, telemetry, and React. Those begin on a
later day, against the domain model and architecture already documented,
with tests written first per the Development Rules above.
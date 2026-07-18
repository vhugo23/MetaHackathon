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

**Day 4B1 — Persistence Foundations: Domain Shapes, Serialization, Private
ORM Models, and the First Slice 1 Migration.**

Day 1 planning, Day 2 scaffolding, Day 3A, Day 3B, and Day 4A are complete
and approved. See README.md's "Current Project Status" for the full
history. Day 4B is split into three reviewable gates (4B1/4B2/4B3); only
4B1 is complete.

Application code is currently allowed **only** for the completed Day 3A,
Day 3B, Day 4A, and Day 4B1 capabilities:

- normalized configuration domain objects (Day 3A)
- vendor adapter contracts, `AdapterRegistry`, Cisco IOS-XE parsing and
  normalization (Day 3A)
- `ConfigurationPolicy`/`RequiredAclRule`, `ConfigurationViolation`/
  `AclAssignmentEvidence`, and the deterministic `PolicyEvaluator` (Day 3B)
- `IncidentSource`/`IncidentStatus`, `IncidentCandidate`/
  `PolicyViolationIncidentEvidence`, `IncidentFactory.build_candidate`,
  and `compute_fingerprint` (Day 4A)
- persisted `Device`, `ConfigurationSnapshot` (+ `compute_raw_text_hash`),
  and persisted `Incident`/`IncidentUpsertOutcome`/`IncidentUpsertResult`
  domain objects; `DeviceRepository`/`ConfigurationSnapshotRepository`/
  `ConfigurationPolicyRepository`/`IncidentRepository`/`UnitOfWork`
  Protocols (interfaces only — no concrete implementation yet); explicit
  JSON serialization (`meta_rne.persistence.serialization`) for
  `NormalizedConfiguration`, `RequiredAclRule` tuples, and
  `PolicyViolationIncidentEvidence`; private SQLAlchemy declarative ORM
  models (`meta_rne.persistence.sqlalchemy.models`); the first Alembic
  migration (Slice 1 tables, the two-stage `devices`/
  `configuration_snapshots` foreign keys, CHECK constraints, and the
  partial unique OPEN-fingerprint index) (Day 4B1)
- unit tests, persistence/migration tests, and representative fixtures
- documentation corrections explicitly approved for Day 3A/3B/4A/4B1 (see
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

**Documentation corrections applied for Day 4B1:**

1. `docs/domain-model.md` §2/§14/§18 — `Device.first_seen_at`/`last_seen_at`
   renamed to `created_at`/`updated_at` (same fields, no behavior change;
   consistent with every other created/updated pair in this document).
2. `docs/domain-model.md` §4/§18 — `ConfigurationSnapshot.raw_text`/
   `raw_source_hash` renamed to `raw_config_text`/`raw_text_hash` (same
   fields; the hash field's name now states both what it hashes and that
   it's a hash).
3. `docs/domain-model.md` §12, `docs/architecture.md` §11.1,
   `docs/test-strategy.md` §9 — `IncidentRepository.find_open_by_fingerprint`
   removed from the documented port surface; the atomic
   `upsert_open_incident` is the only documented dedup mechanism, matching
   the Day 4B1 binding decision that no separate read-only lookup method is
   needed to prove it.
4. `docs/architecture.md` §8/§11.2 — the two remaining `raw_source_hash`/
   `raw_text` references updated to `raw_text_hash`/`raw_config_text` to
   match.

**Still prohibited**: concrete `DeviceRepository`/
`ConfigurationSnapshotRepository`/`ConfigurationPolicyRepository`
implementations (SQLAlchemy or in-memory), `seed_if_missing`,
`SnapshotAlreadyExistsError`, the concrete `IncidentRepository`
(SQLAlchemy or in-memory), the atomic `upsert_open_incident`
implementation, concurrency tests, the concrete `UnitOfWork`
(SQLAlchemy or in-memory), `ConfigIngestionService`, API ingestion
endpoints, `DriftDetector`, `RuleEngine`, telemetry, and React. Repository
implementations, seeding, and the concrete UnitOfWork are Day 4B2; the
atomic incident upsert and concurrency tests are Day 4B3. Both begin
against the domain model, architecture, and ports already documented,
with tests written first per the Development Rules above.
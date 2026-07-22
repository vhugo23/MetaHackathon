# Domain Model — Meta RNE Platform

**Status:** Draft — Day 1 consistency correction
**Date:** 2026-07-18
**Phase:** Planning / Architecture

Defines the domain model referenced by [architecture.md](./architecture.md)
(`domain` module). Scoped to the MVP in [product-spec.md](./product-spec.md).
Framework-independent per [CLAUDE.md](../CLAUDE.md) Rule 9: nothing here
refers to FastAPI, SQLAlchemy, PostgreSQL, or React. Entities are plain
data shapes; behavior is stateless domain services (Section 17). No event
sourcing, CQRS, or unnecessary abstraction — a "finding" that doesn't need
independent identity or history is a transient value, not a persisted
entity. No domain or detection service reads a clock or a repository
directly — time (`observed_at`) and context (`device_id`,
`source_snapshot_id`) are always explicit arguments, supplied once by
`application` (architecture.md Section 4.1).

---

## 1. Core Domain Entities

| Entity | Persisted? |
|---|---|
| Device | Yes |
| ConfigurationSnapshot | Yes (append-only; embeds its `NormalizedConfiguration` inline) |
| ConfigurationPolicy | Yes (seeded/read-mostly) |
| Incident | Yes |
| TelemetrySample | Yes (bounded recent history per device) |

| Derived / transient (no independent identity or repository) |
|---|
| NormalizedConfiguration — a field of `ConfigurationSnapshot`, not a separate row |
| ConfigurationViolation — computed by `PolicyEvaluator`, consumed immediately by `IncidentFactory` |
| Anomaly — computed by `RuleEngine`, consumed immediately by `IncidentFactory` |

| Value object (embedded, no identity) |
|---|
| Recommendation — embedded in `Incident` |

---

## 2. Device

**Purpose.** Identity anchor for everything else in this model.

**Identity.** `device_id` is an arbitrary caller-supplied string (A-04 —
e.g. `"spine-01"`). No surrogate key, no discovery. The caller is
responsible for using a consistent ID across requests for the same
device.

```
Device
├── device_id: string                      # identity
├── vendor: VendorType                     # from the most recent snapshot
├── current_snapshot_id: string | null     # latest ConfigurationSnapshot
├── baseline_snapshot_id: string | null    # FIRST successful snapshot; fixed at creation
├── created_at: timestamp                  # set once, at first successful submission
├── updated_at: timestamp                  # advances on every later submission
```

**Day 4B1 field-name correction:** `created_at`/`updated_at` replace an
earlier draft's `first_seen_at`/`last_seen_at` — the same "first write /
most recent write" timestamps, renamed for consistency with the rest of
this model's persisted entities (`ConfigurationSnapshot.submitted_at`
aside, every other created/updated pair in this document uses
`created_at`). No behavior changes: `created_at` is still set exactly once
and `updated_at` still advances on every later submission.

**`current_snapshot_id` vs. `baseline_snapshot_id`.** Both are set to the
same value on a device's first successful config submission.
`current_snapshot_id` updates on every later submission;
`baseline_snapshot_id` does **not** — later submissions never silently
replace it (product-spec A-02). `DriftDetector` (Section 8's sibling in
architecture.md Section 8) always compares `current` against `baseline`,
never against "whatever the previous submission happened to be."

---

## 3. Interface

**Purpose.** One routed interface within a `NormalizedConfiguration`. Not
independently addressable or persisted — only ever queried as part of a
device's config.

**ACL assignment.** ACLs are named once, in
`NormalizedConfiguration.acls`; an interface holds a reference by name
plus direction — this mirrors `ip access-group <name> in|out`.

```
Interface
├── name: string                      # e.g. "GigabitEthernet0/1"
├── description: string | null        # free-text interface description, if configured
├── ip_address: string | null
├── mtu: int | null
├── admin_state: AdminState           # "up" | "down"
├── acl_in: string | null             # name reference into NormalizedConfiguration.acls
├── acl_out: string | null            # name reference into NormalizedConfiguration.acls
```

Only inbound/outbound ACL assignment on routed interfaces is modeled (no
VLAN/port ACLs) — matches A-07's representative-subset scope.

---

## 4. ConfigurationSnapshot

**Purpose.** The immutable record of one configuration submission,
**including its normalized result** — normalization happens once, at
ingestion time, and is never redone for a normal read.

```
ConfigurationSnapshot
├── snapshot_id: string (uuid)
├── device_id: string                       # FK → Device
├── vendor: VendorType                      # as declared at submission time
├── raw_config_text: string                 # exact CLI text submitted
├── raw_text_hash: string                   # SHA-256 hex digest of raw_config_text
├── normalized_config: NormalizedConfiguration   # stored inline, at ingestion time
├── submitted_at: timestamp
```

**Day 4B1 field-name correction:** `raw_config_text`/`raw_text_hash`
replace an earlier draft's `raw_text`/`raw_source_hash` — same fields, no
behavior change, renamed so the hash field's name states both what it
hashes and that it's a hash (`compute_raw_text_hash(raw_config_text) ==
raw_text_hash`, a SHA-256 hex digest, never recomputed by a repository).

Immutable after creation (append-only). The raw text is retained
alongside its normalized result so a future re-parse (e.g., after an
adapter fix) can replay history without re-collecting data from the
caller — the MVP does not implement that replay, but the split costs
nothing and keeps `raw_config_text → NormalizedConfiguration` a pure,
replayable function (NFR-02).

**`submitted_at` is the only ingestion timestamp in this entire model**,
and it is supplied by `application`'s `Clock` port
(architecture.md Section 4.1) — one `observed_at = clock.now_utc()` call
per ingestion, never `ConfigurationSnapshot` reading a clock itself.
`NormalizedConfiguration` (Section 5) carries no timing field at all.

---

## 5. NormalizedConfiguration

**Purpose.** The canonical, vendor-neutral configuration shape (FR-02),
produced by exactly one adapter and embedded in exactly one
`ConfigurationSnapshot` (Section 4). Policy evaluation, drift detection,
and API reads all operate on this shape and only this shape.

```
NormalizedConfiguration
├── hostname: string
├── interfaces: [Interface]                 # Section 3
├── routing
│     ├── static_routes: [{ prefix: string, next_hop: string }]
│     └── bgp_neighbors: [{ neighbor_ip: string, remote_as: int }]
└── acls: [{ name: string, entries: [AclEntry] }]
      └── AclEntry: { sequence: int, action: AclAction, protocol: string, source: string, destination: string }
```

No identity of its own — it is a value embedded in its owning
`ConfigurationSnapshot`, not separately keyed or queried. **No timing
field.** Earlier drafts of this model included a `normalized_at`
timestamp here; it is removed. Normalization represents configuration
*content* only, and must be deterministic (invariant 10, Section 15):
`parse(raw_text)` is a pure function, so the same content parsed a second
apart or a year apart produces a structurally identical
`NormalizedConfiguration` — there is no field on this type that could ever
differ between those two runs. Ingestion time is `ConfigurationSnapshot.
submitted_at` (Section 4) and only that.

**`routing.static_routes` is deferred from the Day 3A implementation.**
The struct above documents the final normalized model; the Day 3A
`NormalizedRouting` type implements only `bgp_neighbors`. Static-route
parsing is not implemented this phase, and an empty field with no
populating logic and no dedicated type would be exactly the "shape
completeness" abstraction Day 3A was told to avoid — no
`NormalizedStaticRoute` type exists yet. `static_routes` returns once
static-route parsing is implemented on a later day.

---

## 6. ConfigurationPolicy

**Purpose.** A declarative statement of what a configuration must
contain, independent of any prior version — this is what lets "missing
required ACL" fire on a device's very first submission, unlike drift
(Section 8's sibling), which needs a baseline to compare against.

```
ConfigurationPolicy
├── policy_id: string      # stable, non-empty, caller/fixture-assigned — NOT a
│                           # generated UUID; e.g. "policy-acl-external-in";
│                           # this is the value copied into
│                           # ConfigurationViolation.rule_ref (Section 7)
│                           # (see Section 16's identifier-format rules)
├── applies_to: string                # a specific device_id, or "*" for all devices
├── required_acls: [RequiredAclRule]
│     └── RequiredAclRule: { acl_name: string, interface_name: string,
│                             direction: AclDirection, severity: Severity,
│                             recommendation: string }
├── created_at: timestamp
```

A `ConfigurationPolicy` represents a required-inbound-ACL rule set: each
`RequiredAclRule` names one ACL that must be assigned, inbound or
outbound, to one interface. A rule is satisfied only by an exact match:
the named ACL exists in `acls` **and** is assigned to the named interface
in the named direction. `severity`/`recommendation` are fixed at
authoring time (fixture data) and copied verbatim into any
`ConfigurationViolation` the rule produces (Section 7) — `PolicyEvaluator`
does not compute or look them up.

Policies are seeded fixture data (A-08); there is no authoring endpoint.
When both a device-specific and a `"*"` policy apply to the same device,
the union of both policies' rules is evaluated. **Day 3B's
`PolicyEvaluator` implements only exact `applies_to == device_id`
matching** — the Slice 1 policy applies only to `spine-01`, and `"*"`
wildcard resolution is not implemented this phase (a policy whose
`applies_to` is `"*"` simply matches no device yet). This mirrors Day
3A's precedent of implementing a documented shape's minimum slice first
(e.g. `NormalizedRouting.static_routes`, Section 5) rather than building
unused matching logic ahead of a test that needs it.

---

## 7. ConfigurationViolation

**Purpose.** Output of evaluating a `NormalizedConfiguration` against
applicable `ConfigurationPolicy` rules — the policy-based sibling of a
drift diff entry.

```
ConfigurationViolation
├── device_id: string
├── source_snapshot_id: string         # the ConfigurationSnapshot that was evaluated
├── rule_ref: string                  # copied from the matched RequiredAclRule's
│                                       # owning ConfigurationPolicy.policy_id — the
│                                       # same canonical name Incident.rule_ref uses
│                                       # (Section 10); NOT named policy_id here, to
│                                       # avoid two names for one concept
├── violation_type: ViolationType      # "MISSING_REQUIRED_ACL" | "TARGET_INTERFACE_MISSING"
├── affected_resource: string          # interface-centered, deterministic:
│                                       # "interface:{interface_name}:acl_in" (inbound) or
│                                       # "interface:{interface_name}:acl_out" (outbound) —
│                                       # e.g. "interface:GigabitEthernet0/1:acl_in";
│                                       # the expected ACL name is in `evidence`, not
│                                       # repeated here. This is a distinct format from
│                                       # Incident.affected_resource's existing
│                                       # "acl:{name}:{interface}:{direction}" convention
│                                       # (Section 11) — how (or whether) IncidentFactory
│                                       # maps one to the other is undecided, deferred to
│                                       # whichever day implements IncidentFactory
├── severity: Severity                 # copied from the matched RequiredAclRule
├── evidence: AclAssignmentEvidence     # Section 16 — structured, not an untyped dict
├── recommendation: string             # copied from the matched RequiredAclRule
├── detected_at: timestamp
```

```
AclAssignmentEvidence
├── expected_acl_name: string
├── actual_acl_name: string | null     # null when no ACL is assigned in that
│                                       # direction at all (or the interface
│                                       # itself does not exist)
├── interface_name: string
├── direction: AclDirection
```

**`violation_type` distinguishes two cases, both still MVP-scoped to "a
required inbound/outbound ACL assignment isn't as declared":**

- `TARGET_INTERFACE_MISSING` — the rule's `interface_name` does not exist
  in `config.interfaces` at all. `evidence.actual_acl_name = null`.
- `MISSING_REQUIRED_ACL` — the interface exists but the assignment is
  wrong: the ACL is entirely absent (`evidence.actual_acl_name = null`),
  present but unassigned in that direction (`evidence.actual_acl_name =
  null`), or a *different* ACL is assigned in that direction
  (`evidence.actual_acl_name` = that ACL's name). A missing interface is
  never silently treated as satisfying the rule — it always produces
  `TARGET_INTERFACE_MISSING`, never a false "no violation."

Produced by:

```
PolicyEvaluator.evaluate(
    device_id,
    source_snapshot_id,
    observed_at,
    config,
    policies
) -> list[ConfigurationViolation]
```

(architecture.md Section 7), one per unsatisfied rule. `device_id`,
`source_snapshot_id`, and `observed_at` are supplied by the caller as
plain arguments — the evaluator never fetches or reads any of them
itself, which is what keeps it framework-independent and deterministic
(NFR-02/NFR-03: same five inputs in, same violations out, no hidden
repository or clock access). Every violation carries the given
`source_snapshot_id` and `detected_at = observed_at` — never a value the
evaluator generated. No violation carries a generated ID of its own
(Section 16): it is a transient value, not a persisted entity.

**Not persisted independently** — it is a transient value passed straight
to `IncidentFactory` within the same request. Zero violations → zero
incidents from this path. **`IncidentFactory.build_candidate` (Day 4A)
for a `POLICY_VIOLATION` finding copies `device_id`, `rule_ref`,
`affected_resource`, `severity`, and `recommendation` directly from the
violation, verbatim, and does not recompute or template any of them.**
`ConfigurationViolation.affected_resource`'s interface-centered format
(above) is not translated to a different format — it *is*
`IncidentCandidate.affected_resource`/`Incident.affected_resource`; there
is only one `affected_resource` convention for policy violations, not two
(this corrects an earlier draft's two-format design, Section 13).
`evidence` is **remapped, not copied as-is**: `IncidentFactory` builds a
`PolicyViolationIncidentEvidence` (Section 16) from
`ConfigurationViolation.evidence` (`AclAssignmentEvidence`) plus the
violation's own `violation_type` and `source_snapshot_id`, so no
information the violation carried is lost. `IncidentCandidate` also
carries `observed_at = violation.detected_at`, copied directly — the
factory never reads a clock or accepts a separate timestamp argument.

```
IncidentCandidate                        # Day 4A, pre-fingerprint, pre-persistence
├── device_id: string
├── source: IncidentSource                # POLICY_VIOLATION only, Day 4A
├── rule_ref: string
├── affected_resource: string             # copied verbatim from the violation
├── severity: Severity
├── evidence: PolicyViolationIncidentEvidence
├── recommendation: string                # copied verbatim, Section 13
├── observed_at: timestamp                # = violation.detected_at
```

```
PolicyViolationIncidentEvidence
├── source_snapshot_id: string
├── violation_type: ViolationType
├── expected_acl_name: string
├── actual_acl_name: string | null
├── interface_name: string
├── direction: AclDirection
```

---

## 8. TelemetrySample

**Purpose.** One point-in-time telemetry reading (FR-05), submitted by a
caller or the simulator. Not exercised by the first vertical slice.

```
TelemetrySample
├── device_id: string
├── sampled_at: timestamp
├── cpu_utilization_pct: float            # [0, 100]
├── memory_utilization_pct: float         # [0, 100]
├── interface_error_rate: float           # aggregate, not per-interface
├── interface_states: [{ name: string, oper_state: LinkState }]
├── bgp_sessions: [{ neighbor_ip: string, state: BgpState }]
```

Per-interface and per-neighbor granularity is required because
RULE-LINK-FLAP and RULE-BGP-DOWN are meaningless without knowing *which*
interface or neighbor changed. `interface_error_rate` stays aggregate
since no MVP rule needs per-interface granularity.

The platform retains a **bounded recent history** per device (Section 12
— `TelemetryRepository.get_recent`), not only the latest sample, because
RULE-LINK-FLAP needs the last 60 seconds of transitions.

---

## 9. Anomaly

**Purpose.** Output of evaluating recent `TelemetrySample`s against the
rule engine (FR-06) — the telemetry-based sibling of `ConfigurationViolation`.

```
Anomaly
├── device_id: string
├── rule_id: RuleId                    # "RULE-CPU-HIGH" | "RULE-LINK-FLAP" | "RULE-BGP-DOWN"
├── evidence: RuleEvidence             # rule-specific shape
├── detected_at: timestamp
```

`RuleEvidence` shape per rule:

- `RULE-CPU-HIGH` → `{ samples: [{timestamp, cpu_utilization_pct}] }` (2 consecutive samples > 90%)
- `RULE-LINK-FLAP` → `{ interface_name, transitions: [{timestamp, oper_state}] }` (> 3 transitions, i.e. ≥ 4, within 60s)
- `RULE-BGP-DOWN` → `{ neighbor_ip, state, previous_state }` (`previous_state` not in `{Idle, Active}`, `state` in `{Idle, Active}`)

Transient, like `ConfigurationViolation` — produced and consumed within
one rule-evaluation pass, never independently persisted. `detected_at` is
always the `observed_at` argument `RuleEngine.evaluate` was called with
(Section 17), never a value the engine reads from a clock itself.

---

## 10. Incident

**Purpose.** The single unified record of "something the operator should
know about" — from a policy violation, a drift finding, or a telemetry
anomaly. What `GET /incidents` returns.

```
Incident
├── incident_id: string (uuid)
├── fingerprint: string                # dedup key, Section 11
├── device_id: string
├── source: IncidentSource             # "POLICY_VIOLATION" | "DRIFT" | "ANOMALY"
├── rule_ref: string                   # THE canonical source-rule/policy reference field
├── affected_resource: string          # e.g. "interface:GigabitEthernet0/1:acl_in" (policy
│                                       # violations); copied verbatim from the triggering
│                                       # finding, Section 7/17
├── severity: Severity                 # "Critical" | "High" | "Medium" | "Low"
├── status: IncidentStatus             # "OPEN" | "RESOLVED" reachable as of Day 7A;
│                                       # "ACKNOWLEDGED" remains a dormant compatibility
│                                       # state with no public transition into it
├── evidence: object                   # finding-specific detail ONLY — see below
├── recommendation: string             # Section 13 — plain string, Day 4A; a richer
│                                       # Recommendation{summary, details} object and
│                                       # template generation are deferred
├── created_at: timestamp              # first detection; never changes again
├── last_seen_at: timestamp            # most recent (re)detection; advances only while
│                                       # OPEN, on a dedup match (Section 11) — resolution
│                                       # (Section 10.1 below) never touches it
├── occurrence_count: int              # starts at 1, increments on dedup match; resolution
│                                       # never touches it
├── updated_at: timestamp              # Day 7A — the most recent *persisted mutation* of
│                                       # any kind to this row (creation, a dedup update, or
│                                       # an explicit resolution); distinct from
│                                       # last_seen_at, which is detection-only
├── resolved_at: timestamp | null      # Day 7A — null while OPEN; set once, to the exact
│                                       # Clock value captured at resolution, when RESOLVED
```

**Timestamp invariants (Day 7A):**

```
created_at <= last_seen_at <= updated_at
resolved_at <= updated_at   (when resolved_at is not null)
```

**State invariants (Day 7A):**

- `status == "OPEN"` requires `resolved_at` is `null`.
- `status == "RESOLVED"` requires `resolved_at` is populated.
- `status == "ACKNOWLEDGED"` also requires `resolved_at` is `null` — it
  remains a dormant compatibility state (the enum member and its database
  CHECK constraint exist, unchanged since Day 4A/4B1) with no public
  transition into or out of it in the Day 7A lifecycle.

**`rule_ref` is the one consistent field name, used the same way in every
document, for "which rule or policy caused this."** It is populated from:
`ConfigurationViolation.policy_id` (policy violations), `Anomaly.rule_id`
(anomalies), or a drift field path (drift). Those source-level fields keep
their own names (`policy_id` is a real foreign key; `rule_id` is a fixed
enum value) — `rule_ref` is the *Incident's* copy, and it is what the API
and every document refer to.

**Evidence carries no field that duplicates a top-level `Incident`
field.** `device_id` and `rule_ref` already identify "which device, which
rule" — repeating them inside `evidence` was a modeling redundancy in an
earlier draft of this document and is corrected here. `evidence.
source_snapshot_id` is populated directly from `ConfigurationViolation.
source_snapshot_id` (Section 7) by `IncidentFactory.build_candidate` — not
looked up from a repository, since the violation already carries it. For
the vertical slice (missing required ACL):

```json
{
  "acl_name": "ACL-EXTERNAL-IN",
  "interface_name": "GigabitEthernet0/1",
  "direction": "in",
  "violation_type": "MISSING_REQUIRED_ACL",
  "source_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**Severity** is always one of `Critical | High | Medium | Low` (Title
Case, fixed set). A missing required ACL is `Medium`.

**Status** is always one of `OPEN | ACKNOWLEDGED | RESOLVED`
(SCREAMING_SNAKE_CASE, fixed set). Every finding starts `OPEN`; as of
Day 7A, `RESOLVED` is reachable via `Incident.resolve(at)` below.
`ACKNOWLEDGED` remains dormant — nothing transitions an incident into it
(Section 14).

**Timestamps** are ISO-8601 UTC with a `Z` suffix everywhere in this
model and in the API (e.g. `"2026-07-18T10:00:00Z"`) — no other timestamp
format appears in any document.

**`Incident.resolve(at)` (Day 7A) — the one lifecycle transition.**

```
Incident.resolve(at: timestamp) -> Incident
```

- `OPEN -> RESOLVED`: `status` becomes `RESOLVED`; `resolved_at` and
  `updated_at` both become exactly `at` — one captured value assigned to
  both, never two separate reads. Every other field (`fingerprint`,
  `device_id`, `rule_ref`, `affected_resource`, `severity`, `evidence`,
  `recommendation`, `created_at`, `last_seen_at`, `occurrence_count`)
  is unchanged.
- `at` must be timezone-aware UTC and must not precede the incident's
  current `updated_at` — checked against `updated_at`, not `last_seen_at`,
  since an `OPEN` incident may legally already have
  `last_seen_at < updated_at`; a `last_seen_at`-only check would let a
  resolution move `updated_at` backward. A violation is rejected
  (`ValueError`), and the incident is returned/left completely unchanged.
- Already `RESOLVED` is a **true no-op**: the incident is returned
  unchanged, before `at` is validated at all — an invalid or stale `at`
  never raises for an already-resolved incident.
- The dormant `ACKNOWLEDGED` status does not silently resolve — calling
  `resolve(at)` on an `ACKNOWLEDGED` incident raises, since accepting it
  would bypass `ACKNOWLEDGED`'s own (still nonexistent) transition
  entirely.
- No generic state-machine abstraction was introduced — this is the one
  method for the one transition the MVP has.

---

## 11. Incident Deduplication

**The same unresolved finding must never create two `OPEN` incidents.**
Each finding is reduced to a deterministic **fingerprint** — a SHA-256
digest, never a delimiter-joined string:

```
fingerprint = sha256_hex(canonical_json([device_id, source, rule_ref, affected_resource]))
```

1. Serialize the **ordered array** `[device_id, source, rule_ref,
   affected_resource]` as canonical JSON: array order (not object-key
   order) gives deterministic ordering for free, and serialization uses
   no insignificant whitespace (`separators=(",", ":")`).
2. Encode as UTF-8.
3. Compute SHA-256.
4. Store the lowercase 64-character hex digest.

This is deliberately **not** `f"{device_id}|{source}|{rule_ref}|
{affected_resource}"`: any of those four values could itself legitimately
contain `|`, `:`, quotes, or non-ASCII characters (an `acl_name` or
`interface_name` is caller/vendor-supplied text, not a value this
platform controls the character set of), and a naive delimiter join would
let two *different* logical tuples collide on the same joined string.
JSON array serialization has no such collision: each element's boundary
is unambiguous regardless of its content. A named unit test
(`test_compute_fingerprint__delimiter_and_unicode_values__remain_
unambiguous`, test-strategy.md Section 13) proves this directly.

`affected_resource` identifies the specific thing within the device the
finding is about, so two different missing ACLs on the same device (or
the same rule on two different interfaces) get different fingerprints:

| Source | `affected_resource` |
|---|---|
| `POLICY_VIOLATION` | `"interface:{interface_name}:acl_in"` or `"...acl_out"` — copied verbatim from `ConfigurationViolation.affected_resource` (Section 7), not a separate incident-level format |
| `ANOMALY` (`RULE-CPU-HIGH`) | `"device"` |
| `ANOMALY` (`RULE-LINK-FLAP`) | `"interface:{interface_name}"` |
| `ANOMALY` (`RULE-BGP-DOWN`) | `"bgp-neighbor:{neighbor_ip}"` |
| `DRIFT` | `"{field_path}:{entity_name}"`, e.g. `"acls.removed:ACL-EXTERNAL-IN"` |

**The invariant is stronger than "usually doesn't duplicate": no two
`OPEN` incidents may ever share a fingerprint, including under concurrent
`upsert_open_incident` calls.** A find-then-save pattern (read, decide,
write) is explicitly insufficient — two concurrent calls can both read
"not found" before either writes. Enforcement is at two levels: the
fingerprint is computed deterministically before the call (so intent is
always "create-or-update this exact fingerprint," never a bare insert),
and a PostgreSQL partial unique index on `(fingerprint) WHERE status =
'OPEN'` makes the database itself refuse a second row even under a race
(architecture.md Section 11 has the full mechanism, including how a
single statement reports which branch fired). **This guarantee concerns
simultaneous `upsert_open_incident` operations at the repository level —
it does not, by itself, mean Slice 1 has proven two full concurrent HTTP
ingestion requests behave correctly end-to-end; that would require its
own integration test, which is not currently in Slice 1's suite
(test-strategy.md Section 9's concurrency test exercises the repository
directly).**

```
IncidentRepository.upsert_open_incident(candidate, fingerprint, observed_at)
    -> IncidentUpsertResult { incident: Incident, outcome: IncidentUpsertOutcome }

IncidentUpsertOutcome := "CREATED" | "UPDATED"
```

- **No existing `OPEN` row** → insert: `created_at = last_seen_at =
  observed_at`, `occurrence_count = 1`, `outcome = "CREATED"`.
- **An `OPEN` row already exists** (found before the call, or via the
  database's conflict branch during a race) → update in place:
  `last_seen_at = observed_at`, `occurrence_count += 1`, `evidence`
  refreshed, `outcome = "UPDATED"`. No second row.

`application` computes `incidents_created`/`incidents_updated`
(architecture.md Section 10.1) by tallying `.outcome` across the calls it
made in one request — never by inspecting `occurrence_count` and never by
issuing a separate lookup.

The in-memory test double must provide the **same observable contract**,
including under concurrent calls, guarded by a single critical section —
test-strategy.md Section 9 runs one conformance test against both
implementations to prove this. There is deliberately no plain `save()` on
`IncidentRepository` (Section 12): `upsert_open_incident` is the only
write path, so no calling code can reintroduce a find-then-save race.

Dedup is scoped to `OPEN` incidents (A-09): once an incident is resolved
(`Incident.resolve`, Section 10, implemented Day 7A), a recurrence of the
same fingerprint starts a **new** incident rather than reopening the old
one — there is still no reopen workflow, but this is no longer merely a
stated-for-later rule: the partial unique index
(`ux_incidents_open_fingerprint`, `WHERE status = 'OPEN'`) already excludes
a `RESOLVED` row, so `upsert_open_incident`'s existing `ON CONFLICT` target
simply doesn't see one, and a fresh `INSERT` proceeds — a new
`incident_id`, `occurrence_count: 1`, and its own `created_at`/
`last_seen_at`/`updated_at`. The historical `RESOLVED` row is left
completely unchanged, and the new `OPEN` row then deduplicates further
recurrence exactly as any other `OPEN` incident does (no third row). Proven
by real-PostgreSQL tests (test-strategy.md Section 9) — no change to
`upsert_open_incident` or the index itself was required for this.

---

## 12. Repository Boundaries

Repositories participating in one ingestion are reached through a single
`UnitOfWork` (architecture.md Section 11.1), not constructed
independently — `application` opens one per operation, and its
`commit`/`rollback` bounds every write inside it:

```
UnitOfWork
  devices: DeviceRepository
  configuration_snapshots: ConfigurationSnapshotRepository
  configuration_policies: ConfigurationPolicyRepository
  incidents: IncidentRepository
  commit() -> None
  rollback() -> None
```

`TelemetryRepository` (FR-05, later slice) is not part of this grouping —
telemetry ingestion is a separate operation with its own transaction.

All repository operation names are Python-style snake_case, consistent
with the rest of this stack (ADR-0002):

```
DeviceRepository
  get_by_id(device_id) -> Device | None
  save(device) -> None
      # upsert by device_id; every rejected lifecycle transition (vendor
      # change, created_at change, updated_at regression, replacing a set
      # baseline_snapshot_id, clearing a set current_snapshot_id, or a
      # non-null snapshot reference that doesn't exist) raises
      # DeviceConflictError and leaves the stored Device unchanged
      # (Day 4B2 binding decision — no list(), no silent preservation)

ConfigurationSnapshotRepository            # append-only
  get_by_id(snapshot_id) -> ConfigurationSnapshot | None
  add(snapshot) -> None                    # includes normalized_config inline
      # duplicate snapshot_id -> SnapshotAlreadyExistsError;
      # unknown device_id -> ReferencedDeviceNotFoundError (Day 4B2 —
      # no get_current_for_device/get_baseline_for_device on this port)

ConfigurationPolicyRepository              # read-mostly, seeded
  get_applicable_to_device(device_id) -> tuple[ConfigurationPolicy, ...]
      # exact applies_to == device_id matching only — no "*" wildcard
      # behavior (Day 3B/Day 4B2; no list())
  seed_if_missing(policies: tuple[ConfigurationPolicy, ...]) -> None
      # one call is one all-or-nothing operation; semantic equivalence
      # (applies_to + required_acls only, not created_at) is a no-op,
      # differing semantic content raises PolicySeedConflictError
      # (Day 4B2 binding decision)

IncidentRepository
  get_by_id(incident_id) -> Incident | None
  list_all() -> tuple[Incident, ...]
      # returns every stored Incident, ordered ascending by created_at then
      # incident_id (a stable, deterministic order — never DB-insertion
      # order). No filter parameter: `device_id`/`severity` filtering is
      # deferred to the application/API layer over this full result (Day
      # 4B3 binding decision — see CLAUDE.md "Documentation corrections
      # applied for Day 4B3"; domain/ports.py has declared list_all() with
      # no filter since Day 4B1, and this document previously described a
      # filtered list() that was never implemented)
  # Day 4B1 binding decision: no find_open_by_fingerprint on this port —
  # dropped from the public surface. The atomic upsert below is the
  # deduplication mechanism; a separate read-only lookup method is not
  # needed to prove it.
  upsert_open_incident(candidate: IncidentCandidate, fingerprint: str, observed_at: timestamp)
      -> IncidentUpsertResult
      # THE only write path for an Incident: atomic create-or-update,
      # Section 11. There is no plain save() — see Section 11 for why.
      # Rejects (ValueError, before any mutation): a fingerprint that does
      # not equal compute_fingerprint(candidate.device_id, candidate.source,
      # candidate.rule_ref, candidate.affected_resource); an observed_at
      # that does not equal candidate.observed_at; a candidate.source other
      # than POLICY_VIOLATION (the only source this phase's evidence
      # serialization supports); or an empty/whitespace-only generated
      # incident_id. An observed_at older than the existing OPEN incident's
      # last_seen_at is a stale observation (ValueError, no mutation) —
      # equal timestamps are accepted and still increment
      # occurrence_count. incident_id is generated by the repository via an
      # injected `incident_id_factory: Callable[[], str]` (production
      # default: str(uuid4())) only when creating a new row; an update
      # preserves the existing incident_id (Day 4B3 binding decisions).

TelemetryRepository
  save(device_id, sample) -> None
  get_latest(device_id) -> TelemetrySample | None
  get_recent(device_id, since: timestamp) -> list[TelemetrySample]   # bounded recent history
```

Production implementations are SQLAlchemy repositories over PostgreSQL;
in-memory implementations of these same interfaces exist only as fast
test doubles (test-strategy.md Section 9) — never in production
(product-spec NFR-06).

**Deliberately no repository for** `NormalizedConfiguration` (embedded in
its snapshot, Section 4), `ConfigurationViolation`/`Anomaly` (transient,
Sections 7/9 — persisting them independently would need a second
lifecycle that duplicates what `Incident` already provides), or
`Recommendation` (a plain string on `Incident` for now, Section 13 — not
yet its own value object).

`TelemetryRepository`'s in-memory test double retains samples with
`sampled_at` within the last 5 minutes, or the last 100 samples per
device, whichever is smaller — comfortably more than RULE-LINK-FLAP's
60-second window while bounding memory. The production (PostgreSQL)
implementation may retain longer, since disk is cheap; `get_recent`'s
`since` parameter makes the retention window a query-time concern, not a
storage-time one.

---

## 13. Recommendation

**Purpose.** A human-readable remediation string on an incident (FR-07).

**Day 4A: `Incident.recommendation` (and `IncidentCandidate.recommendation`,
Section 7) is a plain `string`, copied verbatim from the triggering
finding** — for a policy violation, `IncidentFactory.build_candidate`
copies `ConfigurationViolation.recommendation` (itself copied from the
matched `RequiredAclRule.recommendation`, Section 6) without recomputing
or reformatting it, e.g. `"Assign ACL-EXTERNAL-IN inbound to
GigabitEthernet0/1"`.

A richer `Recommendation{summary, details}` value object, generated by a
template function keyed on `Incident.source` + `rule_ref`, is **deferred**
— it is not needed until a finding type without its own
caller-authored recommendation string (e.g. `ANOMALY`, `DRIFT`) exists.
An earlier draft of this document showed `IncidentFactory` rewriting the
copied string into a differently-worded, device-specific summary (e.g.
"Restore ACL ACL-EXTERNAL-IN (inbound) on GigabitEthernet0/1, spine-01");
that behavior is not implemented and the worked example in Section 18 has
been corrected to match the verbatim-copy contract actually built.

---

## 14. Entity Lifecycle and State Transitions

| Entity | Lifecycle |
|---|---|
| `Device` | `created_at` set once. `updated_at`/`current_snapshot_id` update on every config submission. `baseline_snapshot_id` set once, on the first submission, and never again. No status field; never removed. |
| `ConfigurationSnapshot` | Created once, immutable, includes its normalized config from creation. Never updated or deleted. |
| `ConfigurationPolicy` | No status field — a policy has no lifecycle states to be in. It is seeded at startup (idempotently, by `policy_id`, Section 12) and never created, edited, or deleted at runtime in the MVP; its existence in the repository is its only observable state. |
| `ConfigurationViolation` | Transient: detected → immediately converted into an `Incident` finding within the same request. |
| `Anomaly` | Same as `ConfigurationViolation`. |
| `Incident` | Created `OPEN`, `occurrence_count = 1`, `updated_at = created_at`, `resolved_at = null`. Each dedup match (Section 11) updates `last_seen_at`/`occurrence_count`/`evidence`/`updated_at` in place, never `resolved_at`. `Incident.resolve(at)` (Day 7A) transitions `OPEN -> RESOLVED`, setting `resolved_at = updated_at = at`; already-`RESOLVED` is a no-op. `ACKNOWLEDGED` remains a dormant compatibility state with no triggering endpoint — see architecture.md Section 11.3. There is no reopen transition back to `OPEN`. Never deleted. |
| `TelemetrySample` | Created once per submission; retained within the bounded recent-history window (Section 12), then aged out. |

---

## 15. Invariants and Validation Rules

1. **Device identity is immutable.** A new `device_id` always means a new `Device`.
2. **A ConfigurationSnapshot is immutable after creation**, including its embedded `normalized_config`.
3. **`NormalizedConfiguration.acls` names are unique within one config.** An `Interface.acl_in`/`acl_out` reference must resolve to a name present in that same config's `acls`, or be `null`; a reference to a nonexistent ACL name is a parser-level `ParseError` (surfaced at the API boundary as HTTP 422, `code: "configuration_parse_error"` — architecture.md Section 5.1/12), not a valid normalized state.
4. **A `RequiredAclRule` is satisfied only by exact match** on `acl_name` + `interface_name` + `direction`.
5. **Every `Incident` has exactly one triggering finding type per fingerprint match.** There is no manual incident-creation path.
6. **Severity, status, and source are drawn from fixed enums only** (Section 16) — no free-text values.
7. **`TelemetrySample.cpu_utilization_pct` and `memory_utilization_pct` are in `[0, 100]`**; out-of-range values are a schema/validation error at ingestion, not stored.
8. **`Device.baseline_snapshot_id` is set exactly once** (invariant 1's corollary for baselines) — DriftDetector always compares against it, never against "the previous submission."
9. **The same unresolved finding never produces two `OPEN` incidents, including under concurrent requests** (Section 11) — enforced by `upsert_open_incident`'s atomicity (application-level fingerprinting plus a PostgreSQL partial unique index), not by a find-then-save sequence.
10. **`NormalizedConfiguration` is a pure, deterministic function of `raw_text` content alone.** Parsing identical `raw_text` at two different times produces structurally identical `NormalizedConfiguration` values — it carries no timestamp or other ingestion-time metadata (Section 5), so there is nothing in it that time could change. `DriftDetector.compare` and `PolicyEvaluator.evaluate` therefore compare configuration content only, never ingestion metadata.

---

## 16. Value Objects and Enums

```
VendorType         := "cisco-ios-xe" | "arista-eos"
AdminState          := "up" | "down"
AclAction           := "permit" | "deny"
AclDirection        := "in" | "out"
ViolationType       := "MISSING_REQUIRED_ACL" | "TARGET_INTERFACE_MISSING"  # Section 7
IncidentSource      := "POLICY_VIOLATION" | "DRIFT" | "ANOMALY"
Severity            := "Critical" | "High" | "Medium" | "Low"
IncidentStatus      := "OPEN" | "ACKNOWLEDGED" | "RESOLVED"    # only "OPEN" is reachable in the MVP
IncidentUpsertOutcome := "CREATED" | "UPDATED"                 # Section 11, IncidentRepository.upsert_open_incident
RuleId              := "RULE-CPU-HIGH" | "RULE-LINK-FLAP" | "RULE-BGP-DOWN"
LinkState           := "up" | "down"
BgpState            := "Idle" | "Connect" | "Active" | "OpenSent" | "OpenConfirm" | "Established"
```

`BgpState` carries the full RFC 4271 state set (not a simplified subset)
because RULE-BGP-DOWN's trigger — "transitions from a non-down state to
Idle or Active" — needs to distinguish *any* non-down predecessor state,
not just "Established."

**`VendorType` is an internal-only enum.** It is the type of
`Device.vendor` and `ConfigurationSnapshot.vendor` — fields populated
*after* a vendor string has already resolved successfully through
`AdapterRegistry.resolve` (architecture.md Section 5). It is **never**
the type of the raw `vendor` field on an incoming `POST
/devices/{id}/config` request: that field is validated at the HTTP schema
layer only as a non-empty string (product-spec FR-01/NFR-05) — a
Pydantic `Literal`/enum there would reject an unsupported-but-well-formed
vendor with FastAPI's own generic request-validation error, decided
*before* any domain code runs, when the correct outcome is HTTP 422 with
`code: "unsupported_vendor"`, decided by `AdapterRegistry.resolve`, not by
the schema (architecture.md Section 12, Day 5B — both paths happen to
share the same HTTP status now, but only one carries the distinguishing
`unsupported_vendor` code).

**Both `VendorType` values are now backed by real adapters as of Day 8A.**
`"cisco-ios-xe"` and `"arista-eos"` both resolve to a registered
`VendorConfigAdapter` in the production `AdapterRegistry`
(architecture.md Section 18) — `arista-eos` is no longer merely a
documented-but-unimplemented enum member. No Arista-specific type was
added anywhere downstream of the adapter boundary: `NormalizedConfiguration`
(Section 5) is exactly as vendor-neutral as it was before Day 8A —
`AristaAdapter.parse` returns the identical type `CiscoAdapter.parse`
does, and nothing in `domain`, `detection`, or `persistence` branches on
`VendorType`. `spine-01`'s `GigabitEthernet0/1` and `leaf-02`'s
`Ethernet1` are legitimately different interface-name strings (real,
vendor-authentic naming conventions, not an artificial distinction) —
this model does not, and should not, try to unify them into one canonical
interface-naming scheme; only the `NormalizedInterface` *shape* is
shared, never the vendor's own naming convention. Cross-vendor
equivalence between a Cisco and an Arista incident for the "same" logical
condition is therefore always a **semantic** claim (same `violation_type`,
same `expected_acl_name`, same `direction`, same `severity`) — never a
claim that the two `Incident`/`NormalizedConfiguration` objects are equal,
since `device_id`, interface names, `rule_ref`, `affected_resource`,
`incident_id`, and `fingerprint` all legitimately differ. **`Device`
vendor-identity immutability (Section 2) is unaffected**: a `Device`'s
`vendor` still cannot change after creation — the repository layer still
raises `DeviceConflictError` for any attempted change (architecture.md
Section 18), so the two demo devices remain necessarily distinct
`device_id`s under two different vendors, never one device migrated
between vendors.

**Identity formats are not uniform** — each identity type has a specific,
binding rule:

| Identity | Format | Who assigns it |
|---|---|---|
| `device_id` | Caller-supplied, non-empty string (A-04) | The caller, e.g. `"spine-01"` |
| `policy_id` | **Stable, non-empty string — not required to be a UUID** | Fixture/seed data, e.g. `"policy-acl-external-in"` |
| `snapshot_id` | Generated UUID string | The platform, at ingestion |
| `incident_id` | Generated UUID string | The platform, at creation |

None of these are wrapped value-object types — wrapping every ID in its
own class would be exactly the unnecessary abstraction this document
avoids; the table above is the whole contract. `policy_id` is
deliberately **not** a UUID: it is a stable, human-readable name chosen
by whoever writes the seed fixture (Section 6), so that seeding is
idempotent by identity (`seed_if_missing`, Section 12) — a UUID
regenerated on every app restart would defeat that idempotency entirely.
`AclAssignmentEvidence` (Section 7) and `PolicyViolationIncidentEvidence`
(Section 7, Day 4A) are this model's value objects: no identity, no
repository, always embedded. `Recommendation` is deferred (Section 13) —
`Incident.recommendation` is a plain string, not yet a value object.

---

## 17. Domain Services

Stateless; take entities/values in, return entities/values out; storage
is the caller's (`application` layer's) job.

| Service | Signature | Responsibility |
|---|---|---|
| `VendorConfigAdapter` (Cisco, Arista) | `parse(raw_text) -> NormalizedConfiguration \| ParseError` | FR-02 |
| `PolicyEvaluator` | `evaluate(device_id, source_snapshot_id, observed_at, config, policies: list[ConfigurationPolicy]) -> list[ConfigurationViolation]` | FR-03, Section 6–7 |
| `DriftDetector` | `compare(baseline: NormalizedConfiguration, current: NormalizedConfiguration) -> DriftReport` | FR-04, implemented Day 9 — Section 20 |
| `RuleEngine` | `evaluate(observed_at, recent_samples: list[TelemetrySample]) -> list[Anomaly]` | FR-06, later slice |
| `IncidentFactory` | `build_candidate(finding) -> IncidentCandidate` (Section 11's fields, pre-fingerprint) | FR-07 |
| `compute_fingerprint` | `(device_id, source, rule_ref, affected_resource) -> str` (SHA-256 hex, Section 11) | Section 11 |
| `Clock` (deferred past Day 5A) | `now_utc() -> timestamp` — `SystemClock` (production) / `FixedClock` (tests) | architecture.md Section 4.1; `ConfigIngestionService` instead takes `observed_at` directly on `IngestConfigurationCommand` this phase |

`IncidentFactory` is the single place that reshapes a finding into an
`IncidentCandidate` — kept in one place rather than scattered across
`PolicyEvaluator`, `RuleEngine`, and `DriftDetector`. For `POLICY_VIOLATION`
(Day 4A), it copies `device_id`, `rule_ref`, `affected_resource`,
`severity`, `recommendation`, and `observed_at` (= `detected_at`) verbatim
from the violation and remaps `evidence` into
`PolicyViolationIncidentEvidence` (Section 7) — it does not compute a
severity or recommendation template itself; that becomes relevant once
`ANOMALY`/`DRIFT` findings (which carry no ready-made recommendation
string of their own) are implemented. `PolicyEvaluator.evaluate` and
`RuleEngine.evaluate` both
take their timestamp (`observed_at`) as an explicit argument rather than
calling `Clock` themselves — `application` supplies that value exactly
once per operation, which is what lets both functions stay pure and
framework-independent (NFR-02) while every finding they return still
carries accurate, consistent context. As of Day 5A, `ConfigIngestionService`
takes that value directly from `IngestConfigurationCommand.observed_at`
(caller-supplied) rather than reading it from an injected `Clock` port —
see architecture.md Section 4.1's Day 5A correction.

---

## 18. Example Objects — First Vertical Slice

**Every timestamp below is the same value, `2026-07-18T10:00:00Z`, by
design.** `ConfigIngestionService` receives exactly one `observed_at` on
`IngestConfigurationCommand` (architecture.md Section 4/4.1) for the whole
operation and passes it everywhere a timestamp is needed —
`ConfigurationSnapshot.submitted_at`, `ConfigurationViolation.detected_at`,
`Incident.created_at`, `Incident.last_seen_at`, and the stdout log's
`timestamp` all come from that one call, not from separate clock reads
that could each drift by a few milliseconds.

```json
// Device, after the single Cisco submission
{
  "device_id": "spine-01",
  "vendor": "cisco-ios-xe",
  "created_at": "2026-07-18T10:00:00Z",
  "updated_at": "2026-07-18T10:00:00Z",
  "current_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "baseline_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

```json
// ConfigurationPolicy (seeded)
{
  "policy_id": "policy-acl-external-in",
  "applies_to": "spine-01",
  "required_acls": [
    {
      "acl_name": "ACL-EXTERNAL-IN",
      "interface_name": "GigabitEthernet0/1",
      "direction": "in",
      "severity": "Medium",
      "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1"
    }
  ],
  "created_at": "2026-07-18T09:00:00Z"
}
```

```json
// ConfigurationSnapshot 3fa85f64-5717-4562-b3fc-2c963f66afa6 (normalized_config embedded)
{
  "snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "device_id": "spine-01",
  "vendor": "cisco-ios-xe",
  "raw_config_text": "hostname spine-01\ninterface GigabitEthernet0/1\n ip address 10.0.0.1 255.255.255.252\n! (no ip access-group applied)\n",
  "raw_text_hash": "9f...64-char-sha256-hex...",
  "normalized_config": {
    "hostname": "spine-01",
    "interfaces": [
      { "name": "GigabitEthernet0/1", "ip_address": "10.0.0.1/30", "mtu": null, "admin_state": "up", "acl_in": null, "acl_out": null }
    ],
    "routing": { "bgp_neighbors": [] },
    "acls": []
  },
  "submitted_at": "2026-07-18T10:00:00Z"
}
```

`normalized_config` has no timestamp of its own — `submitted_at` above,
on the snapshot, is the only ingestion time recorded anywhere for this
config.

```json
// ConfigurationViolation (transient)
{
  "device_id": "spine-01",
  "source_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "rule_ref": "policy-acl-external-in",
  "violation_type": "MISSING_REQUIRED_ACL",
  "affected_resource": "interface:GigabitEthernet0/1:acl_in",
  "severity": "Medium",
  "evidence": {
    "expected_acl_name": "ACL-EXTERNAL-IN",
    "actual_acl_name": null,
    "interface_name": "GigabitEthernet0/1",
    "direction": "in"
  },
  "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
  "detected_at": "2026-07-18T10:00:00Z"
}
```

```json
// Resulting Incident
{
  "incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "fingerprint": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "device_id": "spine-01",
  "source": "POLICY_VIOLATION",
  "rule_ref": "policy-acl-external-in",
  "affected_resource": "interface:GigabitEthernet0/1:acl_in",
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
```

```json
// GET /incidents response body — a bare JSON array, no envelope
// (Day 5B binding correction; also restores "fingerprint", omitted by
// an earlier draft of this example — IncidentResponse, api/schemas.py,
// includes it)
[
  {
    "incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "fingerprint": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    "device_id": "spine-01",
    "source": "POLICY_VIOLATION",
    "rule_ref": "policy-acl-external-in",
    "affected_resource": "interface:GigabitEthernet0/1:acl_in",
    "severity": "Medium",
    "status": "OPEN",
    "evidence": { "source_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "violation_type": "MISSING_REQUIRED_ACL", "expected_acl_name": "ACL-EXTERNAL-IN", "actual_acl_name": null, "interface_name": "GigabitEthernet0/1", "direction": "in" },
    "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
    "created_at": "2026-07-18T10:00:00Z",
    "last_seen_at": "2026-07-18T10:00:00Z",
    "occurrence_count": 1
  }
]
```

stdout log line (FR-09, AC-10) — `outcome` distinguishes creation from update:

```json
{"incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "device_id": "spine-01", "rule_ref": "policy-acl-external-in", "severity": "Medium", "status": "OPEN", "outcome": "CREATED", "timestamp": "2026-07-18T10:00:00Z"}
```

**Repeated processing (dedup, AC-11).** If the identical Cisco config is
submitted again for `spine-01` (a second `POST`, not part of the primary
one-submission demonstration — this section exists to make that second
call well-defined, product-spec Section 11), `PolicyEvaluator` produces
the same `ConfigurationViolation` (same `device_id`/`source`/`rule_ref`/
`affected_resource`), which hashes to the same `fingerprint`.
`upsert_open_incident` finds `7c9e6679-7425-40de-944b-e07fc1f90ae7`
already `OPEN` and updates it in the same atomic call —
`outcome = "UPDATED"`, `last_seen_at` moves forward, `occurrence_count`
becomes `2`, `evidence` is refreshed — rather than inserting a second row.
The second `POST`'s response body reports `incidents_created: 0,
incidents_updated: 1`, computed from that `outcome` (architecture.md
Section 10.1).

---

## 19. Explicitly Deferred Entities

Named to prevent them being mistaken for oversights — genuinely post-MVP:

- **User / Operator entity** — no login/ownership concept (no auth).
- **AuditLog entity** — beyond the append-only `ConfigurationSnapshot`
  history and the stdout structured log, no separate audit trail entity.
- **Independent violation/anomaly history** — findings that never became
  an incident are not retained anywhere; only `Incident` is queryable.
- **`ConfigurationPolicy` CRUD/versioning** — seeded only.
- **Incident reopen workflow, acknowledgment, assignment, comments/notes,
  and bulk resolution** — Day 7A implemented `Incident.resolve(at)`
  (`OPEN -> RESOLVED`, Section 10); `ACKNOWLEDGED` remains a dormant
  compatibility state with no public transition into it, and there is no
  path back from `RESOLVED` to `OPEN`.
- **`ConfigurationSnapshot` replay/re-parse** — the raw/normalized split
  makes this possible later; no replay mechanism is built.
- **Route/BgpNeighbor as top-level entities** — remain embedded
  collections within `NormalizedConfiguration.routing`.
- **Cross-device/topology entities** (e.g. a `Link` between two devices'
  interfaces) — every entity here is single-device-scoped.
- **Drift-to-incident severity table** beyond "removed ACL → Medium." Note:
  this is about incident-emission from a drift finding, which remains
  deferred — `DriftDetector`/`DriftReport`/`DriftEntry` themselves are
  implemented as of Day 9 (Section 20); no `Incident` is ever produced by
  that flow.

---

## 20. DriftEntry and DriftReport (Day 9)

**Purpose.** `DriftDetector.compare`'s output shape (Section 17, FR-04) —
the drift-based sibling of `ConfigurationViolation` (Section 7), used by
`GetDeviceDriftService`/`GET /devices/{device_id}/drift`
(architecture.md Section 20). Transient values, no independent identity or
repository — never persisted, computed fresh on every request.

```
DriftEntry
├── resource: string       # "interface:<name>" | "acl:<name>" |
│                           # "bgp_neighbor:<neighbor_ip>"
├── field: string | null   # the exact NormalizedInterface/NormalizedBgpNeighbor
│                           # attribute name for a `changed` entry; null for
│                           # a whole-resource `added`/`removed` entry
├── old_value: string | null
└── new_value: string | null

DriftReport
├── added: [DriftEntry]
├── removed: [DriftEntry]
└── changed: [DriftEntry]
```

**Invariants.** `DriftEntry.resource` must not be empty.
`old_value`/`new_value` must not both be `null` (an `added` entry has
`old_value = null`; a `removed` entry has `new_value = null`; a `changed`
entry has both set). Both types are immutable value objects (`tuple`
fields on `DriftReport`, never a `list`).

**Whole-resource `added`/`removed` value.** `old_value` (removed) /
`new_value` (added) is the resource's own natural-identity string (the
interface name, ACL name, or neighbor IP) — not a serialization of the
full removed/added object.

**Scalar-value conversion for a `changed` entry.** `None` stays `null`;
`str` values are unchanged; `int` values (e.g. `mtu`, `remote_as`) render
as their base-10 decimal string; enum values (e.g. `admin_state`) render
as the enum's own `.value`, never `EnumClass.MEMBER`. No tuple, list,
dataclass repr, or JSON dump is ever used as a value.

**Determinism.** Output preserves each input tuple's order — never
re-sorted, the same precedent `PolicyEvaluator` (Section 7) uses.
`removed`/`changed` entries follow `baseline`'s collection order; `added`
entries follow `current`'s collection order. Matching between baseline and
current is always by identity (name / neighbor IP), never by tuple
position — reordering an unrelated collection element never produces a
spurious drift entry.

**Comparison scope, exactly as implemented** — see architecture.md
Section 20 for the full table and explicit exclusions (hostname,
`static_routes`, ACL-entry-level diffing, severity, recommendations,
incident creation).

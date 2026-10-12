# ADR-0003: Network lab and live operational-state collection

**Status:** Accepted
**Date:** 2026-10-05

## Context

The original MVP (product-spec.md Sections 6-7, assumption A-03) is
intentionally bounded:

- configuration is submitted by a caller;
- telemetry is submitted by a caller or the deterministic simulator;
- the platform does not poll live devices;
- there is no remediation or configuration push;
- there is no network lab.

That MVP is complete as defined (Day 12 checkpoint, tag
`day-12-device-query-api`) and this ADR does not reopen it.

A new **Network Production Engineering (NPE)** phase extends the project
*beyond* those MVP boundaries. Its purpose is to demonstrate, against a
controlled local lab:

- real routing-protocol operation;
- live network-state collection;
- failure injection;
- root-cause diagnosis;
- eventually, safe remediation.

NPE-1A (a read-only environment audit) found that the development machine
can support this: Ubuntu 22.04 under WSL2, with Containerlab already
installed. No lab code, topology, or collector exists yet.

## Decision

Build the lab with:

- Ubuntu WSL2 as the Linux environment;
- Containerlab as the lab orchestrator;
- FRR (FRRouting) as the routing stack;
- topology-as-code (a version-controlled Containerlab topology file and
  per-node FRR configuration);
- an IPv4-first lab;
- deterministic addressing and deterministic AS numbers.

### Lab 1 topology

Nodes: `spine-1`, `spine-2`, `leaf-1`, `leaf-2`, `host-1`, `host-2`.
Every leaf connects to both spines; `host-1` attaches to `leaf-1` and
`host-2` to `leaf-2`.

```
            spine-1      spine-2
             |  \          /  |
             |   \        /   |
             |    \      /    |
             |     \    /     |
             |      \  /      |
             |       \/       |
             |       /\       |
             |      /  \      |
           leaf-1 --'    '-- leaf-2
             |                |
           host-1           host-2
```

Each leaf has one link to each spine (a full 2x2 mesh).

Four router links: leaf-1↔spine-1, leaf-1↔spine-2, leaf-2↔spine-1,
leaf-2↔spine-2.

### Routing

- Shared spine AS **65000** (both spines).
- leaf-1 AS **65101**; leaf-2 AS **65102**.
- eBGP on every leaf-spine link.
- ECMP with `maximum-paths 2`.
- `/31` routed inter-router links.
- `/32` router loopbacks.
- Routed host-facing `/24` networks.
- A static default route on each host.

A shared spine AS is chosen over per-spine ASNs: it matches the common
data-center eBGP design, gives equal-length AS paths via either spine (so
ECMP needs no tuning), and AS-path loop detection prevents
leaf-spine-leaf-spine path loops.

## Addressing contract

Router links:

| Link | Subnet | Leaf end | Spine end |
|---|---|---|---|
| leaf-1 ↔ spine-1 | 10.255.0.0/31 | leaf-1 10.255.0.1 | spine-1 10.255.0.0 |
| leaf-1 ↔ spine-2 | 10.255.0.2/31 | leaf-1 10.255.0.3 | spine-2 10.255.0.2 |
| leaf-2 ↔ spine-1 | 10.255.0.4/31 | leaf-2 10.255.0.5 | spine-1 10.255.0.4 |
| leaf-2 ↔ spine-2 | 10.255.0.6/31 | leaf-2 10.255.0.7 | spine-2 10.255.0.6 |

Host networks:

| Host | Address | Gateway |
|---|---|---|
| host-1 | 10.1.1.10/24 | 10.1.1.1 (leaf-1) |
| host-2 | 10.1.2.10/24 | 10.1.2.1 (leaf-2) |

Loopbacks:

| Router | Loopback |
|---|---|
| spine-1 | 10.0.0.1/32 |
| spine-2 | 10.0.0.2/32 |
| leaf-1 | 10.0.0.11/32 |
| leaf-2 | 10.0.0.12/32 |

These ranges avoid Docker's default bridge (172.17.0.0/16) and
Containerlab's default management network (172.20.20.0/24).

### Management network

Containerlab's default management IPv4 subnet is 172.20.20.0/24. The
development host already had an unrelated Docker network, `slbdemo0_default`,
using 172.20.0.0/16, which contains it. The first live deployment attempt
therefore failed before any node was created, because Docker rejected the
overlapping network.

Lab 1 now owns an explicit, deterministic management network, declared in
the topology file:

| Setting | Value |
|---|---|
| network | `meta-rne-bgp-mgmt` |
| IPv4 | 172.31.250.0/24 |
| IPv6 | 3fff:172:31:250::/64 |

- This management network is separate from all Lab 1 dataplane addressing.
- The unrelated `slbdemo0` network is deliberately left untouched.
- Explicit topology configuration is preferred to deleting unrelated
  resources or relying on per-command deployment flags.
- This change does not alter the BGP topology, ASNs, dataplane addressing,
  routing behavior, ECMP design or fault scenario.

## First failure scenario

Disable the link **leaf-1 eth1 ↔ spine-1 eth1**.

Expected:

- the interface goes down;
- one BGP adjacency (leaf-1 ↔ spine-1) leaves Established;
- ECMP paths from leaf-1 to host-2's network reduce from 2 to 1;
- host-1 ↔ host-2 reachability remains healthy via spine-2;
- restoring the link restores the adjacency;
- ECMP returns to 2.

## Architecture boundary

Live collection must **not** replace the deterministic simulator. Both
modes coexist:

- **simulator** = deterministic test/demo source (unchanged);
- **live collector** = real lab operational-state source (read-only collector
  implemented in NPE-1C3A; not yet connected to the platform).

Live state will enter a **separate** `NormalizedOperationalState` model.
`TelemetrySample` is **not** extended with routes, reachability, or other
live-only fields: it is persisted, exposed through the API and OpenAPI
contract, and its CPU/memory fields assume a simulator-style source.

A future translation layer may derive `TelemetrySample`-compatible
observations from `NormalizedOperationalState`, so the existing
`RuleEngine`, incident mapping, and incident pipeline can be reused
without modification.

## Safety boundary

This ADR authorizes:

- live **read-only** state collection against the local lab;
- controlled, lab-only **fault injection** in later, separately approved
  gates.

It does **not** yet authorize:

- production-device access;
- configuration push to real networks;
- automatic remediation;
- AI-originated writes;
- external network credentials;
- cloud deployment.

All future writes remain lab-only until separately approved.

## Environment limitations

- Containerlab v0.77.0 is already installed in Ubuntu WSL2.
- Docker Desktop exists but was not running during NPE-1A; Lab 1 requires
  it to be started.
- FRR and host container images may need to be pulled.
- MPLS forwarding is unavailable on the current stock WSL kernel (no
  `MPLS_ROUTING`/`MPLS_IPTUNNEL`), so **MPLS is explicitly deferred**.
- GRE/IP-in-IP kernel support is present and may be considered later.
- Windows free-memory pressure is a deployment risk; at least
  approximately 2 GB of free host memory is required before Lab 1
  deployment.

## Consequences

- product-spec.md A-03, the Section 7 non-goals on live access, and
  architecture.md's "no live device in the loop" statement remain accurate
  for the MVP but are superseded, for the NPE extension only, by this ADR
  (lab devices only).
- Production-device polling and remediation remain out of scope.
- No existing code, schema, API, or test changes as a result of this ADR.

## Implementation status: NPE-1C3A (read-only collection)

`NormalizedOperationalState` now exists (`domain/operational_state.py`), with a
read-only collector for Lab 1 under the existing adapter layer
(`adapters/containerlab_frr/`: pure parsers and a collector that takes an
injected command runner and spawns nothing). The only real subprocess runner
is `scripts/lab_collect_state.py`, which re-checks every call against the
collector's fixed allowlist of read-only commands in the six Lab 1 containers.

- The model is separate from `TelemetrySample`; `TelemetrySample` is unchanged.
- An observed failure state (interface `down`, BGP `Idle`/`Active`) is distinct
  from an unavailable observation (failed command, unparseable output, or a
  BGP state the model does not recognize, which is `Unknown`). A collection
  error is never reported as a network failure.
- Not done, by design: no persistence of normalized state, no translation into
  `TelemetrySample`, no device registration or `VendorType` change, no posting
  to the platform API, no remediation, no AI action. NPE-1C3B (platform
  integration) is not started.

## Implementation status: NPE-1C3B1 (lab router identity and persistence)

NPE-1C3B1 is **identity and persistence support only**. NPE-1C3B (platform
integration) is not complete.

- The four Lab 1 routers may now exist as registered platform devices, with
  the fixed IDs `lab1-spine-1`, `lab1-spine-2`, `lab1-leaf-1` and
  `lab1-leaf-2`. Hosts are not registered.
- Their identity is `VendorType.FRR = "frr"` (migration 0004 widens only
  `ck_devices_vendor`). Their identity is independent of configuration
  snapshots: `current_snapshot_id` and `baseline_snapshot_id` are `None`, which
  is a supported state for FRR devices only. `Device` rejects an FRR device that
  references a snapshot.
- No FRR configuration ingestion exists: there is no FRR adapter,
  `AdapterRegistry` still rejects `frr`, `ConfigurationSnapshot` rejects the FRR
  vendor, and `ck_configuration_snapshots_vendor` still allows only Cisco/Arista.
- `GET /devices/{id}` returns a snapshot-less FRR router with
  `normalized_config: null`; `GET /devices/{id}/drift` returns `409
  drift_not_applicable`. Cisco/Arista behavior is unchanged.
- Registration is an explicit, idempotent, fixed-allowlist use case
  (`RegisterLabDevicesService`) run by `scripts/lab_register_devices.py`
  against `DATABASE_URL`. There is no HTTP registration endpoint, and no
  arbitrary device ID or vendor input. An existing device with a conflicting
  vendor or snapshot state fails registration closed.
- Not done, by design (as of C3B1): no operational-state incident ingestion, no detector,
  no incidents generated from live state, no `RuleEngine` or `BgpDownEvidence`
  change (as of C3B1; see C3B2 below), no auto-resolution. `TelemetrySample` is unchanged.

## Implementation status: NPE-1C3B2 (pure BGP-down detector)

NPE-1C3B is **not complete**. C3B2 adds detection only:

- Normalized operational state now has a pure BGP-down detector
  (`OperationalStateDetector.detect`: `FabricOperationalState` ->
  `tuple[Anomaly, ...]`, RULE-BGP-DOWN only; no repository, database,
  subprocess, HTTP or clock; `detected_at` is the fabric `collected_at`).
- It is stateless and level-triggered: every observed `Idle`/`Active` neighbor
  yields an anomaly on every evaluation. It does not deduplicate; that belongs
  to the incident repository (C3B3).
- `Idle`/`Active` are observed failures. `UNKNOWN` (including a normalized
  `Clearing`), an unavailable or missing BGP facet, `Connect`/`OpenSent`/
  `OpenConfirm`, `Established` and hosts are never failures.
- `BgpDownEvidence.previous_state` is now `BgpState | None`. `None` means the
  neighbor was first observed already degraded; the detector never fabricates a
  predecessor and never uses `UNKNOWN` for it. The telemetry `RuleEngine` is
  unchanged (edge-triggered, still records the real prior state). Persistence,
  the API (`previous_state: string | null`) and the frontend accept `null`;
  non-null evidence is unchanged. The down-state set is shared as
  `BGP_DOWN_STATES`.
- Collector node IDs map through a fixed table (`spine-1` -> `lab1-spine-1`,
  etc.); an unknown router node fails closed with `ValueError`.
- One physical link failure yields two device-scoped anomalies (e.g.
  `lab1-leaf-1`/`10.255.0.0` and `lab1-spine-1`/`10.255.0.1`), whose states may
  differ mid-convergence. They are not correlated; that is deferred to RCA.
- Not done: no incident ingestion, no incident writes, no auto-resolution, no
  recovery event, no public endpoint.

## Implementation status: NPE-1C3B3 (persist live operational-state BGP incidents)

C3B3 adds the
application-layer path from collected state to persisted incidents:

- `OperationalStateIngestionService.ingest(FabricOperationalState)` runs the
  existing pure `OperationalStateDetector`, then maps each anomaly through the
  existing `AnomalyIncidentMapper`, fingerprints it with the existing
  `compute_fingerprint` and persists it with the existing atomic
  `upsert_open_incident`. The detector stays pure and stateless; no
  deduplication logic was added. It returns a frozen
  `OperationalStateIngestionResult` (collection id, anomalies, upsert results,
  created/updated counts).
- The service never collects state, spawns a process, calls Docker/Containerlab,
  performs HTTP, reads a clock, registers a device or resolves an incident. The
  collected state is **not** persisted and **no telemetry is fabricated**:
  `TelemetrySample` and `TelemetryIngestionService` are unchanged.
- One `UnitOfWork` per call, one `commit()`, and the existing
  exception-preserving rollback/close lifecycle: any failure persists nothing.
- Every device an anomaly references must already be registered (C3B1). A
  missing device raises the existing `DeviceNotFoundError` before any write;
  the service never auto-creates devices.
- Timestamps come from the observation (`collected_at` -> `detected_at` ->
  `observed_at`). Repeated degraded observations at later timestamps update the
  same OPEN incident: same fingerprint, `occurrence_count` + 1, `last_seen_at`
  advanced. The repository still rejects a stale (earlier) observation.
- A physical link failure yields **two device-scoped incidents** (e.g.
  `lab1-leaf-1` / `bgp-neighbor:10.255.0.0` and `lab1-spine-1` /
  `bgp-neighbor:10.255.0.1`), not one. Correlating them into a single physical
  root cause is deferred to RCA.
- `previous_state` is persisted as JSON `null` and read back as `None`.
- **C3B3 itself does not resolve incidents.** A recovered or baseline fabric
  yields zero anomalies, therefore zero upserts. (Positive-evidence recovery
  was added afterwards by NPE-1C3C, below.)
- Structured incident events use the existing `IncidentLogEvent`.
- No public write endpoint and no migration. `GET /incidents` is the read path
  and `IncidentResponse` is unchanged. The frontend is unchanged.
- `scripts/lab_ingest_operational_state.py` is the controlled composition
  boundary: it takes no arguments, reuses `lab_collect_state`'s fixed
  allowlisted collector, requires `scripts/lab_register_devices.py` to have run
  (exit 3 otherwise), and prints one deterministic JSON summary on stdout
  (incident events go to stderr). It injects no fault.

Live proof (Lab 1 on the native Ubuntu Docker Engine, disposable PostgreSQL):
healthy fabric (6 nodes running, 4/4 sessions Established, ECMP 2 on both
leaves, hosts reachable) -> 0 anomalies, 0 incidents; with `leaf-1 eth1` down (3/4
sessions Established, leaf ECMP 2 -> 1, hosts still reachable) -> 2 anomalies and
exactly 2 OPEN `ANOMALY`/`RULE-BGP-DOWN` incidents (both ends reported `Active`,
`previous_state` null); a second degraded ingestion -> 0 created, 2 updated,
unchanged fingerprints, `occurrence_count` 2, `last_seen_at` advanced; after
restoring the link (4/4 Established, ECMP 2, hosts reachable) a further
ingestion -> 0 anomalies, 0 changes, both incidents still OPEN.

## Implementation status: NPE-1C3C (trustworthy BGP incident recovery)

C3C adds **positive-evidence recovery** to the C3B3 ingestion path, and nothing
else:

- A neighbor observed `Established` on a registered router resolves the OPEN
  `ANOMALY`/`RULE-BGP-DOWN` incident whose fingerprint matches **the same
  device and the same neighbor** (`device_id`, `ANOMALY`, `RULE-BGP-DOWN`,
  `bgp-neighbor:<neighbor-ip>`). The fingerprint scheme is unchanged and no
  recovery fingerprint exists. If no OPEN incident matches, nothing happens:
  no error, nothing is fabricated.
- **Only `Established` resolves.** `Idle`, `Active`, `Connect`, `OpenSent`,
  `OpenConfirm`, `UNKNOWN` (including a normalized `Clearing`), an unavailable
  or missing BGP facet, a missing neighbor, a missing node and a collector
  error never resolve anything. Absence of failure evidence is not recovery.
  Failure (`Idle`/`Active`) and recovery (`Established`) are disjoint per
  neighbor within a snapshot; transitional/unknown/unavailable states trigger
  neither.
- The two endpoint incidents of one link recover **independently**, each from
  its own local `Established` observation. If only one side is `Established`
  the other incident stays OPEN. Nothing is correlated across devices.
- `OperationalStateDetector` is unchanged and remains failure-detection only.
  Recovery lives in `OperationalStateIngestionService` because it needs
  persisted incident state and the transaction. The service finds the incident
  with the new narrow, read-only `IncidentRepository.get_open_by_fingerprint`
  (an indexed lookup on the OPEN partial unique index; no scan of unrelated
  incidents) and resolves it with the existing atomic
  `IncidentRepository.resolve`, i.e. the same `Incident.resolve` semantics the
  manual endpoint uses. `POST /incidents/{id}/resolve` and
  `ResolveIncidentService` are unchanged.
- Recovery runs **inside the same `UnitOfWork` and the same single commit** as
  the failure upserts of that ingestion; any failure rolls everything back and
  the original exception propagates. Structured events (created/updated and a
  new `RESOLVED` outcome, timestamped with `resolved_at`) are emitted only
  after commit.
- Timestamps: `resolved_at == updated_at ==` the healthy fabric's
  `collected_at`; `last_seen_at`, `created_at` and `occurrence_count` keep the
  values accumulated before recovery. An `Established` observation older than
  the incident's `last_seen_at` is rejected as a stale observation (the
  existing C3B3 rule), rolling back the whole ingestion and leaving the incident
  OPEN. History is never moved backward.
- Repeating a healthy ingestion is idempotent: a RESOLVED incident is no longer
  OPEN, so it is not found and not mutated, and no event is emitted.
- **Re-failure.** The unique constraint covers only OPEN fingerprints, so a
  later failure of the same neighbor creates a **new** OPEN incident (new
  `incident_id`, `occurrence_count` 1) with the same fingerprint value. The
  earlier RESOLVED incident is left untouched as history and is never
  resurrected.
- `OperationalStateIngestionResult` gains `resolved_incidents` and
  `incidents_resolved` (defaults keep existing fields compatible), and the CLI
  summary gains `incidents_resolved` and `resolved_incidents`. `GET /incidents`
  shows the recovery as `status: RESOLVED` with `resolved_at`; there is no new
  endpoint, no migration and no frontend change.
- Not done: no RCA, no cross-endpoint correlation, no remediation, no AI, no
  frontend redesign. `Established` means the local BGP session is up at the
  collection instant; this gate makes no claim about routing convergence
  timing, ECMP or reachability.

Live proof (Lab 1, disposable PostgreSQL): with `leaf-1 eth1` down, two
degraded ingestions created then updated exactly 2 OPEN incidents; after the
link was restored (4/4 Established) one ingestion resolved both (0 anomalies, 0
created, 0 updated, 2 resolved) and `GET /incidents` showed both `RESOLVED`
with `resolved_at` set; a repeat healthy ingestion resolved 0; a second failure
created 2 new OPEN incidents with the same fingerprints and left the resolved
history intact. FRR cycles a down session through `Idle`/`Active`, so separate
observations of the failed link differed (a read-only poll saw `Idle`; the
ingestion collections saw `Active`); persisted evidence always matches the
state of the collection that produced it.

## Implementation status: NPE-1D1A (latest operational-state persistence)

This gate makes the **latest** live operational state durable. It is
persistence only: `NPE-1D1` (state visible to operators) is **not** complete.

- **Storage.** Migration `0005_operational_state_latest` adds
  `operational_state_latest`: one row per lab (`lab_name` TEXT primary key),
  `collection_id` TEXT, `collected_at` TIMESTAMPTZ, `schema_version` INTEGER
  (>= 1) and `state` JSONB (must be a JSON object), all NOT NULL, with
  non-blank checks on `lab_name` and `collection_id`. No foreign keys, no index
  beyond the primary key, no history table, and no change to any existing
  table. Downgrade drops only this table.
- **Document.** `state` holds the complete `FabricOperationalState`
  (`schema_version` 1), encoded by a backend-owned codec
  (`persistence/operational_state_serialization.py`, nothing imported from
  `scripts/`). Every domain field round-trips exactly: tuples, optional
  values, enums, `raw_state`, `UNKNOWN`, unavailable facets, addresses,
  routes with active next hops, reachability. Malformed or unsupported JSON
  raises `SerializationError`; nothing is defaulted. `collection_id` and
  `collected_at` are duplicated into columns on purpose; on every read they
  (and `lab_name`, and a single shared collector) are validated against the
  decoded document and any disagreement fails closed rather than being
  repaired.
- **Source identity.** `FabricOperationalState` is unchanged. Before a fabric
  may be persisted, `derive_fabric_source` requires a non-empty fabric whose
  nodes all share one non-empty `lab_name` and one non-empty `collector`
  (`EmptyFabricError`, `MixedLabNameError`, `MixedCollectorError`); the domain
  already enforces that every node's `collection_id` / `collected_at` matches
  the fabric. The lab is derived, never chosen from "the first node" and never
  invented.
- **Collection identity and ordering.** A collection is `(lab_name,
  collection_id)`. `collection_id` stays an opaque correlation ID: it is never
  ordered and has no global uniqueness constraint. For an existing latest row
  of the same lab: a distinct collection with a strictly newer `collected_at`
  replaces it (`REPLACED`; first write is `CREATED`); a distinct, older one
  raises `StaleOperationalStateError`; a distinct one with an **equal**
  `collected_at` raises `OperationalStateConflictError` (no truthful ordering
  exists); the same `collection_id` with identical timestamp and state is an
  exact idempotent replay (`UNCHANGED`); the same `collection_id` with a
  different timestamp or different state raises `OperationalStateConflictError`
  (a collection ID names one immutable observation). Stale and conflicting
  observations are exceptions, not outcomes.
- **Atomicity of the write.** `OperationalStateRepository.save_latest` is one
  PostgreSQL `INSERT ... ON CONFLICT (lab_name) DO UPDATE ... WHERE` whose
  predicate requires a *different* `collection_id` **and** a strictly newer
  `collected_at`, so an older collection can never overwrite newer state and
  the same ID with a newer timestamp is a conflict, not an update. When it
  writes nothing, the row it conflicted with is read back inside the same
  transaction and classified. The in-memory repository decides through the
  same function (`decide_latest_write`) and the same shared contract tests run
  against both. Real concurrent writers cannot regress the latest state.
- **One transaction.** `uow.operational_state` joins both UnitOfWork
  implementations (in-memory snapshot/commit/rollback included).
  `OperationalStateIngestionService` derives the source, detects, validates
  device prerequisites, saves the latest state, then upserts failure incidents
  and resolves trustworthy recoveries, and commits **once**; events are emitted
  only after commit. Any failure rolls the state write and every incident
  change back together and the original exception propagates.
- **Two intentional behavior changes, nothing else.** (1) A stale collection is
  now rejected (`StaleOperationalStateError`) even if it would otherwise have
  mutated no incident; previously only a stale incident-affecting observation
  raised. (2) An **exact replay** of an already-accepted collection is a
  whole-ingestion no-op: because the state row and the incident mutations
  commit together, a collection already stored was already fully accepted, so
  replaying it does not increment `occurrence_count`, advance `last_seen_at`,
  resolve anything, rewrite the state row, or emit events. The detector's
  anomalies are still returned; every mutation count is zero. This is distinct
  from a **new** collection (new `collection_id`, later `collected_at`) that
  observes the same network condition again, which still updates OPEN
  incidents exactly as in NPE-1C3B3 and still resolves on trustworthy
  `Established` evidence as in NPE-1C3C.
- **CLI.** `scripts/lab_ingest_operational_state.py` remains the only (trusted)
  write boundary and still takes no arguments. Exit codes 0-3 are unchanged;
  new `4` means the collection was rejected as stale or conflicting
  (nothing persisted; stderr states which). An exact replay exits as success.
  The JSON summary gains `state_outcome` (`CREATED` / `REPLACED` /
  `UNCHANGED`).
- **Latest only.** No history of past states is kept; history is deferred.
- **Not done.** No public operational-state write endpoint exists (and none is
  planned: ingestion stays a trusted CLI). `GET /network/state` is not
  implemented, and there are no operational-state API schemas; those are
  `NPE-1D1B`. Topology is not stored and links are never inferred from BGP.
  The frontend is unchanged. No RCA, remediation or AI behavior.

## Implementation status: NPE-1D1B (read-only live network state API)

D1B exposes the D1A latest state read-only and nothing else:

- `GET /network/state` (`operation_id` `get_network_state`) returns the latest
  ingested `FabricOperationalState` of the fixed Lab 1 (`meta-rne-bgp`) through
  `GetNetworkStateService` (one `UnitOfWork`, no commit) as an independent
  `NetworkStateResponse`; the public contract does not depend on the
  `schema_version=1` persistence encoding. No state is `404`
  `operational_state_not_found`, never an empty `200`.
- `retrieved_at` comes from the injected clock, once per query; it is not
  stored and is not a freshness threshold. No health score or status enum.
- Router nodes carry the platform `device_id` from a neutral
  `meta_rne/lab1_identity.py` (a pure move of the former detector mapping,
  now also the Lab name); hosts have `device_id: null`.
- Unavailable facets are `null` plus an `unavailable[]` reason; observed-empty
  is `[]`; not-applicable is `null` (see `docs/frontend-api-contract.md` 8B).
- **No new write boundary** (no public write endpoint) and **no migration**.
  Ingestion remains the trusted CLI.
- **Topology is still deferred to D2** and the **frontend is not implemented**.
  No RCA, remediation or AI behavior.

## Deferred

- OSPF
- IS-IS
- MPLS
- GRE
- IP-in-IP
- translation of normalized state into `TelemetrySample` (NPE-1C3B)
- history of persisted normalized state (only the latest is stored, NPE-1D1A)
- device identity / registration of lab nodes (NPE-1C3B)
- fault automation
- RCA application correlation and incident linking (NPE-1E1B; the pure
  deterministic RCA engine itself exists since NPE-1E1A)
- `GET /network/diagnosis` (NPE-1E1B)
- diagnosis persistence and history
- frontend RCA presentation (NPE-1E1C)
- remediation engine
- AI operator copilot
- evaluation harness
- backend/frontend integration

## Implementation status: NPE-1D2 (live network operator workspace)

Frontend-only. The default app view is now a read-only operator workspace over
`GET /network/state` and `GET /incidents` for the fixed declared Lab 1
topology (physical state from interface observations, BGP adjacency state
separately, ECMP, host reachability, observation age, open incidents, node
detail, 5 s presentation polling, stale-response protection by `collected_at`).
No backend source, endpoint or persistence semantics changed; the browser
performs no operational-state write. RCA, remediation and AI remain out of
scope. Details: `docs/frontend-api-contract.md` Section 8B (NPE-1D2 notes).

## Implementation status: NPE-1E1A (pure deterministic RCA engine)

Backend-only and pure. NPE-1E1 as a whole is **not** complete: this gate adds
the engine and nothing that calls it.

- **Declared topology.** `meta_rne/lab1_topology.py` declares the six Lab 1 nodes,
  six physical links (4 leaf-spine, 2 host-leaf), endpoint addresses and the
  two ECMP expectations (leaf-1 -> `10.1.2.0/24` and leaf-2 -> `10.1.1.0/24`,
  2 paths each). A link's endpoints are ordered by `(node, interface)` and its
  ID is `leaf-1:eth1--spine-1:eth1`. It is never parsed at runtime;
  `tests/unit/test_lab1_topology_drift.py` checks it against
  `lab/topology.clab.yml`, `lab/frr/*/frr.conf` (addresses, BGP neighbors,
  `maximum-paths 2`) and `LAB1_NODE_IDS`/`LAB1_NODE_DEVICE_IDS`.
- **Engine.** `meta_rne/diagnosis/engine.py`: `FabricOperationalState` plus the
  declared topology in, `tuple[Diagnosis, ...]` out (`domain/diagnosis.py`). No
  repository, UnitOfWork, incident, clock, HTTP or subprocess access and no
  history; the same input gives equal, identically ordered output. It reads only
  the latest collection.
- **Types.** `PHYSICAL_LINK_FAILURE`, `BGP_SESSION_FAILURE`,
  `INSUFFICIENT_EVIDENCE` (a diagnostic outcome, not a root cause).
  Only router-router links are evaluated.
- **Trigger.** An endpoint interface explicitly `DOWN`, or an expected BGP
  session explicitly `Idle`/`Active`. UNKNOWN/unavailable/missing facts, a
  missing node, transitional BGP states and a failed host ping never create a
  diagnosis and are never treated as proof of failure or of health.
- **Rules.** Some interface `DOWN` and no endpoint `Established` ->
  physical. Both interfaces `UP`, a BGP side `DOWN`, none `Established` -> BGP
  session. Any other symptom (e.g. an unknown interface with BGP down, or an
  `Established` side) -> `INSUFFICIENT_EVIDENCE` with `confidence = None`.
- **Confidence** (interface and BGP facts only). Physical: both interfaces down
  and a BGP side down `HIGH`; both down without BGP-down, or one down with
  BGP-down `MEDIUM`; one down without BGP-down `LOW`. BGP session: both sides
  down `HIGH`, one `MEDIUM`. An observed interface address that conflicts with
  the declared one is `CONTRADICTS` evidence and caps confidence at `MEDIUM`.
  Routes, ECMP and reachability are supporting evidence and never change a tier.
- **Evidence and symptom keys.** Structured `EvidenceItem`s (interface state,
  BGP session, address match, route next hop, ECMP paths, reachability) with
  `SUPPORTS`/`CONTRADICTS`/`UNKNOWN` roles; unavailable facets yield `UNKNOWN`
  items with no observed value. Route next-hop and ECMP evidence is specific to
  the diagnosed link: an ECMP expectation is attached only if losing that link
  removes one of its paths (the path via the link's spine, declared in
  `lab1_topology`), and it `SUPPORTS` only if that exact next hop is absent from
  the observed routes. A reduction caused by another link, or healthy ECMP, is
  neutral. Reachability is context only (healthy: neutral; loss: supporting). `symptom_keys` are
  `(device_id, "bgp-neighbor:<peer-ip>")` for each BGP-down end, ready for later
  incident linkage. A diagnosis may exist with no incident.
- **Fixtures.** Captured `baseline`, `degraded-active`, `degraded-idle` and
  `degraded-clearing` sets: baseline gives no diagnosis; the leaf-1 eth1 failure
  gives one `HIGH` `PHYSICAL_LINK_FAILURE` on `leaf-1:eth1--spine-1:eth1`.
- **Not done.** No persistence, repository, migration or diagnosis table; no
  API route; no incident linking; no frontend change; no remediation; no AI.
  Application/API correlation is NPE-1E1B and UI presentation is NPE-1E1C.

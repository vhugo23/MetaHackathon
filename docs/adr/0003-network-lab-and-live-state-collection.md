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
- **live collector** = real lab operational-state source (future).

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

## Deferred

- OSPF
- IS-IS
- MPLS
- GRE
- IP-in-IP
- live collector implementation
- `NormalizedOperationalState` implementation
- fault automation
- RCA engine
- remediation engine
- AI operator copilot
- evaluation harness
- backend/frontend integration

# Lab 1 — eBGP leaf-spine

> **Status: live-proven local lab.** This directory is the topology-as-code
> definition. It was first defined and validated offline in NPE-1B2, before
> any successful deployment; later milestones then proved it live on a native
> Ubuntu Docker Engine under WSL2: 4 FRR routers and 2 hosts, four
> Established eBGP sessions, two-path ECMP on both leaves, and bidirectional
> host-to-host reachability. A controlled `leaf-1 eth1` failure and recovery
> was also validated (ECMP 2 → 1 → 2, with host reachability preserved
> throughout). This is a local development lab, not a production network, and
> nothing here performs automated remediation.
> Decision record: [ADR-0003](../docs/adr/0003-network-lab-and-live-state-collection.md).

## Purpose

A small, reproducible data-center fabric for the Network Production
Engineering phase: real eBGP operation, host-to-host reachability, ECMP
redundancy, and survival of a single link failure. It is the base for later
live state collection, fault injection, and root-cause diagnosis.

## Topology

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

Each leaf has one link to each spine. Exactly six dataplane links exist.

## Nodes

| Node | Role | AS | Loopback | Image |
|---|---|---|---|---|
| spine-1 | FRR router | 65000 | 10.0.0.1/32 | `quay.io/frrouting/frr:10.6.1` |
| spine-2 | FRR router | 65000 | 10.0.0.2/32 | `quay.io/frrouting/frr:10.6.1` |
| leaf-1 | FRR router | 65101 | 10.0.0.11/32 | `quay.io/frrouting/frr:10.6.1` |
| leaf-2 | FRR router | 65102 | 10.0.0.12/32 | `quay.io/frrouting/frr:10.6.1` |
| host-1 | Linux host | — | — | `alpine:3.24.2` |
| host-2 | Linux host | — | — | `alpine:3.24.2` |

Both spines share AS 65000. Images are pinned; `latest` is never used.

## Links and addressing

| Link | Endpoint A | A address | Endpoint B | B address | Subnet |
|---|---|---|---|---|---|
| 1 | leaf-1 eth1 | 10.255.0.1 | spine-1 eth1 | 10.255.0.0 | 10.255.0.0/31 |
| 2 | leaf-1 eth2 | 10.255.0.3 | spine-2 eth1 | 10.255.0.2 | 10.255.0.2/31 |
| 3 | leaf-2 eth1 | 10.255.0.5 | spine-1 eth2 | 10.255.0.4 | 10.255.0.4/31 |
| 4 | leaf-2 eth2 | 10.255.0.7 | spine-2 eth2 | 10.255.0.6 | 10.255.0.6/31 |
| 5 | host-1 eth1 | 10.1.1.10 | leaf-1 eth3 | 10.1.1.1 | 10.1.1.0/24 |
| 6 | host-2 eth1 | 10.1.2.10 | leaf-2 eth3 | 10.1.2.1 | 10.1.2.0/24 |

Hosts use a static default route via their leaf (`10.1.1.1` / `10.1.2.1`),
configured by Containerlab `exec` commands with no package installation.

## Management network

| Setting | Value |
|---|---|
| name | `meta-rne-bgp-mgmt` |
| IPv4 | 172.31.250.0/24 |
| IPv6 | 3fff:172:31:250::/64 |

Management addressing is explicit on purpose: Containerlab's default
172.20.20.0/24 collided with another local Docker project's network
(172.20.0.0/16). The network is management-only; dataplane tests do not
depend on management addresses. The unrelated Docker network was not
changed. A deployment has still not completed successfully, so Lab 1 remains
unproven live.

## Routing design

- eBGP on every leaf-spine link: **4 sessions** expected.
- Each router advertises its loopback; each leaf also advertises its
  host-facing /24.
- `maximum-paths 2` on both leaves (address-family `ipv4 unicast`).
- `frr defaults datacenter`, integrated config (`service integrated-vtysh-config`).
- `no bgp ebgp-requires-policy` on every router. `bgp ebgp-requires-policy`
  implements an RFC 8212-style requirement for explicit inbound/outbound
  eBGP policy, and `frr defaults datacenter` already disables it by
  default. The command is therefore redundant with the current
  datacenter-profile default; it is present on purpose so Lab 1's
  route-exchange behavior is explicit and does not rely on an implicit FRR
  profile default (it stays correct if the FRR defaults/profile later
  change). No route-maps are used. It is not an ECMP setting.
- Only `bgpd` is enabled (plus FRR's always-on zebra/staticd/mgmtd). No OSPF,
  IS-IS, MPLS/LDP, GRE, IP-in-IP, BFD, route reflectors, or IPv6.

### Why shared-spine-AS ECMP should work (to be proven live)

leaf-1 learns leaf-2's `10.1.2.0/24` from both spines. Each path carries
the AS path `65000 65102` — identical in content and length — so the two
paths tie in best-path selection and `maximum-paths 2` can install both.
`bgp bestpath as-path multipath-relax` is only needed when equal-length AS
paths differ in content, which does not happen here. The spines are not
interconnected, so no path crosses both. This is reasoning only; it must be
confirmed against a running lab.

## Expected behavior (once deployed)

- 4 BGP sessions Established.
- Each leaf's routing table holds the other leaf's host /24 with two
  equal-cost next-hops (one per spine).
- host-1 ↔ host-2 ping succeeds.

## Controlled link-failure scenario

`scripts/lab_failure_scenario.py` is a lab-only, repeatable, measured version
of the first fault scenario (first proven by hand in NPE-1C1). It models loss
of exactly one fabric link, `leaf-1 eth1 ↔ spine-1 eth1`, and its recovery.

| Phase | Expected |
|---|---|
| Baseline | 4 of 4 leaf-spine eBGP sessions Established; two installed next-hops on each leaf (leaf-1 → `10.1.2.0/24` via `10.255.0.0`/eth1 and `10.255.0.2`/eth2; leaf-2 → `10.1.1.0/24` via `10.255.0.4`/eth1 and `10.255.0.6`/eth2); host-1 ↔ host-2 reachable |
| Failure | `ip link set eth1 down` inside leaf-1 only; the leaf-1 ↔ spine-1 session leaves Established, the other 3 stay Established |
| Degraded | ECMP 2 → 1: leaf-1 keeps only `10.255.0.2` via eth2, leaf-2 only `10.255.0.6` via eth2; host-1 ↔ host-2 stays reachable through spine-2 |
| Restore | `ip link set eth1 up` (no re-addressing, no FRR restart or config change) |
| Recovered | 4 of 4 sessions Established again, ECMP back to 2 on both leaves, hosts reachable |

Run it against an already-deployed lab, from Ubuntu WSL next to the native
Docker Engine (Python 3.10+ is enough, standard library only):

```
containerlab deploy  -t lab/topology.clab.yml
python3 scripts/lab_failure_scenario.py [--output result.json]
containerlab destroy -t lab/topology.clab.yml --cleanup
```

Progress goes to stderr; a structured JSON result goes to stdout (and to
`--output`). Exit codes: `0` passed, `1` scenario failed, `2` refused (a safety
precondition failed, nothing injected), `3` baseline unhealthy (nothing
injected), `4` Docker/WSL runtime restarted during the run, `5` recovery
failed (the lab may be left degraded), `6` unexpected internal error.

Lab-only safety restrictions:

- The target (`clab-meta-rne-bgp-leaf-1`, `eth1`) is a constant. There is no
  option or code path for another container, interface, command, or address.
- It refuses unless the topology is `meta-rne-bgp`, exactly the six Lab 1
  containers are running, the Docker server is the native Engine (not Docker
  Desktop), and the baseline above is fully healthy. It never injects a fault
  into an unhealthy lab.
- The restore command runs in a `finally` path, so `eth1` is brought back up
  even if validation fails, times out, or raises.

Measurements (all monotonic, from one execution):

- BGP detection, leaf-1 FIB convergence and leaf-2 FIB convergence are
  measured independently from the injection instant; they are not
  interchangeable. Recovery has the same three measurements from the restore
  instant.
- They are taken by polling, so each is an upper bound with a resolution of one
  poll cycle (a few `docker exec` calls) and includes `docker exec` dispatch
  latency. A value of a few hundred ms means "converged by the first poll".
- A background monitor sends one single-packet ping per probe from host-1 to
  host-2 and timestamps each result itself (avoiding BusyBox's buffered
  long-running ping output). It reports probe count, losses, loss percentage,
  the longest consecutive failure streak, and failure offsets relative to the
  injection. Brief loss during reconvergence does not fail the scenario;
  failing to recover does.
- The result also records the WSL boot id and Docker restart count before and
  after; a change fails the run (exit `4`), because a restarted daemon would
  invalidate the experiment.

This is controlled fault injection and measurement only. It does not detect,
diagnose, or remediate anything automatically, and it is not connected to the
platform's telemetry, incident, or AI features.

## Deferred

MPLS (the stock WSL kernel lacks MPLS forwarding), OSPF, IS-IS, GRE,
IP-in-IP, live collection, and fault automation.

## Offline validation

The committed definition is checked without Docker or Containerlab:

```
python scripts/lab_validate.py
python scripts/test_lab_validate.py
```

## Offline parse check (this Containerlab version)

The installed Containerlab (v0.77.0) has **no `containerlab validate`
command**. The supported offline parse/graph check is:

```
containerlab graph   -t lab/topology.clab.yml   --offline   --mermaid
```

This parses the topology and builds a graph from topology-file information
only. It does not deploy the lab and is **not** equivalent to a full live
deployment validation. It does not prove bind-mount behavior, image or
runtime behavior, FRR startup, host `exec` behavior, BGP establishment,
ECMP, or reachability — those require the live deployment gate. Note that
the command writes a generated `clab-meta-rne-bgp/` directory next to the
topology file (ignored by `.gitignore`); remove it afterwards.

## Lab lifecycle commands

```
containerlab deploy -t lab/topology.clab.yml
containerlab destroy -t lab/topology.clab.yml --cleanup
```

NPE-1B2 itself did not run these (it only defined and validated the lab
offline); they were first executed in later live-deployment milestones, which
validated the lab end to end. Run them from Ubuntu WSL against the native
Docker Engine: Containerlab and Docker must share one Linux network
namespace, so Docker Desktop's separate engine does not work. The
`quay.io/frrouting/frr:10.6.1` and `alpine:3.24.2` images must be pulled into
that engine, the Containerlab management network is the explicit
`meta-rne-bgp-mgmt` (see above), and roughly 2 GB of free Windows memory is
advisable. Set `instanceIdleTimeout=-1` under `[general]` in `.wslconfig` so
WSL does not shut down (and restart Docker) while the lab is running.

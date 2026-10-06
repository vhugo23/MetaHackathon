# Lab 1 — eBGP leaf-spine

> **Status: defined offline, not yet deployed.** This directory is the
> topology-as-code definition from NPE-1B2. No container has been started,
> no BGP session has been established, and no reachability or ECMP behavior
> has been proven. The Alpine host image may not yet be present locally
> (`quay.io/frrouting/frr:10.6.1` is cached; `alpine:3.24.2` is not).
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

## First future fault scenario

Disable `leaf-1 eth1 ↔ spine-1 eth1`: expect that interface down, one BGP
adjacency out of Established, ECMP from 2 paths to 1, host-1 ↔ host-2 still
reachable via spine-2; restoring the link restores the adjacency and ECMP 2.

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

## Future commands (documentation only — not run by NPE-1B2)

```
containerlab deploy -t lab/topology.clab.yml
containerlab destroy -t lab/topology.clab.yml --cleanup
```

Deployment needs Docker Desktop running, the `alpine:3.24.2` image pulled,
and roughly 2 GB of free host memory.

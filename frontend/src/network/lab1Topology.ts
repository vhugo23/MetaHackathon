/**
 * The fixed, declared physical topology of Lab 1 (NPE-1D2).
 *
 * This is presentation metadata copied from `lab/topology.clab.yml` (node
 * names, link endpoints/interface names) and the Lab 1 addressing contract in
 * `lab/frr/<router>/frr.conf` / `lab/README.md` (link endpoint addresses).
 * Physical links are NEVER inferred from BGP neighbors, subnets or route next
 * hops. `lab1Topology.drift.test.ts` fails if this file diverges from the
 * repository's topology/FRR files, so production code needs no YAML/filesystem
 * access. Operational values (state, counts, paths) come from the API only.
 */

export type TopologyTier = "spine" | "leaf" | "host";

export interface TopologyNode {
  id: string;
  role: "router" | "host";
  tier: TopologyTier;
  /**
   * Platform device ID of a router (mirrors backend `lab1_identity.py`);
   * `null` for hosts, which are not registered devices.
   */
  deviceId: string | null;
}

export interface TopologyEndpoint {
  node: string;
  interface: string;
  /** The endpoint's own IPv4 address from the fixed addressing contract. */
  address: string;
}

export interface TopologyLink {
  id: string;
  /** `fabric`: router-to-router (one eBGP adjacency); `access`: host-to-leaf. */
  kind: "fabric" | "access";
  a: TopologyEndpoint;
  b: TopologyEndpoint;
}

export const LAB1_NODES: readonly TopologyNode[] = [
  { id: "spine-1", role: "router", tier: "spine", deviceId: "lab1-spine-1" },
  { id: "spine-2", role: "router", tier: "spine", deviceId: "lab1-spine-2" },
  { id: "leaf-1", role: "router", tier: "leaf", deviceId: "lab1-leaf-1" },
  { id: "leaf-2", role: "router", tier: "leaf", deviceId: "lab1-leaf-2" },
  { id: "host-1", role: "host", tier: "host", deviceId: null },
  { id: "host-2", role: "host", tier: "host", deviceId: null },
];

function link(kind: TopologyLink["kind"], a: TopologyEndpoint, b: TopologyEndpoint): TopologyLink {
  return { id: `${a.node}:${a.interface}--${b.node}:${b.interface}`, kind, a, b };
}

export const LAB1_LINKS: readonly TopologyLink[] = [
  link(
    "fabric",
    { node: "leaf-1", interface: "eth1", address: "10.255.0.1" },
    { node: "spine-1", interface: "eth1", address: "10.255.0.0" },
  ),
  link(
    "fabric",
    { node: "leaf-1", interface: "eth2", address: "10.255.0.3" },
    { node: "spine-2", interface: "eth1", address: "10.255.0.2" },
  ),
  link(
    "fabric",
    { node: "leaf-2", interface: "eth1", address: "10.255.0.5" },
    { node: "spine-1", interface: "eth2", address: "10.255.0.4" },
  ),
  link(
    "fabric",
    { node: "leaf-2", interface: "eth2", address: "10.255.0.7" },
    { node: "spine-2", interface: "eth2", address: "10.255.0.6" },
  ),
  link(
    "access",
    { node: "host-1", interface: "eth1", address: "10.1.1.10" },
    { node: "leaf-1", interface: "eth3", address: "10.1.1.1" },
  ),
  link(
    "access",
    { node: "host-2", interface: "eth1", address: "10.1.2.10" },
    { node: "leaf-2", interface: "eth3", address: "10.1.2.1" },
  ),
];

/**
 * The route whose path count expresses ECMP redundancy for a leaf: the other
 * leaf's host network. `expectedPaths` is the declared design (one path per
 * spine); the observed count always comes from the API.
 */
export interface EcmpExpectation {
  node: string;
  prefix: string;
  towardsHost: string;
  expectedPaths: number;
}

export const LAB1_ECMP_EXPECTATIONS: readonly EcmpExpectation[] = [
  { node: "leaf-1", prefix: "10.1.2.0/24", towardsHost: "host-2", expectedPaths: 2 },
  { node: "leaf-2", prefix: "10.1.1.0/24", towardsHost: "host-1", expectedPaths: 2 },
];

export function nodeById(id: string): TopologyNode | undefined {
  return LAB1_NODES.find((node) => node.id === id);
}

export function fabricLinks(): TopologyLink[] {
  return LAB1_LINKS.filter((l) => l.kind === "fabric");
}

/** The expected BGP adjacency count of one router (its fabric links). */
export function expectedAdjacencyCount(nodeId: string): number {
  return fabricLinks().filter((l) => l.a.node === nodeId || l.b.node === nodeId).length;
}

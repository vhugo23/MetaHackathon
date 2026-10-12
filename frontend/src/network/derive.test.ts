import { describe, expect, test } from "vitest";
import type { NetworkStateResponse } from "../api/networkStateTypes";
import type { IncidentResponse } from "../api/types";
import {
  BASELINE_NETWORK_STATE,
  DEGRADED_NETWORK_STATE,
  allBgpUnavailableState,
  bgpDownIncident,
  failureIncidents,
  recoveredNetworkState,
  resolvedFailureIncidents,
  withNode,
} from "../test/networkFixtures";
import {
  combineBgpEndpoints,
  combinePhysicalState,
  deriveBgpAdjacencies,
  deriveEcmp,
  deriveNetworkSummary,
  derivePhysicalLink,
  derivePhysicalLinks,
  deriveReachability,
  formatDuration,
  lab1OperationalIncidents,
  openIncidents,
  openIncidentsForDevice,
  observationAgeMs,
  summarizeRouterBgp,
} from "./derive";
import { LAB1_LINKS, LAB1_NODES } from "./lab1Topology";

const LEAF1_SPINE1 = LAB1_LINKS[0]!;

function setInterfaceState(
  state: NetworkStateResponse,
  nodeId: string,
  iface: string,
  oper: "up" | "down" | "unknown",
): NetworkStateResponse {
  return withNode(state, nodeId, (node) => ({
    ...node,
    interfaces: node.interfaces!.map((i) => (i.name === iface ? { ...i, oper_state: oper } : i)),
  }));
}

describe("declared topology", () => {
  test("has exactly 6 nodes and 6 physical links", () => {
    expect(LAB1_NODES.map((n) => n.id)).toEqual([
      "spine-1",
      "spine-2",
      "leaf-1",
      "leaf-2",
      "host-1",
      "host-2",
    ]);
    expect(LAB1_LINKS).toHaveLength(6);
    expect(LAB1_LINKS.filter((l) => l.kind === "fabric")).toHaveLength(4);
    expect(LAB1_LINKS.filter((l) => l.kind === "access")).toHaveLength(2);
  });
});

describe("physical link state (interface observations only)", () => {
  test.each([
    ["up", "up", "up"],
    ["down", "up", "down"],
    ["up", "down", "down"],
    ["down", "down", "down"],
    ["unknown", "up", "unknown"],
    ["up", "unknown", "unknown"],
    ["unknown", "unknown", "unknown"],
    ["unknown", "down", "down"],
  ] as const)("%s + %s -> %s", (a, b, expected) => {
    expect(combinePhysicalState(a, b)).toBe(expected);
  });

  test("baseline: all six links UP", () => {
    expect(derivePhysicalLinks(BASELINE_NETWORK_STATE).map((l) => l.state)).toEqual(
      Array(6).fill("up"),
    );
  });

  test("an endpoint reporting down makes the link DOWN (either side)", () => {
    const leafDown = setInterfaceState(BASELINE_NETWORK_STATE, "leaf-1", "eth1", "down");
    expect(derivePhysicalLink(leafDown, LEAF1_SPINE1).state).toBe("down");
    const spineDown = setInterfaceState(BASELINE_NETWORK_STATE, "spine-1", "eth1", "down");
    expect(derivePhysicalLink(spineDown, LEAF1_SPINE1).state).toBe("down");
  });

  test("a reported interface state of unknown is UNKNOWN", () => {
    const state = setInterfaceState(BASELINE_NETWORK_STATE, "leaf-1", "eth1", "unknown");
    const link = derivePhysicalLink(state, LEAF1_SPINE1);
    expect(link.state).toBe("unknown");
    expect(link.a.reason).toBe("reported");
  });

  test("an unavailable interfaces facet is UNKNOWN, never UP", () => {
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      interfaces: null,
      unavailable: [{ facet: "interfaces", reason: "ip -j addr failed" }],
    }));
    const link = derivePhysicalLink(state, LEAF1_SPINE1);
    expect(link.state).toBe("unknown");
    expect(link.a.reason).toBe("facet-unavailable");
  });

  test("a missing interface is UNKNOWN", () => {
    const state = withNode(BASELINE_NETWORK_STATE, "spine-1", (node) => ({
      ...node,
      interfaces: node.interfaces!.filter((i) => i.name !== "eth1"),
    }));
    const link = derivePhysicalLink(state, LEAF1_SPINE1);
    expect(link.state).toBe("unknown");
    expect(link.b.reason).toBe("interface-missing");
  });

  test("a missing node, or no state at all, is UNKNOWN", () => {
    const state: NetworkStateResponse = {
      ...BASELINE_NETWORK_STATE,
      nodes: BASELINE_NETWORK_STATE.nodes.filter((n) => n.node_id !== "spine-1"),
    };
    const link = derivePhysicalLink(state, LEAF1_SPINE1);
    expect(link.state).toBe("unknown");
    expect(link.b.reason).toBe("node-missing");
    expect(derivePhysicalLinks(null).every((l) => l.state === "unknown")).toBe(true);
  });

  test("BGP state never colours the physical link", () => {
    // leaf-1 eth1 is up in the interface facet but its BGP session is Active.
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      bgp: {
        ...node.bgp!,
        neighbors: node.bgp!.neighbors.map((n) =>
          n.neighbor_ip === "10.255.0.0" ? { ...n, state: "Active" as const } : n,
        ),
      },
    }));
    expect(derivePhysicalLink(state, LEAF1_SPINE1).state).toBe("up");
  });

  test("degraded capture: leaf-1:eth1 <-> spine-1:eth1 is DOWN, the rest UP", () => {
    const states = Object.fromEntries(
      derivePhysicalLinks(DEGRADED_NETWORK_STATE).map((l) => [l.link.id, l.state]),
    );
    expect(states["leaf-1:eth1--spine-1:eth1"]).toBe("down");
    expect(Object.values(states).filter((s) => s === "up")).toHaveLength(5);
  });
});

describe("BGP adjacency state (separate from physical state)", () => {
  const established = { kind: "observed", state: "Established", rawState: "Established" } as const;
  const active = { kind: "observed", state: "Active", rawState: "Active" } as const;
  const unknown = { kind: "observed", state: "Unknown", rawState: "Clearing" } as const;

  test("both Established -> established", () => {
    expect(combineBgpEndpoints(established, established)).toBe("established");
  });

  test("one known non-Established endpoint -> degraded", () => {
    expect(combineBgpEndpoints(established, active)).toBe("degraded");
    expect(combineBgpEndpoints(active, established)).toBe("degraded");
    expect(combineBgpEndpoints(active, active)).toBe("degraded");
  });

  test("a known non-Established endpoint outranks an unknown other endpoint", () => {
    expect(combineBgpEndpoints(active, unknown)).toBe("degraded");
    expect(combineBgpEndpoints(active, { kind: "facet-unavailable" })).toBe("degraded");
  });

  test("UNKNOWN / unavailable / missing -> unknown (never degraded or established)", () => {
    expect(combineBgpEndpoints(established, unknown)).toBe("unknown");
    expect(combineBgpEndpoints(unknown, unknown)).toBe("unknown");
    expect(combineBgpEndpoints(established, { kind: "facet-unavailable" })).toBe("unknown");
    expect(combineBgpEndpoints(established, { kind: "node-missing" })).toBe("unknown");
    expect(combineBgpEndpoints(established, { kind: "neighbor-missing" })).toBe("unknown");
    expect(combineBgpEndpoints({ kind: "node-missing" }, { kind: "node-missing" })).toBe("unknown");
  });

  test("baseline: four adjacencies, all established (not eight)", () => {
    const adjacencies = deriveBgpAdjacencies(BASELINE_NETWORK_STATE);
    expect(adjacencies).toHaveLength(4);
    expect(adjacencies.every((a) => a.state === "established")).toBe(true);
  });

  test("degraded capture: leaf-1<->spine-1 degraded with the real Active states retained", () => {
    const adjacency = deriveBgpAdjacencies(DEGRADED_NETWORK_STATE).find(
      (a) => a.link.id === "leaf-1:eth1--spine-1:eth1",
    )!;
    expect(adjacency.state).toBe("degraded");
    expect(adjacency.a.observation).toEqual({
      kind: "observed",
      state: "Active",
      rawState: "Active",
    });
    expect(adjacency.b.observation).toEqual({
      kind: "observed",
      state: "Active",
      rawState: "Active",
    });
  });

  test("all BGP facets unavailable -> every adjacency unknown", () => {
    const adjacencies = deriveBgpAdjacencies(allBgpUnavailableState());
    expect(adjacencies.every((a) => a.state === "unknown")).toBe(true);
  });

  test("a router's per-node summary counts only explicit Established", () => {
    expect(summarizeRouterBgp(BASELINE_NETWORK_STATE, "leaf-1")).toEqual({
      established: 2,
      expected: 2,
      uncertain: false,
    });
    expect(summarizeRouterBgp(DEGRADED_NETWORK_STATE, "leaf-1")).toEqual({
      established: 1,
      expected: 2,
      uncertain: false,
    });
    expect(summarizeRouterBgp(allBgpUnavailableState(), "leaf-1")).toEqual({
      established: 0,
      expected: 2,
      uncertain: true,
    });
  });
});

describe("ECMP", () => {
  test("baseline: 2 paths on both leaves toward the other host network", () => {
    const ecmp = deriveEcmp(BASELINE_NETWORK_STATE);
    expect(ecmp.map((e) => [e.node, e.prefix, e.pathCount, e.status])).toEqual([
      ["leaf-1", "10.1.2.0/24", 2, "intact"],
      ["leaf-2", "10.1.1.0/24", 2, "intact"],
    ]);
  });

  test("degraded: path count comes from the API (1) and is flagged reduced", () => {
    const ecmp = deriveEcmp(DEGRADED_NETWORK_STATE);
    expect(ecmp.map((e) => [e.node, e.pathCount, e.status])).toEqual([
      ["leaf-1", 1, "reduced"],
      ["leaf-2", 1, "reduced"],
    ]);
  });

  test("a missing route is unknown / not observed, not 0 paths", () => {
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      routes: node.routes!.filter((r) => r.prefix !== "10.1.2.0/24"),
    }));
    const leaf1 = deriveEcmp(state)[0]!;
    expect(leaf1.pathCount).toBeNull();
    expect(leaf1.status).toBe("unknown");
  });

  test("an unavailable routes facet is unknown", () => {
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-2", (node) => ({
      ...node,
      routes: null,
      unavailable: [{ facet: "routes", reason: "show ip route failed" }],
    }));
    expect(deriveEcmp(state)[1]!.status).toBe("unknown");
  });
});

describe("reachability", () => {
  test("baseline: both hosts 3/3 succeeded", () => {
    expect(deriveReachability(BASELINE_NETWORK_STATE)).toEqual([
      { host: "host-1", probe: { target: "10.1.2.10", sent: 3, received: 3 }, status: "succeeded" },
      { host: "host-2", probe: { target: "10.1.1.10", sent: 3, received: 3 }, status: "succeeded" },
    ]);
  });

  test("an observed total loss is failed, an unavailable probe is unavailable", () => {
    const lossy = withNode(BASELINE_NETWORK_STATE, "host-1", (node) => ({
      ...node,
      reachability: [
        { from_node: "host-1", target: "10.1.2.10", sent: 3, received: 0, succeeded: false },
      ],
    }));
    expect(deriveReachability(lossy)[0]!.status).toBe("failed");

    const unavailable = withNode(BASELINE_NETWORK_STATE, "host-1", (node) => ({
      ...node,
      reachability: null,
      unavailable: [{ facet: "reachability", reason: "ping could not run" }],
    }));
    const entry = deriveReachability(unavailable)[0]!;
    expect(entry.status).toBe("unavailable");
    expect(entry.probe).toBeNull();
  });
});

describe("network metrics by scenario", () => {
  test("healthy baseline", () => {
    const summary = deriveNetworkSummary(BASELINE_NETWORK_STATE, []);
    expect(summary.bgp).toEqual({ expected: 4, established: 4, degraded: 0, unknown: 0 });
    expect(summary.ecmp.map((e) => e.pathCount)).toEqual([2, 2]);
    expect(summary.reachability.every((r) => r.status === "succeeded")).toBe(true);
    expect(summary.openIncidentCount).toBe(0);
    expect(summary.status).toBe("healthy");
  });

  test("proven failure: 3/4 BGP, link down, ECMP reduced, reachability ok, 2 OPEN incidents", () => {
    const summary = deriveNetworkSummary(DEGRADED_NETWORK_STATE, failureIncidents());
    expect(summary.bgp).toEqual({ expected: 4, established: 3, degraded: 1, unknown: 0 });
    expect(summary.links.filter((l) => l.state === "down")).toHaveLength(1);
    expect(summary.ecmp.map((e) => e.pathCount)).toEqual([1, 1]);
    expect(summary.reachability.every((r) => r.status === "succeeded")).toBe(true);
    expect(summary.openIncidentCount).toBe(2);
    expect(summary.status).toBe("degraded");
  });

  test("recovery: 4/4 BGP, ECMP restored, reachability ok, incidents RESOLVED not removed", () => {
    const summary = deriveNetworkSummary(recoveredNetworkState(), resolvedFailureIncidents());
    expect(summary.bgp.established).toBe(4);
    expect(summary.ecmp.map((e) => e.pathCount)).toEqual([2, 2]);
    expect(summary.reachability.every((r) => r.status === "succeeded")).toBe(true);
    expect(summary.openIncidentCount).toBe(0);
    expect(summary.status).toBe("healthy");
  });

  test("RESOLVED incidents are not counted as OPEN", () => {
    expect(
      deriveNetworkSummary(BASELINE_NETWORK_STATE, resolvedFailureIncidents()).openIncidentCount,
    ).toBe(0);
  });
});

describe("presentation status is conservative", () => {
  test("missing data never yields healthy", () => {
    expect(deriveNetworkSummary(allBgpUnavailableState(), []).status).toBe("unknown");
    expect(deriveNetworkSummary(null, []).status).toBe("unknown");
  });

  test("an unloaded incident list shows no count but does not alter network status", () => {
    const summary = deriveNetworkSummary(BASELINE_NETWORK_STATE, null);
    expect(summary.openIncidentCount).toBeNull();
    expect(summary.status).toBe("healthy");
  });

  test("an unknown link or missing route keeps an otherwise-healthy network unknown", () => {
    const unknownLink = setInterfaceState(BASELINE_NETWORK_STATE, "host-1", "eth1", "unknown");
    expect(deriveNetworkSummary(unknownLink, []).status).toBe("unknown");
    const noRoute = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      routes: [],
    }));
    expect(deriveNetworkSummary(noRoute, []).status).toBe("unknown");
  });

  test("each explicit failure degrades on its own", () => {
    expect(
      deriveNetworkSummary(setInterfaceState(BASELINE_NETWORK_STATE, "host-2", "eth1", "down"), [])
        .status,
    ).toBe("degraded");
    const lossy = withNode(BASELINE_NETWORK_STATE, "host-2", (node) => ({
      ...node,
      reachability: [
        { from_node: "host-2", target: "10.1.1.10", sent: 3, received: 1, succeeded: false },
      ],
    }));
    expect(deriveNetworkSummary(lossy, []).status).toBe("degraded");
  });

  test("incident lifecycle never changes network status (endpoint skew)", () => {
    // Network recovered, incident endpoint briefly still says OPEN + Critical.
    const skewed = deriveNetworkSummary(recoveredNetworkState(), failureIncidents());
    expect(skewed.status).toBe("healthy");
    expect(skewed.bgp.established).toBe(4);
    expect(skewed.openIncidentCount).toBe(2);
    // The converse: network degraded, incidents not yet created.
    const early = deriveNetworkSummary(DEGRADED_NETWORK_STATE, []);
    expect(early.status).toBe("degraded");
    expect(early.openIncidentCount).toBe(0);
    // An unknown network stays unknown regardless of incidents.
    expect(deriveNetworkSummary(allBgpUnavailableState(), resolvedFailureIncidents()).status).toBe(
      "unknown",
    );
  });

  test("an unrelated Critical incident does not change network status", () => {
    const unrelated = bgpDownIncident("e2e-tel-device", "10.0.0.2");
    expect(deriveNetworkSummary(BASELINE_NETWORK_STATE, [unrelated]).status).toBe("healthy");
  });

  test("an explicit failure outranks unknown data elsewhere", () => {
    const state = allBgpUnavailableState();
    const down = setInterfaceState(state, "leaf-1", "eth1", "down");
    expect(deriveNetworkSummary(down, []).status).toBe("degraded");
  });
});

describe("workspace incident scope", () => {
  const policy: IncidentResponse = {
    ...bgpDownIncident("lab1-leaf-1", "10.255.0.0"),
    incident_id: "policy-1",
    source: "POLICY_VIOLATION",
    rule_ref: "policy-acl-external-in",
    affected_resource: "interface:eth1:acl_in",
    evidence: {
      source_snapshot_id: "s",
      violation_type: "MISSING_REQUIRED_ACL",
      expected_acl_name: "A",
      actual_acl_name: null,
      interface_name: "eth1",
      direction: "in",
    },
  };

  test("only OPEN ANOMALY incidents on Lab 1 routers are counted", () => {
    const list = [
      ...failureIncidents(),
      policy, // Lab 1 device but not ANOMALY
      bgpDownIncident("spine-01", "10.9.9.9"), // other device
      bgpDownIncident("lab1-spine-2", "10.255.0.7", { status: "RESOLVED" }),
    ];
    expect(deriveNetworkSummary(BASELINE_NETWORK_STATE, list).openIncidentCount).toBe(2);
    expect(openIncidents(list)).toHaveLength(2);
  });

  test("the scoped list keeps RESOLVED Lab 1 history and drops unrelated incidents", () => {
    const list = [...resolvedFailureIncidents(), policy, bgpDownIncident("spine-01", "10.9.9.9")];
    expect(lab1OperationalIncidents(list).map((i) => i.incident_id)).toEqual(
      resolvedFailureIncidents().map((i) => i.incident_id),
    );
  });

  test("an unrelated policy incident does not mark a node", () => {
    expect(openIncidentsForDevice([policy], "lab1-leaf-1")).toEqual([]);
  });
});

describe("observation age", () => {
  test("is server-anchored: retrieved_at minus collected_at", () => {
    expect(observationAgeMs(BASELINE_NETWORK_STATE)).toBe(3000);
  });

  test("never negative", () => {
    expect(
      observationAgeMs({ ...BASELINE_NETWORK_STATE, retrieved_at: "2026-10-07T11:59:00Z" }),
    ).toBe(0);
  });

  test.each([
    [0, "0 s"],
    [3000, "3 s"],
    [59_999, "59 s"],
    [60_000, "1 min"],
    [125_000, "2 min 5 s"],
    [3_600_000, "1 h"],
    [5_400_000, "1 h 30 min"],
    [90_000_000, "1 d 1 h"],
  ])("formats %d ms as %s", (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });
});

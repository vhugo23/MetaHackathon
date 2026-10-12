/**
 * Pure presentation derivations over a `NetworkStateResponse` and the fixed
 * declared Lab 1 topology (NPE-1D2). Nothing here is persisted, sent to the
 * backend, or a root-cause statement. The guiding rule everywhere: missing,
 * unavailable or unknown data is UNKNOWN — it is never promoted to up /
 * Established / healthy, and never demoted to "down".
 */
import type { IncidentResponse } from "../api/types";
import type { BgpSessionState, NetworkNode, NetworkStateResponse } from "../api/networkStateTypes";
import {
  LAB1_ECMP_EXPECTATIONS,
  LAB1_LINKS,
  LAB1_NODES,
  expectedAdjacencyCount,
  fabricLinks,
  type TopologyEndpoint,
  type TopologyLink,
} from "./lab1Topology";

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

export function findNode(state: NetworkStateResponse | null, nodeId: string): NetworkNode | null {
  return state?.nodes.find((node) => node.node_id === nodeId) ?? null;
}

// ---------------------------------------------------------------------------
// Physical link state — interface observations ONLY (never BGP).
// ---------------------------------------------------------------------------

export type PhysicalLinkState = "up" | "down" | "unknown";

export type EndpointInterfaceReason =
  "reported" | "node-missing" | "facet-unavailable" | "interface-missing";

export interface EndpointInterfaceObservation {
  node: string;
  interface: string;
  state: PhysicalLinkState;
  /** Why the state is `unknown` when the device did not explicitly report it. */
  reason: EndpointInterfaceReason;
}

export function observeEndpointInterface(
  state: NetworkStateResponse | null,
  endpoint: TopologyEndpoint,
): EndpointInterfaceObservation {
  const base = { node: endpoint.node, interface: endpoint.interface };
  const node = findNode(state, endpoint.node);
  if (node === null) {
    return { ...base, state: "unknown", reason: "node-missing" };
  }
  if (node.interfaces === null) {
    return { ...base, state: "unknown", reason: "facet-unavailable" };
  }
  const observed = node.interfaces.find((i) => i.name === endpoint.interface);
  if (observed === undefined) {
    return { ...base, state: "unknown", reason: "interface-missing" };
  }
  // The backend's own `unknown` is a report of unknown, kept as UNKNOWN.
  return { ...base, state: observed.oper_state, reason: "reported" };
}

/** Either endpoint explicitly down => DOWN; both explicitly up => UP; else UNKNOWN. */
export function combinePhysicalState(
  a: PhysicalLinkState,
  b: PhysicalLinkState,
): PhysicalLinkState {
  if (a === "down" || b === "down") {
    return "down";
  }
  if (a === "up" && b === "up") {
    return "up";
  }
  return "unknown";
}

export interface PhysicalLinkPresentation {
  link: TopologyLink;
  state: PhysicalLinkState;
  a: EndpointInterfaceObservation;
  b: EndpointInterfaceObservation;
}

export function derivePhysicalLink(
  state: NetworkStateResponse | null,
  link: TopologyLink,
): PhysicalLinkPresentation {
  const a = observeEndpointInterface(state, link.a);
  const b = observeEndpointInterface(state, link.b);
  return { link, state: combinePhysicalState(a.state, b.state), a, b };
}

export function derivePhysicalLinks(
  state: NetworkStateResponse | null,
): PhysicalLinkPresentation[] {
  return LAB1_LINKS.map((link) => derivePhysicalLink(state, link));
}

// ---------------------------------------------------------------------------
// BGP adjacency state — separate from physical state.
// ---------------------------------------------------------------------------

/** What one router's BGP facet says about its expected peer. */
export type BgpEndpointState =
  | { kind: "observed"; state: BgpSessionState; rawState: string | null }
  | { kind: "facet-unavailable" }
  | { kind: "node-missing" }
  | { kind: "neighbor-missing" };

export interface BgpEndpointObservation {
  node: string;
  expectedPeerIp: string;
  observation: BgpEndpointState;
}

export type BgpAdjacencyState = "established" | "degraded" | "unknown";

export interface BgpAdjacencyPresentation {
  link: TopologyLink;
  state: BgpAdjacencyState;
  a: BgpEndpointObservation;
  b: BgpEndpointObservation;
}

export function observeBgpEndpoint(
  state: NetworkStateResponse | null,
  endpoint: TopologyEndpoint,
  peer: TopologyEndpoint,
): BgpEndpointObservation {
  const base = { node: endpoint.node, expectedPeerIp: peer.address };
  const node = findNode(state, endpoint.node);
  if (node === null) {
    return { ...base, observation: { kind: "node-missing" } };
  }
  if (node.bgp === null) {
    return { ...base, observation: { kind: "facet-unavailable" } };
  }
  const neighbor = node.bgp.neighbors.find((n) => n.neighbor_ip === peer.address);
  if (neighbor === undefined) {
    return { ...base, observation: { kind: "neighbor-missing" } };
  }
  return {
    ...base,
    observation: { kind: "observed", state: neighbor.state, rawState: neighbor.raw_state },
  };
}

function isKnownNonEstablished(observation: BgpEndpointState): boolean {
  return (
    observation.kind === "observed" &&
    observation.state !== "Established" &&
    observation.state !== "Unknown"
  );
}

function isEstablished(observation: BgpEndpointState): boolean {
  return observation.kind === "observed" && observation.state === "Established";
}

export function combineBgpEndpoints(a: BgpEndpointState, b: BgpEndpointState): BgpAdjacencyState {
  if (isEstablished(a) && isEstablished(b)) {
    return "established";
  }
  if (isKnownNonEstablished(a) || isKnownNonEstablished(b)) {
    return "degraded";
  }
  return "unknown";
}

export function deriveBgpAdjacency(
  state: NetworkStateResponse | null,
  link: TopologyLink,
): BgpAdjacencyPresentation {
  const a = observeBgpEndpoint(state, link.a, link.b);
  const b = observeBgpEndpoint(state, link.b, link.a);
  return { link, state: combineBgpEndpoints(a.observation, b.observation), a, b };
}

/** The four router-to-router relationships — NOT eight directional neighbors. */
export function deriveBgpAdjacencies(
  state: NetworkStateResponse | null,
): BgpAdjacencyPresentation[] {
  return fabricLinks().map((link) => deriveBgpAdjacency(state, link));
}

export interface BgpAdjacencySummary {
  expected: number;
  established: number;
  degraded: number;
  unknown: number;
}

export function summarizeBgpAdjacencies(
  adjacencies: readonly BgpAdjacencyPresentation[],
): BgpAdjacencySummary {
  const count = (s: BgpAdjacencyState) => adjacencies.filter((a) => a.state === s).length;
  return {
    expected: adjacencies.length,
    established: count("established"),
    degraded: count("degraded"),
    unknown: count("unknown"),
  };
}

// ---------------------------------------------------------------------------
// ECMP redundancy
// ---------------------------------------------------------------------------

export type EcmpStatus = "intact" | "reduced" | "unknown";

export interface EcmpPresentation {
  node: string;
  prefix: string;
  towardsHost: string;
  expectedPaths: number;
  /** Observed path count, or `null` when not observed (never `0` by default). */
  pathCount: number | null;
  status: EcmpStatus;
}

export function deriveEcmp(state: NetworkStateResponse | null): EcmpPresentation[] {
  return LAB1_ECMP_EXPECTATIONS.map((expectation) => {
    const node = findNode(state, expectation.node);
    const route =
      node?.routes?.find((r) => r.prefix === expectation.prefix && r.protocol === "bgp") ?? null;
    const pathCount = route === null ? null : route.ecmp_path_count;
    let status: EcmpStatus = "unknown";
    if (pathCount !== null) {
      status = pathCount >= expectation.expectedPaths ? "intact" : "reduced";
    }
    return { ...expectation, pathCount, status };
  });
}

// ---------------------------------------------------------------------------
// Host reachability
// ---------------------------------------------------------------------------

export type ReachabilityStatus = "succeeded" | "failed" | "unavailable";

export interface ReachabilityPresentation {
  host: string;
  /** `null` when the host did not report a probe (unavailable / missing). */
  probe: { target: string; sent: number; received: number } | null;
  status: ReachabilityStatus;
}

export function deriveReachability(state: NetworkStateResponse | null): ReachabilityPresentation[] {
  return LAB1_NODES.filter((n) => n.role === "host").map((host) => {
    const probe = findNode(state, host.id)?.reachability?.[0] ?? null;
    if (probe === null) {
      return { host: host.id, probe: null, status: "unavailable" };
    }
    return {
      host: host.id,
      probe: { target: probe.target, sent: probe.sent, received: probe.received },
      status: probe.succeeded ? "succeeded" : "failed",
    };
  });
}

// ---------------------------------------------------------------------------
// Observation age
// ---------------------------------------------------------------------------

/**
 * Server-anchored age (`retrieved_at - collected_at`), so browser clock skew
 * cannot distort it. Never negative. This is display only: no freshness
 * threshold exists in D2.
 */
export function observationAgeMs(state: NetworkStateResponse): number {
  return Math.max(0, Date.parse(state.retrieved_at) - Date.parse(state.collected_at));
}

export function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(Math.max(0, ms) / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds} s`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) {
    const seconds = totalSeconds % 60;
    return seconds === 0 ? `${totalMinutes} min` : `${totalMinutes} min ${seconds} s`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 24) {
    return minutes === 0 ? `${hours} h` : `${hours} h ${minutes} min`;
  }
  const days = Math.floor(hours / 24);
  const remainderHours = hours % 24;
  return remainderHours === 0 ? `${days} d` : `${days} d ${remainderHours} h`;
}

// ---------------------------------------------------------------------------
// Incidents
// ---------------------------------------------------------------------------

const LAB1_DEVICE_IDS: ReadonlySet<string> = new Set(
  LAB1_NODES.flatMap((n) => (n.deviceId === null ? [] : [n.deviceId])),
);

/**
 * The workspace's incident scope: ANOMALY incidents on one of the fixed Lab 1
 * router device IDs. Unrelated incidents (policy/config, other devices) stay
 * available, unchanged, in the global `#tools` view and never affect the
 * workspace's counts.
 */
export function isLab1OperationalIncident(incident: IncidentResponse): boolean {
  return incident.source === "ANOMALY" && LAB1_DEVICE_IDS.has(incident.device_id);
}

export function lab1OperationalIncidents(
  incidents: readonly IncidentResponse[],
): IncidentResponse[] {
  return incidents.filter(isLab1OperationalIncident);
}

/** OPEN incidents in the Lab 1 operational scope. */
export function openIncidents(incidents: readonly IncidentResponse[]): IncidentResponse[] {
  return lab1OperationalIncidents(incidents).filter((incident) => incident.status === "OPEN");
}

export function openIncidentsForDevice(
  incidents: readonly IncidentResponse[],
  deviceId: string | null,
): IncidentResponse[] {
  if (deviceId === null) {
    return [];
  }
  return openIncidents(incidents).filter((incident) => incident.device_id === deviceId);
}

// ---------------------------------------------------------------------------
// Router node summary
// ---------------------------------------------------------------------------

export interface RouterBgpSummary {
  established: number;
  expected: number;
  /** True when at least one expected peer is not explicitly observed. */
  uncertain: boolean;
}

export function summarizeRouterBgp(
  state: NetworkStateResponse | null,
  nodeId: string,
): RouterBgpSummary {
  let established = 0;
  let uncertain = false;
  for (const link of fabricLinks()) {
    const endpoint = link.a.node === nodeId ? link.a : link.b.node === nodeId ? link.b : null;
    if (endpoint === null) {
      continue;
    }
    const peer = endpoint === link.a ? link.b : link.a;
    const { observation } = observeBgpEndpoint(state, endpoint, peer);
    if (isEstablished(observation)) {
      established += 1;
    }
    if (observation.kind !== "observed" || observation.state === "Unknown") {
      uncertain = true;
    }
  }
  return { established, expected: expectedAdjacencyCount(nodeId), uncertain };
}

// ---------------------------------------------------------------------------
// Overall presentation status (frontend-only, not persisted, not RCA)
// ---------------------------------------------------------------------------

export type PresentationStatus = "healthy" | "degraded" | "unknown";

export interface NetworkSummary {
  links: PhysicalLinkPresentation[];
  adjacencies: BgpAdjacencyPresentation[];
  bgp: BgpAdjacencySummary;
  ecmp: EcmpPresentation[];
  reachability: ReachabilityPresentation[];
  /** `null` while the incident list is not loaded — never treated as zero. */
  openIncidentCount: number | null;
  status: PresentationStatus;
}

/**
 * Conservative aggregate derived ONLY from GET /network/state observations
 * (incident lifecycle is a separate, independently fetched read and is never
 * an input, so a skew between the two endpoints cannot change it):
 *   DEGRADED — any explicit failure: a link down, an adjacency with a known
 *              non-Established endpoint, a failed probe, or reduced ECMP;
 *   HEALTHY  — only when every physical link is up, every adjacency is
 *              Established, every ECMP route is intact and every probe
 *              succeeded;
 *   UNKNOWN  — everything else (any needed observation missing/unknown).
 * `incidents` only feeds the separately displayed open-incident count
 * (`null` while not loaded).
 */
export function deriveNetworkSummary(
  state: NetworkStateResponse | null,
  incidents: readonly IncidentResponse[] | null,
): NetworkSummary {
  const links = derivePhysicalLinks(state);
  const adjacencies = deriveBgpAdjacencies(state);
  const bgp = summarizeBgpAdjacencies(adjacencies);
  const ecmp = deriveEcmp(state);
  const reachability = deriveReachability(state);
  const open = incidents === null ? null : openIncidents(incidents);

  const anyDegraded =
    links.some((l) => l.state === "down") ||
    bgp.degraded > 0 ||
    ecmp.some((e) => e.status === "reduced") ||
    reachability.some((r) => r.status === "failed");

  const allHealthy =
    state !== null &&
    links.every((l) => l.state === "up") &&
    bgp.established === bgp.expected &&
    ecmp.every((e) => e.status === "intact") &&
    reachability.every((r) => r.status === "succeeded");

  const status: PresentationStatus = anyDegraded ? "degraded" : allHealthy ? "healthy" : "unknown";

  return {
    links,
    adjacencies,
    bgp,
    ecmp,
    reachability,
    openIncidentCount: open === null ? null : open.length,
    status,
  };
}

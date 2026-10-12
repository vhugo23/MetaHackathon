// ---------------------------------------------------------------------------
// GET /network/state (NPE-1D1B) — see docs/frontend-api-contract.md Section 8B.
//
// Unlike the forward-compatible incident guards in types.ts, the enum-valued
// fields here are validated against their exact backend strings: a value this
// client does not know is rejected as a malformed response rather than being
// silently coerced into something it might be mistaken for (e.g. an unknown
// BGP state rendered as "down").
// ---------------------------------------------------------------------------

export const NODE_ROLES = ["router", "host"] as const;
export type NodeRole = (typeof NODE_ROLES)[number];

export const INTERFACE_OPER_STATES = ["up", "down", "unknown"] as const;
export type InterfaceOperState = (typeof INTERFACE_OPER_STATES)[number];

export const BGP_SESSION_STATES = [
  "Idle",
  "Connect",
  "Active",
  "OpenSent",
  "OpenConfirm",
  "Established",
  "Unknown",
] as const;
export type BgpSessionState = (typeof BGP_SESSION_STATES)[number];

export const OBSERVATION_FACETS = ["interfaces", "bgp", "routes", "reachability"] as const;
export type ObservationFacet = (typeof OBSERVATION_FACETS)[number];

export interface NetworkStateSource {
  collector: string;
  lab_name: string;
}

export interface NetworkInterface {
  name: string;
  oper_state: InterfaceOperState;
  addresses: string[];
}

export interface NetworkBgpNeighbor {
  neighbor_ip: string;
  remote_as: number | null;
  state: BgpSessionState;
  raw_state: string | null;
  prefixes_received: number | null;
  uptime_ms: number | null;
}

export interface NetworkBgp {
  local_as: number;
  router_id: string;
  established_count: number;
  neighbors: NetworkBgpNeighbor[];
}

export interface NetworkNextHop {
  ip: string | null;
  interface: string | null;
}

export interface NetworkRoute {
  prefix: string;
  protocol: string;
  ecmp_path_count: number;
  next_hops: NetworkNextHop[];
}

export interface NetworkReachability {
  from_node: string;
  target: string;
  sent: number;
  received: number;
  succeeded: boolean;
}

export interface NetworkUnavailableObservation {
  facet: ObservationFacet;
  reason: string;
}

/**
 * Facet applicability (backend contract): `null` means "unavailable" for a
 * facet that applies to the node's role (see `unavailable[]`), and "not
 * applicable" for one that does not (`bgp`/`routes` on a host, `reachability`
 * on a router).
 */
export interface NetworkNode {
  node_id: string;
  device_id: string | null;
  role: NodeRole;
  interfaces: NetworkInterface[] | null;
  bgp: NetworkBgp | null;
  routes: NetworkRoute[] | null;
  reachability: NetworkReachability[] | null;
  unavailable: NetworkUnavailableObservation[];
}

export interface NetworkStateResponse {
  collection_id: string;
  collected_at: string;
  retrieved_at: string;
  source: NetworkStateSource;
  nodes: NetworkNode[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isNullableNonNegativeInteger(value: unknown): value is number | null {
  return value === null || isNonNegativeInteger(value);
}

function isOneOf<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

function isParseableTimestamp(value: unknown): value is string {
  return isNonEmptyString(value) && !Number.isNaN(Date.parse(value));
}

function isArrayOf<T>(value: unknown, guard: (item: unknown) => item is T): value is T[] {
  return Array.isArray(value) && value.every(guard);
}

function isNetworkStateSource(value: unknown): value is NetworkStateSource {
  return isRecord(value) && isNonEmptyString(value.collector) && isNonEmptyString(value.lab_name);
}

export function isNetworkInterface(value: unknown): value is NetworkInterface {
  return (
    isRecord(value) &&
    isNonEmptyString(value.name) &&
    isOneOf(INTERFACE_OPER_STATES, value.oper_state) &&
    isArrayOf(value.addresses, isNonEmptyString)
  );
}

export function isNetworkBgpNeighbor(value: unknown): value is NetworkBgpNeighbor {
  return (
    isRecord(value) &&
    isNonEmptyString(value.neighbor_ip) &&
    isNullableNonNegativeInteger(value.remote_as) &&
    isOneOf(BGP_SESSION_STATES, value.state) &&
    isNullableString(value.raw_state) &&
    isNullableNonNegativeInteger(value.prefixes_received) &&
    isNullableNonNegativeInteger(value.uptime_ms)
  );
}

export function isNetworkBgp(value: unknown): value is NetworkBgp {
  return (
    isRecord(value) &&
    isNonNegativeInteger(value.local_as) &&
    isNonEmptyString(value.router_id) &&
    isNonNegativeInteger(value.established_count) &&
    isArrayOf(value.neighbors, isNetworkBgpNeighbor)
  );
}

export function isNetworkNextHop(value: unknown): value is NetworkNextHop {
  return isRecord(value) && isNullableString(value.ip) && isNullableString(value.interface);
}

export function isNetworkRoute(value: unknown): value is NetworkRoute {
  return (
    isRecord(value) &&
    isNonEmptyString(value.prefix) &&
    isNonEmptyString(value.protocol) &&
    isNonNegativeInteger(value.ecmp_path_count) &&
    isArrayOf(value.next_hops, isNetworkNextHop)
  );
}

export function isNetworkReachability(value: unknown): value is NetworkReachability {
  return (
    isRecord(value) &&
    isNonEmptyString(value.from_node) &&
    isNonEmptyString(value.target) &&
    isNonNegativeInteger(value.sent) &&
    isNonNegativeInteger(value.received) &&
    typeof value.succeeded === "boolean"
  );
}

export function isNetworkUnavailableObservation(
  value: unknown,
): value is NetworkUnavailableObservation {
  return (
    isRecord(value) && isOneOf(OBSERVATION_FACETS, value.facet) && isNonEmptyString(value.reason)
  );
}

export function isNetworkNode(value: unknown): value is NetworkNode {
  return (
    isRecord(value) &&
    isNonEmptyString(value.node_id) &&
    (value.device_id === null || isNonEmptyString(value.device_id)) &&
    isOneOf(NODE_ROLES, value.role) &&
    (value.interfaces === null || isArrayOf(value.interfaces, isNetworkInterface)) &&
    (value.bgp === null || isNetworkBgp(value.bgp)) &&
    (value.routes === null || isArrayOf(value.routes, isNetworkRoute)) &&
    (value.reachability === null || isArrayOf(value.reachability, isNetworkReachability)) &&
    isArrayOf(value.unavailable, isNetworkUnavailableObservation)
  );
}

/**
 * Full structural validation of the nested response. `collected_at` and
 * `retrieved_at` must be parseable instants because stale-response
 * protection compares them as instants, never as strings.
 */
export function isNetworkStateResponse(value: unknown): value is NetworkStateResponse {
  return (
    isRecord(value) &&
    isNonEmptyString(value.collection_id) &&
    isParseableTimestamp(value.collected_at) &&
    isParseableTimestamp(value.retrieved_at) &&
    isNetworkStateSource(value.source) &&
    isArrayOf(value.nodes, isNetworkNode)
  );
}

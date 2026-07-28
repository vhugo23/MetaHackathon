export type Severity = "Critical" | "High" | "Medium" | "Low";
export type IncidentStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
export type IncidentSource = "POLICY_VIOLATION" | "DRIFT" | "ANOMALY";
export type ViolationType = "MISSING_REQUIRED_ACL" | "TARGET_INTERFACE_MISSING";
export type Direction = "in" | "out";

export interface PolicyViolationIncidentEvidenceResponse {
  source_snapshot_id: string;
  violation_type: ViolationType | (string & {});
  expected_acl_name: string;
  actual_acl_name: string | null;
  interface_name: string;
  direction: Direction | (string & {});
}

// ---------------------------------------------------------------------------
// ANOMALY incident evidence (rule_ref-discriminated: RULE-CPU-HIGH /
// RULE-LINK-FLAP / RULE-BGP-DOWN) — see docs/frontend-api-contract.md
// Section 6. The backend discriminates which shape applies via the
// incident's own `source`/`rule_ref` fields; no synthetic discriminator
// field exists inside `evidence` itself.
// ---------------------------------------------------------------------------

export interface CpuSampleEvidenceResponse {
  timestamp: string;
  cpu_utilization_pct: number;
}

export interface CpuHighEvidenceResponse {
  samples: CpuSampleEvidenceResponse[];
}

export interface InterfaceTransitionEvidenceResponse {
  timestamp: string;
  oper_state: string;
}

export interface LinkFlapEvidenceResponse {
  interface_name: string;
  transitions: InterfaceTransitionEvidenceResponse[];
}

export interface BgpDownEvidenceResponse {
  neighbor_ip: string;
  previous_state: string;
  state: string;
}

export type IncidentEvidenceResponse =
  | PolicyViolationIncidentEvidenceResponse
  | CpuHighEvidenceResponse
  | LinkFlapEvidenceResponse
  | BgpDownEvidenceResponse;

export interface IncidentResponse {
  incident_id: string;
  fingerprint: string;
  device_id: string;
  source: IncidentSource | (string & {});
  rule_ref: string;
  affected_resource: string;
  severity: Severity | (string & {});
  status: IncidentStatus | (string & {});
  evidence: IncidentEvidenceResponse;
  recommendation: string;
  created_at: string;
  last_seen_at: string;
  occurrence_count: number;
  updated_at: string;
  resolved_at: string | null;
}

export interface ApiErrorResponse {
  code: string;
  detail: string;
}

// ---------------------------------------------------------------------------
// POST /devices/{device_id}/config
// ---------------------------------------------------------------------------

// The frontend supports exactly the two currently registered production
// vendors (Gate 8A-E) — never an arbitrary string.
export type SupportedVendor = "cisco-ios-xe" | "arista-eos";

export interface ConfigurationSubmissionRequest {
  vendor: SupportedVendor;
  raw_config_text: string;
}

export interface NormalizedInterfaceResponse {
  name: string;
  description: string | null;
  ip_address: string | null;
  mtu: number | null;
  admin_state: string;
  acl_in: string | null;
  acl_out: string | null;
}

export interface NormalizedBgpNeighborResponse {
  neighbor_ip: string;
  remote_as: number;
}

export interface NormalizedRoutingResponse {
  bgp_neighbors: NormalizedBgpNeighborResponse[];
}

export interface NormalizedAclEntryResponse {
  sequence: number;
  action: string;
  protocol: string;
  source: string;
  destination: string;
}

export interface NormalizedAclResponse {
  name: string;
  entries: NormalizedAclEntryResponse[];
}

export interface NormalizedConfigurationResponse {
  hostname: string;
  interfaces: NormalizedInterfaceResponse[];
  routing: NormalizedRoutingResponse;
  acls: NormalizedAclResponse[];
}

export interface ConfigurationSubmissionResponse {
  device_id: string;
  snapshot_id: string;
  normalized_config: NormalizedConfigurationResponse;
  violations_detected: number;
  incidents_created: number;
  incidents_updated: number;
}

// ---------------------------------------------------------------------------
// GET /devices/{device_id}/telemetry/recent
// ---------------------------------------------------------------------------

export interface InterfaceStateResponse {
  name: string;
  oper_state: string;
}

export interface BgpSessionResponse {
  neighbor_ip: string;
  state: string;
}

export interface TelemetrySampleResponse {
  device_id: string;
  sampled_at: string;
  cpu_utilization_pct: number;
  memory_utilization_pct: number;
  interface_error_rate: number;
  interface_states: InterfaceStateResponse[];
  bgp_sessions: BgpSessionResponse[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function isNullableInteger(value: unknown): value is number | null {
  return value === null || isInteger(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Structural only, deliberately not a closed-enum check: `severity`/
 * `status`/`source`/`violation_type`/`direction` are validated as
 * non-empty strings, not against the current literal unions above, so a
 * backend-added future enum value is still accepted (and rendered as
 * plain text) rather than making the whole payload fail closed.
 */
export function isPolicyViolationIncidentEvidenceResponse(
  value: unknown,
): value is PolicyViolationIncidentEvidenceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.source_snapshot_id) &&
    isNonEmptyString(value.violation_type) &&
    isNonEmptyString(value.expected_acl_name) &&
    isNullableString(value.actual_acl_name) &&
    isNonEmptyString(value.interface_name) &&
    isNonEmptyString(value.direction)
  );
}

/** Structural only, per `CpuSampleEvidenceResponse`'s own fields. */
export function isCpuSampleEvidenceResponse(value: unknown): value is CpuSampleEvidenceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return isNonEmptyString(value.timestamp) && isFiniteNumber(value.cpu_utilization_pct);
}

export function isCpuHighEvidenceResponse(value: unknown): value is CpuHighEvidenceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return Array.isArray(value.samples) && value.samples.every(isCpuSampleEvidenceResponse);
}

export function isInterfaceTransitionEvidenceResponse(
  value: unknown,
): value is InterfaceTransitionEvidenceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return isNonEmptyString(value.timestamp) && isNonEmptyString(value.oper_state);
}

export function isLinkFlapEvidenceResponse(value: unknown): value is LinkFlapEvidenceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.interface_name) &&
    Array.isArray(value.transitions) &&
    value.transitions.every(isInterfaceTransitionEvidenceResponse)
  );
}

export function isBgpDownEvidenceResponse(value: unknown): value is BgpDownEvidenceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.neighbor_ip) &&
    isNonEmptyString(value.previous_state) &&
    isNonEmptyString(value.state)
  );
}

/**
 * Structural-only union membership check — accepts a value that matches
 * *any* of the four known evidence shapes, without regard to the sibling
 * `source`/`rule_ref` fields on the containing incident. This keeps
 * `isIncidentResponse` a pure shape check, consistent with its existing
 * treatment of `severity`/`status`/`source` as non-empty strings rather
 * than closed enums. Confirming that a *specific* rule_ref's evidence
 * actually matches its expected shape (e.g. rejecting a RULE-CPU-HIGH
 * incident carrying link-flap evidence) is a stricter, source/rule_ref-aware
 * check layered on top in `incidents.ts` — mirroring how `resolveIncident`
 * already layers its own stricter semantic check on top of this same
 * function.
 */
function isIncidentEvidenceResponse(value: unknown): value is IncidentEvidenceResponse {
  return (
    isPolicyViolationIncidentEvidenceResponse(value) ||
    isCpuHighEvidenceResponse(value) ||
    isLinkFlapEvidenceResponse(value) ||
    isBgpDownEvidenceResponse(value)
  );
}

export function isIncidentResponse(value: unknown): value is IncidentResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.incident_id) &&
    isNonEmptyString(value.fingerprint) &&
    isNonEmptyString(value.device_id) &&
    isNonEmptyString(value.source) &&
    isNonEmptyString(value.rule_ref) &&
    isNonEmptyString(value.affected_resource) &&
    isNonEmptyString(value.severity) &&
    isNonEmptyString(value.status) &&
    isIncidentEvidenceResponse(value.evidence) &&
    typeof value.recommendation === "string" &&
    isNonEmptyString(value.created_at) &&
    isNonEmptyString(value.last_seen_at) &&
    isNonNegativeInteger(value.occurrence_count) &&
    isNonEmptyString(value.updated_at) &&
    (value.resolved_at === null || isNonEmptyString(value.resolved_at))
  );
}

export function isNormalizedAclEntryResponse(value: unknown): value is NormalizedAclEntryResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isInteger(value.sequence) &&
    isNonEmptyString(value.action) &&
    isNonEmptyString(value.protocol) &&
    isNonEmptyString(value.source) &&
    isNonEmptyString(value.destination)
  );
}

export function isNormalizedAclResponse(value: unknown): value is NormalizedAclResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.name) &&
    Array.isArray(value.entries) &&
    value.entries.every(isNormalizedAclEntryResponse)
  );
}

export function isNormalizedInterfaceResponse(
  value: unknown,
): value is NormalizedInterfaceResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.name) &&
    isNullableString(value.description) &&
    isNullableString(value.ip_address) &&
    isNullableInteger(value.mtu) &&
    isNonEmptyString(value.admin_state) &&
    isNullableString(value.acl_in) &&
    isNullableString(value.acl_out)
  );
}

export function isNormalizedBgpNeighborResponse(
  value: unknown,
): value is NormalizedBgpNeighborResponse {
  if (!isRecord(value)) {
    return false;
  }
  return isNonEmptyString(value.neighbor_ip) && isInteger(value.remote_as);
}

export function isNormalizedRoutingResponse(value: unknown): value is NormalizedRoutingResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    Array.isArray(value.bgp_neighbors) && value.bgp_neighbors.every(isNormalizedBgpNeighborResponse)
  );
}

export function isNormalizedConfigurationResponse(
  value: unknown,
): value is NormalizedConfigurationResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.hostname) &&
    Array.isArray(value.interfaces) &&
    value.interfaces.every(isNormalizedInterfaceResponse) &&
    isNormalizedRoutingResponse(value.routing) &&
    Array.isArray(value.acls) &&
    value.acls.every(isNormalizedAclResponse)
  );
}

export function isConfigurationSubmissionResponse(
  value: unknown,
): value is ConfigurationSubmissionResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.device_id) &&
    isNonEmptyString(value.snapshot_id) &&
    isNormalizedConfigurationResponse(value.normalized_config) &&
    isNonNegativeInteger(value.violations_detected) &&
    isNonNegativeInteger(value.incidents_created) &&
    isNonNegativeInteger(value.incidents_updated)
  );
}

/**
 * Structural only, matching this document's forward-compatible convention:
 * `oper_state` is validated as a non-empty string, not the closed `"up" |
 * "down"` union, so an unrecognized future link-state value is still
 * accepted rather than rejecting the whole sample.
 */
export function isInterfaceStateResponse(value: unknown): value is InterfaceStateResponse {
  if (!isRecord(value)) {
    return false;
  }
  return isNonEmptyString(value.name) && isNonEmptyString(value.oper_state);
}

/** Structural only — `state` is a non-empty string, not a closed BGP-state union. */
export function isBgpSessionResponse(value: unknown): value is BgpSessionResponse {
  if (!isRecord(value)) {
    return false;
  }
  return isNonEmptyString(value.neighbor_ip) && isNonEmptyString(value.state);
}

export function isTelemetrySampleResponse(value: unknown): value is TelemetrySampleResponse {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isNonEmptyString(value.device_id) &&
    isNonEmptyString(value.sampled_at) &&
    isFiniteNumber(value.cpu_utilization_pct) &&
    isFiniteNumber(value.memory_utilization_pct) &&
    isFiniteNumber(value.interface_error_rate) &&
    Array.isArray(value.interface_states) &&
    value.interface_states.every(isInterfaceStateResponse) &&
    Array.isArray(value.bgp_sessions) &&
    value.bgp_sessions.every(isBgpSessionResponse)
  );
}

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

export interface IncidentResponse {
  incident_id: string;
  fingerprint: string;
  device_id: string;
  source: IncidentSource | (string & {});
  rule_ref: string;
  affected_resource: string;
  severity: Severity | (string & {});
  status: IncidentStatus | (string & {});
  evidence: PolicyViolationIncidentEvidenceResponse;
  recommendation: string;
  created_at: string;
  last_seen_at: string;
  occurrence_count: number;
}

export interface ApiErrorResponse {
  code: string;
  detail: string;
}

// ---------------------------------------------------------------------------
// POST /devices/{device_id}/config
// ---------------------------------------------------------------------------

export interface ConfigurationSubmissionRequest {
  vendor: "cisco-ios-xe";
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
    isPolicyViolationIncidentEvidenceResponse(value.evidence) &&
    typeof value.recommendation === "string" &&
    isNonEmptyString(value.created_at) &&
    isNonEmptyString(value.last_seen_at) &&
    isNonNegativeInteger(value.occurrence_count)
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

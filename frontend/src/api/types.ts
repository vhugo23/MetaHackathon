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

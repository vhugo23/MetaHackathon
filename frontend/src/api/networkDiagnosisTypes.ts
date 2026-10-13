// ---------------------------------------------------------------------------
// GET /network/diagnosis (NPE-1E1B backend, NPE-1E1C consumer) — see
// docs/frontend-api-contract.md Section 8C.
//
// Every enum-valued field is validated against its exact backend string: a
// value this client does not know is rejected as a malformed response rather
// than being coerced into something it might be mistaken for. The diagnosis
// type, confidence and evidence are backend-authoritative; nothing here (or
// anywhere in the frontend) derives them.
// ---------------------------------------------------------------------------

export const DIAGNOSIS_TYPES = [
  "PHYSICAL_LINK_FAILURE",
  "BGP_SESSION_FAILURE",
  "INSUFFICIENT_EVIDENCE",
] as const;
export type DiagnosisType = (typeof DIAGNOSIS_TYPES)[number];

export const DIAGNOSIS_CONFIDENCES = ["HIGH", "MEDIUM", "LOW"] as const;
export type DiagnosisConfidence = (typeof DIAGNOSIS_CONFIDENCES)[number];

export const EVIDENCE_ROLES = ["SUPPORTS", "CONTRADICTS", "UNKNOWN"] as const;
export type EvidenceRole = (typeof EVIDENCE_ROLES)[number];

export const EVIDENCE_KINDS = [
  "INTERFACE_STATE",
  "BGP_SESSION",
  "ADDRESS_MATCH",
  "ROUTE_NEXT_HOP",
  "ECMP_PATHS",
  "REACHABILITY",
] as const;
export type EvidenceKind = (typeof EVIDENCE_KINDS)[number];

export const ENDPOINT_INTERFACE_STATES = ["UP", "DOWN", "UNKNOWN"] as const;
export type EndpointInterfaceState = (typeof ENDPOINT_INTERFACE_STATES)[number];

export const ENDPOINT_BGP_STATES = ["DOWN", "ESTABLISHED", "OTHER", "UNKNOWN"] as const;
export type EndpointBgpState = (typeof ENDPOINT_BGP_STATES)[number];

export interface DiagnosisEndpoint {
  node_id: string;
  interface: string;
}

export interface DiagnosisResource {
  resource_id: string;
  endpoint_a: DiagnosisEndpoint;
  endpoint_b: DiagnosisEndpoint;
}

export interface InterfaceStateObserved {
  interface_name: string;
  state: EndpointInterfaceState;
}

export interface BgpSessionObserved {
  neighbor_ip: string;
  state: EndpointBgpState;
  raw_state: string | null;
}

export interface AddressObserved {
  interface_name: string;
  declared_address: string;
  observed_addresses: string[];
  matches: boolean | null;
}

export interface NextHopObserved {
  next_hop_ip: string;
  interface_name: string;
  active_route_count: number | null;
}

export interface EcmpPathsObserved {
  prefix: string;
  observed_paths: number | null;
  expected_paths: number;
  link_next_hop_ip: string;
  link_next_hop_present: boolean | null;
}

export interface ReachabilityObserved {
  target: string;
  sent: number | null;
  received: number | null;
}

interface EvidenceBase<K extends EvidenceKind, O> {
  kind: K;
  role: EvidenceRole;
  node_id: string;
  subject: string;
  observed: O;
}

/** Discriminated on `kind`: each kind carries exactly one `observed` shape. */
export type DiagnosisEvidence =
  | EvidenceBase<"INTERFACE_STATE", InterfaceStateObserved>
  | EvidenceBase<"BGP_SESSION", BgpSessionObserved>
  | EvidenceBase<"ADDRESS_MATCH", AddressObserved>
  | EvidenceBase<"ROUTE_NEXT_HOP", NextHopObserved>
  | EvidenceBase<"ECMP_PATHS", EcmpPathsObserved>
  | EvidenceBase<"REACHABILITY", ReachabilityObserved>;

export interface SymptomKey {
  device_id: string;
  affected_resource: string;
}

export interface LinkedIncident {
  incident_id: string;
  device_id: string;
  affected_resource: string;
  severity: string;
  status: string;
  created_at: string;
  last_seen_at: string;
  occurrence_count: number;
}

export interface Diagnosis {
  diagnosis_type: DiagnosisType;
  /** `null` exactly for `INSUFFICIENT_EVIDENCE`. */
  confidence: DiagnosisConfidence | null;
  resource: DiagnosisResource;
  evidence: DiagnosisEvidence[];
  symptom_keys: SymptomKey[];
  linked_incidents: LinkedIncident[];
}

export interface NetworkDiagnosisResponse {
  collection_id: string;
  collected_at: string;
  diagnoses: Diagnosis[];
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

function isNullableBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
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

function isEndpoint(value: unknown): value is DiagnosisEndpoint {
  return isRecord(value) && isNonEmptyString(value.node_id) && isNonEmptyString(value.interface);
}

function isResource(value: unknown): value is DiagnosisResource {
  return (
    isRecord(value) &&
    isNonEmptyString(value.resource_id) &&
    isEndpoint(value.endpoint_a) &&
    isEndpoint(value.endpoint_b)
  );
}

const OBSERVED_GUARDS: Record<EvidenceKind, (observed: unknown) => boolean> = {
  INTERFACE_STATE: (o) =>
    isRecord(o) &&
    isNonEmptyString(o.interface_name) &&
    isOneOf(ENDPOINT_INTERFACE_STATES, o.state),
  BGP_SESSION: (o) =>
    isRecord(o) &&
    isNonEmptyString(o.neighbor_ip) &&
    isOneOf(ENDPOINT_BGP_STATES, o.state) &&
    isNullableString(o.raw_state),
  ADDRESS_MATCH: (o) =>
    isRecord(o) &&
    isNonEmptyString(o.interface_name) &&
    isNonEmptyString(o.declared_address) &&
    isArrayOf(o.observed_addresses, isNonEmptyString) &&
    isNullableBoolean(o.matches),
  ROUTE_NEXT_HOP: (o) =>
    isRecord(o) &&
    isNonEmptyString(o.next_hop_ip) &&
    isNonEmptyString(o.interface_name) &&
    isNullableNonNegativeInteger(o.active_route_count),
  ECMP_PATHS: (o) =>
    isRecord(o) &&
    isNonEmptyString(o.prefix) &&
    isNullableNonNegativeInteger(o.observed_paths) &&
    isNonNegativeInteger(o.expected_paths) &&
    isNonEmptyString(o.link_next_hop_ip) &&
    isNullableBoolean(o.link_next_hop_present),
  REACHABILITY: (o) =>
    isRecord(o) &&
    isNonEmptyString(o.target) &&
    isNullableNonNegativeInteger(o.sent) &&
    isNullableNonNegativeInteger(o.received),
};

export function isDiagnosisEvidence(value: unknown): value is DiagnosisEvidence {
  return (
    isRecord(value) &&
    isOneOf(EVIDENCE_KINDS, value.kind) &&
    isOneOf(EVIDENCE_ROLES, value.role) &&
    isNonEmptyString(value.node_id) &&
    isNonEmptyString(value.subject) &&
    OBSERVED_GUARDS[value.kind](value.observed)
  );
}

function isSymptomKey(value: unknown): value is SymptomKey {
  return (
    isRecord(value) &&
    isNonEmptyString(value.device_id) &&
    isNonEmptyString(value.affected_resource)
  );
}

export function isLinkedIncident(value: unknown): value is LinkedIncident {
  return (
    isRecord(value) &&
    isNonEmptyString(value.incident_id) &&
    isNonEmptyString(value.device_id) &&
    isNonEmptyString(value.affected_resource) &&
    isNonEmptyString(value.severity) &&
    isNonEmptyString(value.status) &&
    isParseableTimestamp(value.created_at) &&
    isParseableTimestamp(value.last_seen_at) &&
    isNonNegativeInteger(value.occurrence_count)
  );
}

export function isDiagnosis(value: unknown): value is Diagnosis {
  if (!isRecord(value) || !isOneOf(DIAGNOSIS_TYPES, value.diagnosis_type)) {
    return false;
  }
  // Backend contract: confidence is null exactly for INSUFFICIENT_EVIDENCE.
  const confidenceOk =
    value.diagnosis_type === "INSUFFICIENT_EVIDENCE"
      ? value.confidence === null
      : isOneOf(DIAGNOSIS_CONFIDENCES, value.confidence);
  return (
    confidenceOk &&
    isResource(value.resource) &&
    isArrayOf(value.evidence, isDiagnosisEvidence) &&
    isArrayOf(value.symptom_keys, isSymptomKey) &&
    isArrayOf(value.linked_incidents, isLinkedIncident)
  );
}

/**
 * Full structural validation of the nested response. `collected_at` must be a
 * parseable instant because stale-response protection compares instants,
 * never strings.
 */
export function isNetworkDiagnosisResponse(value: unknown): value is NetworkDiagnosisResponse {
  return (
    isRecord(value) &&
    isNonEmptyString(value.collection_id) &&
    isParseableTimestamp(value.collected_at) &&
    isArrayOf(value.diagnoses, isDiagnosis)
  );
}

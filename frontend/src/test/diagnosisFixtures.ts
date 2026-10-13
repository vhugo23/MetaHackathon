import type {
  Diagnosis,
  DiagnosisEvidence,
  LinkedIncident,
  NetworkDiagnosisResponse,
} from "../api/networkDiagnosisTypes";

/** Aligned with DEGRADED_NETWORK_STATE in networkFixtures.ts. */
export const DEGRADED_COLLECTION_ID = "collection-degraded";
export const DEGRADED_COLLECTED_AT = "2026-10-07T12:01:00Z";

const LEAF_1_ETH1_SPINE_1_ETH1 = {
  resource_id: "leaf-1:eth1--spine-1:eth1",
  endpoint_a: { node_id: "leaf-1", interface: "eth1" },
  endpoint_b: { node_id: "spine-1", interface: "eth1" },
};

/** Mirrors the evidence the backend produces for the proven leaf-1 eth1 failure. */
export function physicalEvidence(): DiagnosisEvidence[] {
  return [
    {
      kind: "INTERFACE_STATE",
      role: "SUPPORTS",
      node_id: "leaf-1",
      subject: "eth1",
      observed: { interface_name: "eth1", state: "DOWN" },
    },
    {
      kind: "BGP_SESSION",
      role: "SUPPORTS",
      node_id: "leaf-1",
      subject: "10.255.0.0",
      observed: { neighbor_ip: "10.255.0.0", state: "DOWN", raw_state: "Active" },
    },
    {
      kind: "ADDRESS_MATCH",
      role: "CONTRADICTS",
      node_id: "spine-1",
      subject: "eth1",
      observed: {
        interface_name: "eth1",
        declared_address: "10.255.0.0",
        observed_addresses: ["10.255.0.0"],
        matches: true,
      },
    },
    {
      kind: "ROUTE_NEXT_HOP",
      role: "SUPPORTS",
      node_id: "leaf-1",
      subject: "10.255.0.0",
      observed: { next_hop_ip: "10.255.0.0", interface_name: "eth1", active_route_count: 0 },
    },
    {
      kind: "ECMP_PATHS",
      role: "SUPPORTS",
      node_id: "leaf-1",
      subject: "10.1.2.0/24",
      observed: {
        prefix: "10.1.2.0/24",
        observed_paths: 1,
        expected_paths: 2,
        link_next_hop_ip: "10.255.0.0",
        link_next_hop_present: false,
      },
    },
    {
      kind: "REACHABILITY",
      role: "UNKNOWN",
      node_id: "host-1",
      subject: "10.1.2.10",
      observed: { target: "10.1.2.10", sent: 3, received: 3 },
    },
  ];
}

export function linkedIncident(
  deviceId: string,
  neighborIp: string,
  overrides: Partial<LinkedIncident> = {},
): LinkedIncident {
  return {
    incident_id: `incident-${deviceId}-${neighborIp}`,
    device_id: deviceId,
    affected_resource: `bgp-neighbor:${neighborIp}`,
    severity: "Critical",
    status: "OPEN",
    created_at: "2026-10-07T12:01:00Z",
    last_seen_at: "2026-10-07T12:01:00Z",
    occurrence_count: 1,
    ...overrides,
  };
}

export function physicalDiagnosis(overrides: Partial<Diagnosis> = {}): Diagnosis {
  return {
    diagnosis_type: "PHYSICAL_LINK_FAILURE",
    confidence: "HIGH",
    resource: structuredClone(LEAF_1_ETH1_SPINE_1_ETH1),
    evidence: physicalEvidence(),
    symptom_keys: [
      { device_id: "lab1-leaf-1", affected_resource: "bgp-neighbor:10.255.0.0" },
      { device_id: "lab1-spine-1", affected_resource: "bgp-neighbor:10.255.0.1" },
    ],
    linked_incidents: [
      linkedIncident("lab1-leaf-1", "10.255.0.0"),
      linkedIncident("lab1-spine-1", "10.255.0.1"),
    ],
    ...overrides,
  };
}

/** Interfaces UP, BGP session down on the declared adjacency. */
export function bgpDiagnosis(overrides: Partial<Diagnosis> = {}): Diagnosis {
  return {
    diagnosis_type: "BGP_SESSION_FAILURE",
    confidence: "MEDIUM",
    resource: structuredClone(LEAF_1_ETH1_SPINE_1_ETH1),
    evidence: [
      {
        kind: "INTERFACE_STATE",
        role: "CONTRADICTS",
        node_id: "leaf-1",
        subject: "eth1",
        observed: { interface_name: "eth1", state: "UP" },
      },
      {
        kind: "BGP_SESSION",
        role: "SUPPORTS",
        node_id: "leaf-1",
        subject: "10.255.0.0",
        observed: { neighbor_ip: "10.255.0.0", state: "DOWN", raw_state: "Idle" },
      },
    ],
    symptom_keys: [{ device_id: "lab1-leaf-1", affected_resource: "bgp-neighbor:10.255.0.0" }],
    linked_incidents: [linkedIncident("lab1-leaf-1", "10.255.0.0")],
    ...overrides,
  };
}

export function insufficientDiagnosis(overrides: Partial<Diagnosis> = {}): Diagnosis {
  return {
    diagnosis_type: "INSUFFICIENT_EVIDENCE",
    confidence: null,
    resource: structuredClone(LEAF_1_ETH1_SPINE_1_ETH1),
    evidence: [
      {
        kind: "INTERFACE_STATE",
        role: "UNKNOWN",
        node_id: "leaf-1",
        subject: "eth1",
        observed: { interface_name: "eth1", state: "UNKNOWN" },
      },
      {
        kind: "BGP_SESSION",
        role: "CONTRADICTS",
        node_id: "spine-1",
        subject: "10.255.0.1",
        observed: { neighbor_ip: "10.255.0.1", state: "ESTABLISHED", raw_state: "Established" },
      },
    ],
    symptom_keys: [{ device_id: "lab1-leaf-1", affected_resource: "bgp-neighbor:10.255.0.0" }],
    linked_incidents: [],
    ...overrides,
  };
}

export function diagnosisResponse(
  diagnoses: Diagnosis[],
  overrides: Partial<NetworkDiagnosisResponse> = {},
): NetworkDiagnosisResponse {
  return {
    collection_id: DEGRADED_COLLECTION_ID,
    collected_at: DEGRADED_COLLECTED_AT,
    diagnoses,
    ...overrides,
  };
}

/** Aligned with BASELINE_NETWORK_STATE: a healthy network. */
export function healthyDiagnosis(): NetworkDiagnosisResponse {
  return diagnosisResponse([], {
    collection_id: "collection-baseline",
    collected_at: "2026-10-07T12:00:00Z",
  });
}

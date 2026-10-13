/**
 * Presentation-only helpers for the backend RCA (`GET /network/diagnosis`).
 *
 * Nothing here decides a diagnosis type, confidence or evidence role: those
 * are backend-authoritative and only formatted. The only logic is (1) whether
 * a diagnosis response belongs to the displayed snapshot and (2) matching a
 * diagnosis resource, by its structured endpoints, to an existing declared
 * physical link.
 */
import type {
  Diagnosis,
  DiagnosisConfidence,
  DiagnosisEvidence,
  DiagnosisResource,
  DiagnosisType,
  EvidenceRole,
  NetworkDiagnosisResponse,
} from "../api/networkDiagnosisTypes";
import type { NetworkStateResponse } from "../api/networkStateTypes";
import type { TopologyLink } from "./lab1Topology";

/**
 * A diagnosis is authoritative for the displayed state only when both come
 * from the very same collection. Anything else is never cross-correlated.
 */
export function isDiagnosisAligned(
  diagnosis: NetworkDiagnosisResponse,
  state: NetworkStateResponse,
): boolean {
  return diagnosis.collection_id === state.collection_id;
}

export const CONFIDENCE_UNAVAILABLE_TEXT = "Confidence unavailable";

export function confidenceText(confidence: DiagnosisConfidence | null): string {
  return confidence === null ? CONFIDENCE_UNAVAILABLE_TEXT : `${confidence} confidence`;
}

export function resourceText(resource: DiagnosisResource): string {
  const { endpoint_a: a, endpoint_b: b } = resource;
  return `${a.node_id} ${a.interface} ↔ ${b.node_id} ${b.interface}`;
}

/**
 * The existing declared link whose two endpoints are exactly the diagnosis
 * resource's endpoints (either orientation), or `null`. Never guesses and
 * never parses `resource_id`.
 */
export function findDiagnosedLink(
  resource: DiagnosisResource,
  links: readonly TopologyLink[],
): TopologyLink | null {
  const { endpoint_a: a, endpoint_b: b } = resource;
  const same = (
    end: { node: string; interface: string },
    other: { node_id: string; interface: string },
  ) => end.node === other.node_id && end.interface === other.interface;
  return (
    links.find(
      (link) => (same(link.a, a) && same(link.b, b)) || (same(link.a, b) && same(link.b, a)),
    ) ?? null
  );
}

export interface DiagnosisLinkMark {
  type: DiagnosisType;
  confidence: DiagnosisConfidence | null;
}

/** Declared-link id -> the diagnoses (API order) that name exactly that link. */
export function diagnosisMarksByLink(
  diagnoses: readonly Diagnosis[],
  links: readonly TopologyLink[],
): Map<string, DiagnosisLinkMark[]> {
  const marks = new Map<string, DiagnosisLinkMark[]>();
  for (const diagnosis of diagnoses) {
    const link = findDiagnosedLink(diagnosis.resource, links);
    if (link !== null) {
      const list = marks.get(link.id) ?? [];
      list.push({ type: diagnosis.diagnosis_type, confidence: diagnosis.confidence });
      marks.set(link.id, list);
    }
  }
  return marks;
}

const KIND_LABELS: Record<DiagnosisEvidence["kind"], string> = {
  INTERFACE_STATE: "Interface state",
  BGP_SESSION: "BGP session",
  ADDRESS_MATCH: "Interface address",
  ROUTE_NEXT_HOP: "Route next hop",
  ECMP_PATHS: "ECMP paths",
  REACHABILITY: "Reachability",
};

export interface EvidenceLine {
  /** Human label of the evidence kind. */
  label: string;
  /** Operator-readable facts, derived only from the structured `observed`. */
  facts: string[];
}

function pathPresence(present: boolean | null): string {
  if (present === null) {
    return "not determinable";
  }
  return present ? "present" : "absent";
}

/** Plain observed facts only: no causal wording. */
export function describeEvidence(item: DiagnosisEvidence): EvidenceLine {
  const label = KIND_LABELS[item.kind];
  switch (item.kind) {
    case "INTERFACE_STATE":
      return {
        label,
        facts: [`${item.node_id} ${item.observed.interface_name} — ${item.observed.state}`],
      };
    case "BGP_SESSION": {
      const shown = item.observed.raw_state ?? item.observed.state;
      return {
        label,
        facts: [`${item.node_id} → ${item.observed.neighbor_ip} — ${shown}`],
      };
    }
    case "ADDRESS_MATCH": {
      const { interface_name, declared_address, observed_addresses, matches } = item.observed;
      const subject = `${item.node_id} ${interface_name}`;
      if (matches === true) {
        return {
          label,
          facts: [`${subject}: declared and observed interface address match`],
        };
      }
      if (matches === false) {
        const observed =
          observed_addresses.length > 0 ? observed_addresses.join(", ") : "none observed";
        return {
          label,
          facts: [`${subject}: declared ${declared_address}; observed ${observed}`],
        };
      }
      return {
        label,
        facts: [`${subject}: address match not determinable (declared ${declared_address})`],
      };
    }
    case "ROUTE_NEXT_HOP": {
      const { next_hop_ip, active_route_count } = item.observed;
      const fact =
        active_route_count === null
          ? `${next_hop_ip} — active routes not determinable`
          : active_route_count === 0
            ? `${next_hop_ip} — absent from active routes`
            : `${next_hop_ip} — in ${active_route_count} active ${
                active_route_count === 1 ? "route" : "routes"
              }`;
      return { label, facts: [`${item.node_id}: ${fact}`] };
    }
    case "ECMP_PATHS": {
      const { prefix, observed_paths, expected_paths, link_next_hop_ip, link_next_hop_present } =
        item.observed;
      const paths =
        observed_paths === null
          ? `paths not observed (expected ${expected_paths})`
          : `${observed_paths} of ${expected_paths} paths`;
      return {
        label,
        facts: [
          `${item.node_id}: ${prefix} — ${paths}`,
          `path via ${link_next_hop_ip} ${pathPresence(link_next_hop_present)}`,
        ],
      };
    }
    case "REACHABILITY": {
      const { target, sent, received } = item.observed;
      const fact =
        sent === null || received === null ? "not observed" : `${received}/${sent} received`;
      return { label, facts: [`${item.node_id} → ${target} — ${fact}`] };
    }
  }
}

export const EVIDENCE_ROLE_ORDER: readonly EvidenceRole[] = ["SUPPORTS", "CONTRADICTS", "UNKNOWN"];

/** Evidence grouped by role, each group keeping the engine's ordering. */
export function groupEvidenceByRole(
  evidence: readonly DiagnosisEvidence[],
): Record<EvidenceRole, DiagnosisEvidence[]> {
  const groups: Record<EvidenceRole, DiagnosisEvidence[]> = {
    SUPPORTS: [],
    CONTRADICTS: [],
    UNKNOWN: [],
  };
  for (const item of evidence) {
    groups[item.role].push(item);
  }
  return groups;
}

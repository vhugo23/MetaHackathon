/**
 * Label / tone / glyph vocabulary for the live network workspace. Every
 * status carries a text label and a glyph so meaning is never colour-only.
 *
 * Five distinct meanings are kept apart (NPE-1D2):
 *   observed healthy (ok) · observed failure (bad/warn) · UNKNOWN (unknown) ·
 *   UNAVAILABLE / not observed (unavailable) · NOT APPLICABLE (na)
 */
import type { DiagnosisType } from "../api/networkDiagnosisTypes";
import type { BgpSessionState } from "../api/networkStateTypes";
import type {
  BgpAdjacencyState,
  BgpEndpointState,
  EndpointInterfaceReason,
  PhysicalLinkState,
  PresentationStatus,
  ReachabilityStatus,
  EcmpStatus,
} from "./derive";

export type Tone = "ok" | "bad" | "warn" | "unknown" | "unavailable" | "na";

export interface Presentation {
  tone: Tone;
  label: string;
  glyph: string;
}

const GLYPHS: Record<Tone, string> = {
  ok: "✓",
  bad: "✕",
  warn: "!",
  unknown: "?",
  unavailable: "∅",
  na: "–",
};

function make(tone: Tone, label: string): Presentation {
  return { tone, label, glyph: GLYPHS[tone] };
}

export function physicalPresentation(state: PhysicalLinkState): Presentation {
  switch (state) {
    case "up":
      return make("ok", "UP");
    case "down":
      return make("bad", "DOWN");
    case "unknown":
      return make("unknown", "UNKNOWN");
  }
}

export function adjacencyPresentation(state: BgpAdjacencyState): Presentation {
  switch (state) {
    case "established":
      return make("ok", "Established");
    case "degraded":
      return make("warn", "Degraded");
    case "unknown":
      return make("unknown", "Unknown");
  }
}

export function bgpSessionPresentation(state: BgpSessionState): Presentation {
  if (state === "Established") {
    return make("ok", "Established");
  }
  if (state === "Unknown") {
    return make("unknown", "Unknown");
  }
  return make("warn", state);
}

export function bgpEndpointPresentation(observation: BgpEndpointState): Presentation {
  switch (observation.kind) {
    case "observed":
      return bgpSessionPresentation(observation.state);
    case "facet-unavailable":
      return make("unavailable", "BGP unavailable");
    case "node-missing":
      return make("unavailable", "Node not observed");
    case "neighbor-missing":
      return make("unavailable", "Neighbor not observed");
  }
}

export function interfaceReasonText(reason: EndpointInterfaceReason): string {
  switch (reason) {
    case "reported":
      return "reported by the device";
    case "node-missing":
      return "node not in the snapshot";
    case "facet-unavailable":
      return "interfaces facet unavailable";
    case "interface-missing":
      return "interface not in the snapshot";
  }
}

export function ecmpPresentation(status: EcmpStatus): Presentation {
  switch (status) {
    case "intact":
      return make("ok", "Intact");
    case "reduced":
      return make("warn", "Reduced");
    case "unknown":
      return make("unknown", "Not observed");
  }
}

export function reachabilityPresentation(status: ReachabilityStatus): Presentation {
  switch (status) {
    case "succeeded":
      return make("ok", "Reachable");
    case "failed":
      return make("bad", "Loss observed");
    case "unavailable":
      return make("unavailable", "Unavailable");
  }
}

export function statusPresentation(status: PresentationStatus): Presentation {
  switch (status) {
    case "healthy":
      return make("ok", "Healthy");
    case "degraded":
      return make("warn", "Degraded");
    case "unknown":
      return make("unknown", "Unknown");
  }
}

export function interfaceStatePresentation(state: "up" | "down" | "unknown"): Presentation {
  return physicalPresentation(state);
}

/**
 * Operator wording for a backend diagnosis type. The tones keep the three
 * outcomes visually and textually distinct: a physical-link failure is never
 * presented like a BGP-session failure, and an inconclusive outcome is
 * neither.
 */
export function diagnosisTypePresentation(type: DiagnosisType): Presentation {
  switch (type) {
    case "PHYSICAL_LINK_FAILURE":
      return make("bad", "Physical link failure");
    case "BGP_SESSION_FAILURE":
      return make("warn", "BGP session failure");
    case "INSUFFICIENT_EVIDENCE":
      return make("unknown", "Insufficient evidence");
  }
}

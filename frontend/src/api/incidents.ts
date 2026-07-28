import {
  ApiRequestError,
  getJsonArray,
  MALFORMED_RESPONSE_MESSAGE,
  postNoBody,
  type GetJsonOptions,
  type PostNoBodyOptions,
} from "./client";
import {
  isBgpDownEvidenceResponse,
  isCpuHighEvidenceResponse,
  isIncidentResponse,
  isLinkFlapEvidenceResponse,
  isPolicyViolationIncidentEvidenceResponse,
  type IncidentResponse,
} from "./types";

/**
 * `isIncidentResponse` (types.ts) only proves `evidence` matches *some*
 * known shape, without regard to the sibling `source`/`rule_ref` fields —
 * a deliberate, purely structural check (see its own doc comment). This
 * function is the stricter, source/rule_ref-aware layer proving the
 * *correct* shape was returned for *this* incident's declared rule: an
 * `ANOMALY` incident's evidence must match the one shape its `rule_ref`
 * requires (`RULE-CPU-HIGH` -> CPU samples, `RULE-LINK-FLAP` -> interface
 * transitions, `RULE-BGP-DOWN` -> a single BGP transition); any other
 * `rule_ref` under `ANOMALY` is a shape this client does not recognize and
 * is rejected rather than guessed at. Every non-`ANOMALY` source (today,
 * only `POLICY_VIOLATION`; the dormant `DRIFT` has no evidence shape of
 * its own yet) keeps the exact pre-Day-11B check — no special case is
 * invented for it. This mirrors the existing precedent of layering a
 * stricter semantic check on top of `isIncidentResponse` (see
 * `resolveIncident` below).
 */
function hasEvidenceConsistentWithRule(incident: IncidentResponse): boolean {
  if (incident.source !== "ANOMALY") {
    return isPolicyViolationIncidentEvidenceResponse(incident.evidence);
  }
  switch (incident.rule_ref) {
    case "RULE-CPU-HIGH":
      return isCpuHighEvidenceResponse(incident.evidence);
    case "RULE-LINK-FLAP":
      return isLinkFlapEvidenceResponse(incident.evidence);
    case "RULE-BGP-DOWN":
      return isBgpDownEvidenceResponse(incident.evidence);
    default:
      return false;
  }
}

export async function fetchIncidents(options: GetJsonOptions = {}): Promise<IncidentResponse[]> {
  const body = await getJsonArray("/incidents", options);

  if (!body.every(isIncidentResponse) || !body.every(hasEvidenceConsistentWithRule)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body;
}

/**
 * Pure, read-only filter over an already-fetched incident list — never
 * issues a request of its own. Exact `device_id` and `source === "ANOMALY"`
 * match only (no severity/rule/status filter); both `OPEN` and `RESOLVED`
 * anomaly incidents are retained. Input order is preserved and the input
 * array itself is never mutated (`Array.prototype.filter` already returns
 * a new array).
 */
export function filterAnomalyIncidentsForDevice(
  incidents: readonly IncidentResponse[],
  deviceId: string,
): IncidentResponse[] {
  return incidents.filter(
    (incident) => incident.device_id === deviceId && incident.source === "ANOMALY",
  );
}

/**
 * `incidentId` is encoded as exactly one URL path segment, otherwise
 * preserved as given (never trimmed), matching `submitDeviceConfiguration`'s
 * `device_id` encoding convention. Beyond `isIncidentResponse`'s general
 * structural check, this endpoint enforces its own success semantics — a
 * `2xx` body that is structurally a valid `IncidentResponse` but doesn't
 * actually represent *this* incident having been resolved (wrong
 * `incident_id`, a `status` other than exactly `"RESOLVED"`, or a null
 * `resolved_at`) is rejected as the same controlled malformed-response
 * error, never returned to the caller.
 */
export async function resolveIncident(
  incidentId: string,
  options: PostNoBodyOptions = {},
): Promise<IncidentResponse> {
  const path = `/incidents/${encodeURIComponent(incidentId)}/resolve`;
  const body = await postNoBody<unknown>(path, options);

  if (
    !isIncidentResponse(body) ||
    body.incident_id !== incidentId ||
    body.status !== "RESOLVED" ||
    body.resolved_at === null
  ) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body;
}

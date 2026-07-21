import {
  ApiRequestError,
  getJsonArray,
  MALFORMED_RESPONSE_MESSAGE,
  postNoBody,
  type GetJsonOptions,
  type PostNoBodyOptions,
} from "./client";
import { isIncidentResponse, type IncidentResponse } from "./types";

export async function fetchIncidents(options: GetJsonOptions = {}): Promise<IncidentResponse[]> {
  const body = await getJsonArray("/incidents", options);

  if (!body.every(isIncidentResponse)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body;
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

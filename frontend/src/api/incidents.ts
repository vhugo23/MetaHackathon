import {
  ApiRequestError,
  getJsonArray,
  MALFORMED_RESPONSE_MESSAGE,
  type GetJsonOptions,
} from "./client";
import { isIncidentResponse, type IncidentResponse } from "./types";

export async function fetchIncidents(options: GetJsonOptions = {}): Promise<IncidentResponse[]> {
  const body = await getJsonArray("/incidents", options);

  if (!body.every(isIncidentResponse)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body;
}

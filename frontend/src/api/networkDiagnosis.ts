import {
  ApiRequestError,
  getJsonObject,
  MALFORMED_RESPONSE_MESSAGE,
  type GetJsonOptions,
} from "./client";
import { isNetworkDiagnosisResponse, type NetworkDiagnosisResponse } from "./networkDiagnosisTypes";

/**
 * `GET /network/diagnosis`: the read-only, on-demand deterministic RCA of the
 * latest ingested Lab 1 state. A non-2xx response keeps its
 * `ApiRequestError.code` (notably `operational_state_not_found`, recognized
 * by `isOperationalStateNotFound` in `networkState.ts`); a structurally
 * invalid 2xx body is the stable malformed-response error, never a partially
 * shaped object.
 */
export async function fetchNetworkDiagnosis(
  options: GetJsonOptions = {},
): Promise<NetworkDiagnosisResponse> {
  const body = await getJsonObject("/network/diagnosis", options);
  if (!isNetworkDiagnosisResponse(body)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }
  return body;
}

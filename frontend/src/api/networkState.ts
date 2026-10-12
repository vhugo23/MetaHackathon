import {
  ApiRequestError,
  getJsonObject,
  MALFORMED_RESPONSE_MESSAGE,
  type GetJsonOptions,
} from "./client";
import { isNetworkStateResponse, type NetworkStateResponse } from "./networkStateTypes";

/** `ApiRequestError.code` of a 404 before any operational state was ingested. */
export const OPERATIONAL_STATE_NOT_FOUND_CODE = "operational_state_not_found";

export function isOperationalStateNotFound(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === OPERATIONAL_STATE_NOT_FOUND_CODE;
}

/**
 * `GET /network/state`: the latest ingested operational state of the fixed
 * Lab 1. A non-2xx response keeps its `ApiRequestError.code` (notably
 * `operational_state_not_found`, which callers present as "no snapshot yet",
 * not as a failure); a structurally invalid 2xx body is the stable
 * malformed-response error, never a partially shaped object.
 */
export async function fetchNetworkState(
  options: GetJsonOptions = {},
): Promise<NetworkStateResponse> {
  const body = await getJsonObject("/network/state", options);
  if (!isNetworkStateResponse(body)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }
  return body;
}

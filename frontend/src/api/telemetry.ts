import {
  ApiRequestError,
  getJsonArray,
  MALFORMED_RESPONSE_MESSAGE,
  type GetJsonOptions,
} from "./client";
import { isTelemetrySampleResponse, type TelemetrySampleResponse } from "./types";

/**
 * `deviceId` and `since` are each encoded independently via
 * `encodeURIComponent` — `deviceId` as one opaque path segment (matching
 * `submitDeviceConfiguration`/`resolveIncident`'s convention), `since` as
 * one query-parameter value. Neither is trimmed or otherwise rewritten.
 * The backend contract already returns samples in ascending `sampled_at`
 * order with stable ordering for ties (docs/frontend-api-contract.md
 * Section 9) — this function performs no sorting, deduplication, or
 * limiting of its own; the received order is returned unchanged.
 */
export async function fetchRecentTelemetry(
  deviceId: string,
  since: string,
  options: GetJsonOptions = {},
): Promise<TelemetrySampleResponse[]> {
  const path = `/devices/${encodeURIComponent(deviceId)}/telemetry/recent?since=${encodeURIComponent(since)}`;
  const body = await getJsonArray(path, options);

  if (!body.every(isTelemetrySampleResponse)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body;
}

/**
 * The backend contract guarantees ascending `sampled_at` order (see
 * `fetchRecentTelemetry` above), so the latest sample is always the final
 * array element — no sorting or comparison of timestamps happens here.
 */
export function selectLatestTelemetrySample(
  samples: readonly TelemetrySampleResponse[],
): TelemetrySampleResponse | undefined {
  return samples[samples.length - 1];
}

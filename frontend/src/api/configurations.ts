import {
  ApiRequestError,
  MALFORMED_RESPONSE_MESSAGE,
  postJson,
  type PostJsonOptions,
} from "./client";
import {
  isConfigurationSubmissionResponse,
  type ConfigurationSubmissionRequest,
  type ConfigurationSubmissionResponse,
} from "./types";

/**
 * `deviceId` is encoded as exactly one URL path segment and otherwise
 * preserved as given — never trimmed or rewritten. It is treated as an
 * opaque string, never parsed for meaning.
 */
export async function submitDeviceConfiguration(
  deviceId: string,
  request: ConfigurationSubmissionRequest,
  options: PostJsonOptions = {},
): Promise<ConfigurationSubmissionResponse> {
  const path = `/devices/${encodeURIComponent(deviceId)}/config`;
  // Constructed fresh — TypeScript's structural typing does not guarantee a
  // runtime object has only these two keys, so the caller-supplied object is
  // never forwarded directly.
  const requestBody: ConfigurationSubmissionRequest = {
    vendor: request.vendor,
    raw_config_text: request.raw_config_text,
  };
  const body = await postJson(path, requestBody, options);

  if (!isConfigurationSubmissionResponse(body)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body;
}

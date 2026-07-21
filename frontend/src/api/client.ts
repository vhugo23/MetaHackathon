import type { ApiErrorResponse } from "./types";

const DEFAULT_BASE_URL = "http://localhost:8080";
const FALLBACK_ERROR_MESSAGE = "The request failed and no further detail is available.";
export const MALFORMED_RESPONSE_MESSAGE =
  "The server returned a response that could not be understood.";

export class ApiRequestError extends Error {
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.code = code;
  }
}

function resolveBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_BASE_URL;
  return raw.replace(/\/+$/, "");
}

function buildUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${resolveBaseUrl()}${normalizedPath}`;
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).code === "string" &&
    typeof (value as Record<string, unknown>).detail === "string"
  );
}

interface ParsedErrorDetail {
  detail: string;
  code?: string;
}

async function parseErrorDetail(response: Response): Promise<ParsedErrorDetail> {
  try {
    const body: unknown = await response.json();
    if (isApiErrorResponse(body)) {
      return { detail: body.detail, code: body.code };
    }
  } catch {
    // Malformed, empty, or non-JSON (e.g. HTML) body — fall through to the
    // stable fallback; the raw body text is deliberately never surfaced.
  }
  return { detail: FALLBACK_ERROR_MESSAGE };
}

export interface GetJsonOptions {
  signal?: AbortSignal;
}

/**
 * Returns the raw parsed JSON array, element shape unvalidated — callers
 * that need `T[]` must apply their own runtime element validator (see
 * `incidents.ts`'s use of `isIncidentResponse`); this function only proves
 * the top-level body is an array, never asserts element shape via cast.
 */
export async function getJsonArray(path: string, options: GetJsonOptions = {}): Promise<unknown[]> {
  const response = await fetch(buildUrl(path), {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "omit",
    signal: options.signal,
  });

  if (!response.ok) {
    const { detail, code } = await parseErrorDetail(response);
    throw new ApiRequestError(detail, code);
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  if (!Array.isArray(body)) {
    throw new ApiRequestError(MALFORMED_RESPONSE_MESSAGE);
  }

  return body as unknown[];
}

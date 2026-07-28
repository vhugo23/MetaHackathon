import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchRecentTelemetry, selectLatestTelemetrySample } from "./telemetry";
import { ApiRequestError } from "./client";
import type { TelemetrySampleResponse } from "./types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DEVICE_ID = "spine-01";
const SINCE = "2026-07-18T09:55:00Z";

const validSample: TelemetrySampleResponse = {
  device_id: DEVICE_ID,
  sampled_at: "2026-07-18T10:00:00Z",
  cpu_utilization_pct: 95.0,
  memory_utilization_pct: 60.0,
  interface_error_rate: 0.0,
  interface_states: [{ name: "GigabitEthernet0/1", oper_state: "up" }],
  bgp_sessions: [{ neighbor_ip: "10.0.0.1", state: "Established" }],
};

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function stubResponse(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body, status));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// ---------------------------------------------------------------------------
// Request shape
// ---------------------------------------------------------------------------

test("requests the exact encoded device path and since query parameter", async () => {
  const fetchMock = stubResponse([validSample]);

  await fetchRecentTelemetry(DEVICE_ID, SINCE);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe(
    `http://localhost:8080/devices/${DEVICE_ID}/telemetry/recent?since=${encodeURIComponent(SINCE)}`,
  );
});

test("safely encodes a device ID containing reserved characters", async () => {
  const fetchMock = stubResponse([]);
  const rawDeviceId = "spine 01/segment";

  await fetchRecentTelemetry(rawDeviceId, SINCE);

  const [url] = fetchMock.mock.calls[0] as [string];
  expect(url).toBe(
    `http://localhost:8080/devices/${encodeURIComponent(rawDeviceId)}/telemetry/recent?since=${encodeURIComponent(SINCE)}`,
  );
});

test("forwards an AbortSignal as the exact same object", async () => {
  const fetchMock = stubResponse([validSample]);
  const controller = new AbortController();

  await fetchRecentTelemetry(DEVICE_ID, SINCE, { signal: controller.signal });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.signal).toBe(controller.signal);
});

// ---------------------------------------------------------------------------
// Success
// ---------------------------------------------------------------------------

test("returns a valid telemetry array", async () => {
  stubResponse([validSample]);

  const result = await fetchRecentTelemetry(DEVICE_ID, SINCE);

  expect(result).toEqual([validSample]);
});

test("accepts an empty telemetry array", async () => {
  stubResponse([]);

  const result = await fetchRecentTelemetry(DEVICE_ID, SINCE);

  expect(result).toEqual([]);
});

test("preserves the backend's sample order", async () => {
  const second = { ...validSample, sampled_at: "2026-07-18T10:00:10Z" };
  stubResponse([validSample, second]);

  const result = await fetchRecentTelemetry(DEVICE_ID, SINCE);

  expect(result.map((sample) => sample.sampled_at)).toEqual([
    validSample.sampled_at,
    second.sampled_at,
  ]);
});

test("preserves equal-timestamp duplicate rows", async () => {
  stubResponse([validSample, validSample]);

  const result = await fetchRecentTelemetry(DEVICE_ID, SINCE);

  expect(result).toHaveLength(2);
});

// ---------------------------------------------------------------------------
// Rejection
// ---------------------------------------------------------------------------

test("rejects a non-array response", async () => {
  stubResponse({ not: "an array" });

  await expect(fetchRecentTelemetry(DEVICE_ID, SINCE)).rejects.toThrow(ApiRequestError);
});

test("rejects a sample missing a required field", async () => {
  const { device_id, ...withoutDeviceId } = validSample;
  void device_id;
  stubResponse([withoutDeviceId]);

  await expect(fetchRecentTelemetry(DEVICE_ID, SINCE)).rejects.toThrow(ApiRequestError);
});

test("rejects a sample with a non-finite numeric field", async () => {
  stubResponse([{ ...validSample, cpu_utilization_pct: Number.NaN }]);

  await expect(fetchRecentTelemetry(DEVICE_ID, SINCE)).rejects.toThrow(ApiRequestError);
});

test("rejects a sample with an invalid interface-state entry", async () => {
  stubResponse([{ ...validSample, interface_states: [{ name: "", oper_state: "up" }] }]);

  await expect(fetchRecentTelemetry(DEVICE_ID, SINCE)).rejects.toThrow(ApiRequestError);
});

test("rejects a sample with an invalid BGP-session entry", async () => {
  stubResponse([{ ...validSample, bgp_sessions: [{ neighbor_ip: "10.0.0.1", state: "" }] }]);

  await expect(fetchRecentTelemetry(DEVICE_ID, SINCE)).rejects.toThrow(ApiRequestError);
});

// ---------------------------------------------------------------------------
// selectLatestTelemetrySample
// ---------------------------------------------------------------------------

test("selectLatestTelemetrySample returns the final sample", () => {
  const second = { ...validSample, sampled_at: "2026-07-18T10:00:10Z" };

  expect(selectLatestTelemetrySample([validSample, second])).toBe(second);
});

test("selectLatestTelemetrySample returns undefined for an empty array", () => {
  expect(selectLatestTelemetrySample([])).toBeUndefined();
});

test("selectLatestTelemetrySample does not mutate its input", () => {
  const samples = [validSample, { ...validSample, sampled_at: "2026-07-18T10:00:10Z" }];
  const original = [...samples];

  selectLatestTelemetrySample(samples);

  expect(samples).toEqual(original);
});

// ---------------------------------------------------------------------------
// Error propagation
// ---------------------------------------------------------------------------

test("propagates a {code, detail} API error using the shared error convention", async () => {
  stubResponse({ code: "device_not_found", detail: "device not found: 'spine-01'" }, 404);

  try {
    await fetchRecentTelemetry(DEVICE_ID, SINCE);
    expect.unreachable("expected fetchRecentTelemetry to reject");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).code).toBe("device_not_found");
    expect((error as ApiRequestError).message).toBe("device not found: 'spine-01'");
  }
});

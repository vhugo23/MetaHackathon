import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { TELEMETRY_HISTORY_SINCE, useTelemetryWorkspace } from "./useTelemetryWorkspace";
import { ApiRequestError } from "../api/client";
import * as telemetryModule from "../api/telemetry";
import * as incidentsModule from "../api/incidents";
import type { IncidentResponse, TelemetrySampleResponse } from "../api/types";

vi.mock("../api/telemetry", async () => {
  const actual = await vi.importActual<typeof import("../api/telemetry")>("../api/telemetry");
  return { ...actual, fetchRecentTelemetry: vi.fn() };
});

vi.mock("../api/incidents", async () => {
  const actual = await vi.importActual<typeof import("../api/incidents")>("../api/incidents");
  return { ...actual, fetchIncidents: vi.fn() };
});

const fetchRecentTelemetryMock = vi.mocked(telemetryModule.fetchRecentTelemetry);
const fetchIncidentsMock = vi.mocked(incidentsModule.fetchIncidents);

const T0 = "2026-07-18T10:00:00Z";

function sample(overrides: Partial<TelemetrySampleResponse> = {}): TelemetrySampleResponse {
  return {
    device_id: "spine-01",
    sampled_at: T0,
    cpu_utilization_pct: 50,
    memory_utilization_pct: 40,
    interface_error_rate: 0,
    interface_states: [],
    bgp_sessions: [],
    ...overrides,
  };
}

function anomalyIncident(overrides: Partial<IncidentResponse> = {}): IncidentResponse {
  return {
    incident_id: "cpu-incident-1",
    fingerprint: "f".repeat(40),
    device_id: "spine-01",
    source: "ANOMALY",
    rule_ref: "RULE-CPU-HIGH",
    affected_resource: "device",
    severity: "High",
    status: "OPEN",
    evidence: { samples: [{ timestamp: T0, cpu_utilization_pct: 95 }] },
    recommendation: "Investigate sustained high CPU utilization on spine-01.",
    created_at: T0,
    last_seen_at: T0,
    occurrence_count: 1,
    updated_at: T0,
    resolved_at: null,
    ...overrides,
  };
}

function createDeferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  fetchRecentTelemetryMock.mockReset();
  fetchIncidentsMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. Initial state
// ---------------------------------------------------------------------------

test("issues no request on initial render", () => {
  renderHook(() => useTelemetryWorkspace());

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
  expect(fetchIncidentsMock).not.toHaveBeenCalled();
});

test("the initial state has no selected device and empty data", () => {
  const { result } = renderHook(() => useTelemetryWorkspace());

  expect(result.current.state.deviceId).toBeUndefined();
  expect(result.current.state.samples).toEqual([]);
  expect(result.current.state.anomalyIncidents).toEqual([]);
  expect(result.current.state.isInitialLoading).toBe(false);
  expect(result.current.state.isRefreshing).toBe(false);
  expect(result.current.state.telemetryError).toBeUndefined();
  expect(result.current.state.incidentError).toBeUndefined();
});

// ---------------------------------------------------------------------------
// 2-3. loadDevice input handling
// ---------------------------------------------------------------------------

test("loadDevice with an empty device ID performs no request", () => {
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("");
  });

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
  expect(fetchIncidentsMock).not.toHaveBeenCalled();
  expect(result.current.state.deviceId).toBeUndefined();
});

test("loadDevice with a whitespace-only device ID performs no request", () => {
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("   ");
  });

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
  expect(fetchIncidentsMock).not.toHaveBeenCalled();
});

test("loadDevice trims surrounding whitespace before requesting", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("  spine-01  ");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.deviceId).toBe("spine-01");
  const [deviceIdArg] = fetchRecentTelemetryMock.mock.calls[0]!;
  expect(deviceIdArg).toBe("spine-01");
});

// ---------------------------------------------------------------------------
// 4-5. Request shape
// ---------------------------------------------------------------------------

test("requests telemetry with TELEMETRY_HISTORY_SINCE", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  const [, since] = fetchRecentTelemetryMock.mock.calls[0]!;
  expect(since).toBe(TELEMETRY_HISTORY_SINCE);
});

test("telemetry and incident requests share the same AbortSignal", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  const [, , telemetryOptions] = fetchRecentTelemetryMock.mock.calls[0]!;
  const [incidentOptions] = fetchIncidentsMock.mock.calls[0]!;
  expect(telemetryOptions?.signal).toBeInstanceOf(AbortSignal);
  expect(telemetryOptions?.signal).toBe(incidentOptions?.signal);
});

// ---------------------------------------------------------------------------
// 6-7. Success and filtering
// ---------------------------------------------------------------------------

test("successful telemetry and incident responses populate both sides", async () => {
  const s = sample();
  const incident = anomalyIncident();
  fetchRecentTelemetryMock.mockResolvedValue([s]);
  fetchIncidentsMock.mockResolvedValue([incident]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.samples).toEqual([s]);
  expect(result.current.state.anomalyIncidents).toEqual([incident]);
  expect(result.current.state.telemetryError).toBeUndefined();
  expect(result.current.state.incidentError).toBeUndefined();
});

test("incident results are filtered by exact device ID and ANOMALY source", async () => {
  const matching = anomalyIncident();
  const otherDevice = anomalyIncident({ incident_id: "other-device", device_id: "leaf-02" });
  const policyIncident: IncidentResponse = {
    ...anomalyIncident({ incident_id: "policy-1" }),
    source: "POLICY_VIOLATION",
    rule_ref: "policy-acl-external-in",
    evidence: {
      source_snapshot_id: "snap-1",
      violation_type: "MISSING_REQUIRED_ACL",
      expected_acl_name: "ACL-EXTERNAL-IN",
      actual_acl_name: null,
      interface_name: "GigabitEthernet0/1",
      direction: "in",
    },
  };
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([matching, otherDevice, policyIncident]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.anomalyIncidents).toEqual([matching]);
});

// ---------------------------------------------------------------------------
// 8-11. Partial success
// ---------------------------------------------------------------------------

test("telemetry success survives an incident retrieval failure", async () => {
  const s = sample();
  fetchRecentTelemetryMock.mockResolvedValue([s]);
  fetchIncidentsMock.mockRejectedValue(new ApiRequestError("DB down", "persistence_error"));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.samples).toEqual([s]);
  expect(result.current.state.telemetryError).toBeUndefined();
  expect(result.current.state.incidentError).toBe("DB down");
});

test("incident success survives a telemetry retrieval failure", async () => {
  const incident = anomalyIncident();
  fetchRecentTelemetryMock.mockRejectedValue(
    new ApiRequestError("device not found: 'spine-01'", "device_not_found"),
  );
  fetchIncidentsMock.mockResolvedValue([incident]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.anomalyIncidents).toEqual([incident]);
  expect(result.current.state.incidentError).toBeUndefined();
  expect(result.current.state.telemetryError).toBe("device not found: 'spine-01'");
});

test("both failures are retained independently in state", async () => {
  fetchRecentTelemetryMock.mockRejectedValue(
    new ApiRequestError("telemetry down", "persistence_error"),
  );
  fetchIncidentsMock.mockRejectedValue(new ApiRequestError("incidents down", "persistence_error"));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.telemetryError).toBe("telemetry down");
  expect(result.current.state.incidentError).toBe("incidents down");
});

test("an empty telemetry array is retained as a valid success, not an error", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.samples).toEqual([]);
  expect(result.current.state.telemetryError).toBeUndefined();
});

// ---------------------------------------------------------------------------
// 12-14. Refresh
// ---------------------------------------------------------------------------

test("refresh does nothing before a device has been loaded", () => {
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.refresh();
  });

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
  expect(fetchIncidentsMock).not.toHaveBeenCalled();
});

test("refresh reuses the currently selected device", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));

  act(() => {
    result.current.refresh();
  });

  await waitFor(() => expect(result.current.state.isRefreshing).toBe(false));
  expect(fetchRecentTelemetryMock.mock.calls[1]?.[0]).toBe("spine-01");
});

test("refresh keeps existing data visible while pending", async () => {
  const s = sample();
  fetchRecentTelemetryMock.mockResolvedValueOnce([s]).mockReturnValueOnce(new Promise(() => {}));
  fetchIncidentsMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));

  act(() => {
    result.current.refresh();
  });

  expect(result.current.state.isRefreshing).toBe(true);
  expect(result.current.state.samples).toEqual([s]);
});

test("refresh updates only the successful side after a partial failure", async () => {
  const s1 = sample({ sampled_at: T0 });
  const s2 = sample({ sampled_at: "2026-07-18T10:00:30Z" });
  const incident = anomalyIncident();
  fetchRecentTelemetryMock.mockResolvedValueOnce([s1]).mockResolvedValueOnce([s2]);
  fetchIncidentsMock
    .mockResolvedValueOnce([incident])
    .mockRejectedValueOnce(new ApiRequestError("DB down", "persistence_error"));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));

  act(() => {
    result.current.refresh();
  });

  await waitFor(() => expect(result.current.state.isRefreshing).toBe(false));
  expect(result.current.state.samples).toEqual([s2]);
  expect(result.current.state.anomalyIncidents).toEqual([incident]);
  expect(result.current.state.incidentError).toBe("DB down");
});

// ---------------------------------------------------------------------------
// 15-18. Device change and staleness
// ---------------------------------------------------------------------------

test("loading a new device clears the previous device's data immediately", async () => {
  const s = sample();
  fetchRecentTelemetryMock.mockResolvedValueOnce([s]).mockReturnValueOnce(new Promise(() => {}));
  fetchIncidentsMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.samples).toEqual([s]);

  act(() => {
    result.current.loadDevice("leaf-02");
  });

  expect(result.current.state.deviceId).toBe("leaf-02");
  expect(result.current.state.samples).toEqual([]);
  expect(result.current.state.anomalyIncidents).toEqual([]);
  expect(result.current.state.isInitialLoading).toBe(true);
});

test("loading a new device aborts the prior in-flight request", () => {
  let firstSignal: AbortSignal | undefined;
  fetchRecentTelemetryMock
    .mockImplementationOnce((_device, _since, options) => {
      firstSignal = options?.signal;
      return new Promise(() => {});
    })
    .mockReturnValueOnce(new Promise(() => {}));
  fetchIncidentsMock.mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  expect(firstSignal?.aborted).toBe(false);

  act(() => {
    result.current.loadDevice("leaf-02");
  });

  expect(firstSignal?.aborted).toBe(true);
});

test("a late response for a prior device cannot overwrite the current device's state", async () => {
  const staleSample = sample({ device_id: "spine-01" });
  const currentSample = sample({ device_id: "leaf-02" });
  const firstTelemetry = createDeferred<TelemetrySampleResponse[]>();
  fetchRecentTelemetryMock
    .mockImplementationOnce(() => firstTelemetry.promise)
    .mockResolvedValueOnce([currentSample]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  act(() => {
    result.current.loadDevice("leaf-02");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.deviceId).toBe("leaf-02");
  expect(result.current.state.samples).toEqual([currentSample]);

  await act(async () => {
    firstTelemetry.resolve([staleSample]);
    await firstTelemetry.promise.catch(() => {});
  });

  expect(result.current.state.deviceId).toBe("leaf-02");
  expect(result.current.state.samples).toEqual([currentSample]);
});

test("an older refresh response cannot overwrite a newer refresh's result", async () => {
  fetchRecentTelemetryMock.mockResolvedValueOnce([]);
  fetchIncidentsMock.mockResolvedValueOnce([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });
  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));

  const firstRefresh = createDeferred<TelemetrySampleResponse[]>();
  const secondSample = sample({ sampled_at: "2026-07-18T11:00:00Z" });
  fetchRecentTelemetryMock
    .mockImplementationOnce(() => firstRefresh.promise)
    .mockResolvedValueOnce([secondSample]);
  fetchIncidentsMock.mockResolvedValue([]);

  act(() => {
    result.current.refresh();
  });
  act(() => {
    result.current.refresh();
  });

  await waitFor(() => expect(result.current.state.isRefreshing).toBe(false));
  expect(result.current.state.samples).toEqual([secondSample]);

  await act(async () => {
    firstRefresh.resolve([sample({ sampled_at: "2026-07-18T09:00:00Z" })]);
    await firstRefresh.promise.catch(() => {});
  });

  expect(result.current.state.samples).toEqual([secondSample]);
});

// ---------------------------------------------------------------------------
// 19. Unmount
// ---------------------------------------------------------------------------

test("unmount aborts active work", () => {
  let capturedSignal: AbortSignal | undefined;
  fetchRecentTelemetryMock.mockImplementationOnce((_device, _since, options) => {
    capturedSignal = options?.signal;
    return new Promise(() => {});
  });
  fetchIncidentsMock.mockReturnValueOnce(new Promise(() => {}));
  const { result, unmount } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  expect(capturedSignal?.aborted).toBe(false);
  unmount();
  expect(capturedSignal?.aborted).toBe(true);
});

// ---------------------------------------------------------------------------
// 20. Last-refreshed timestamp
// ---------------------------------------------------------------------------

test("the last-successful-refresh timestamp updates after a complete success", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.lastRefreshedAt).toBeDefined();
});

test("the last-successful-refresh timestamp updates even when only one side succeeds", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockRejectedValue(new ApiRequestError("DB down", "persistence_error"));
  const { result } = renderHook(() => useTelemetryWorkspace());

  act(() => {
    result.current.loadDevice("spine-01");
  });

  await waitFor(() => expect(result.current.state.isInitialLoading).toBe(false));
  expect(result.current.state.lastRefreshedAt).toBeDefined();
});

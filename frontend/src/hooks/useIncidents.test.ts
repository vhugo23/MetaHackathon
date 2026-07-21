import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useIncidents } from "./useIncidents";
import type { IncidentResponse } from "../api/types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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

const incidentA: IncidentResponse = {
  incident_id: "8f14e45f-ceea-4c1d-8f1e-1234567890ab",
  fingerprint: "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
  device_id: "spine-01",
  source: "POLICY_VIOLATION",
  rule_ref: "policy-acl-external-in",
  affected_resource: "acl:ACL-EXTERNAL-IN:GigabitEthernet0/1:in",
  severity: "Medium",
  status: "OPEN",
  evidence: {
    source_snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    violation_type: "MISSING_REQUIRED_ACL",
    expected_acl_name: "ACL-EXTERNAL-IN",
    actual_acl_name: null,
    interface_name: "GigabitEthernet0/1",
    direction: "in",
  },
  recommendation: "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
  created_at: "2026-07-18T10:00:00Z",
  last_seen_at: "2026-07-18T10:00:00Z",
  occurrence_count: 1,
};

const incidentB: IncidentResponse = {
  ...incidentA,
  incident_id: "second-incident-id",
  fingerprint: "second-fingerprint",
  device_id: "leaf-02",
};

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

test("aborts the active request on unmount", () => {
  let capturedSignal: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      capturedSignal = init.signal as AbortSignal;
      return new Promise(() => {});
    }),
  );

  const { unmount } = renderHook(() => useIncidents());

  expect(capturedSignal?.aborted).toBe(false);
  unmount();
  expect(capturedSignal?.aborted).toBe(true);
});

test("refresh aborts a request still in flight and starts exactly one new request", async () => {
  const signals: AbortSignal[] = [];
  const fetchMock = vi
    .fn()
    .mockImplementationOnce((_url: string, init: RequestInit) => {
      signals.push(init.signal as AbortSignal);
      return Promise.resolve(jsonResponse([incidentA]));
    })
    .mockImplementation((_url: string, init: RequestInit) => {
      signals.push(init.signal as AbortSignal);
      return new Promise(() => {});
    });
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => {
    expect(result.current.state.status).toBe("success");
  });

  act(() => {
    result.current.refresh();
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(signals[1]?.aborted).toBe(false);

  // Retry shares the same `refresh` implementation as the Refresh control —
  // this second call exercises the identical abort-and-restart path.
  act(() => {
    result.current.refresh();
  });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(signals[1]?.aborted).toBe(true);
});

test("a late-resolving stale success cannot overwrite a newer successful response", async () => {
  const first = createDeferred<Response>();
  const second = createDeferred<Response>();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => {
    expect(result.current.state.status).toBe("success");
  });

  act(() => {
    result.current.refresh();
  });
  act(() => {
    result.current.refresh();
  });

  await act(async () => {
    second.resolve(jsonResponse([incidentB]));
    await second.promise;
  });
  await waitFor(() => {
    expect(result.current.state.status === "success" && result.current.state.data).toEqual([
      incidentB,
    ]);
  });

  await act(async () => {
    first.resolve(jsonResponse([incidentA]));
    await first.promise.catch(() => {});
  });

  expect(result.current.state.status).toBe("success");
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    incidentB,
  ]);
});

test("a late-resolving stale failure cannot overwrite a newer successful response", async () => {
  const first = createDeferred<Response>();
  const second = createDeferred<Response>();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => {
    expect(result.current.state.status).toBe("success");
  });

  act(() => {
    result.current.refresh();
  });
  act(() => {
    result.current.refresh();
  });

  await act(async () => {
    second.resolve(jsonResponse([incidentB]));
    await second.promise;
  });
  await waitFor(() => {
    expect(result.current.state.status === "success" && result.current.state.data).toEqual([
      incidentB,
    ]);
  });

  await act(async () => {
    first.reject(new TypeError("late network failure"));
    await first.promise.catch(() => {});
  });

  expect(result.current.state.status).toBe("success");
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    incidentB,
  ]);
});

test("a superseded request's rejection never produces a visible error state", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() =>
      Promise.reject(
        Object.assign(new Error("The operation was aborted."), { name: "AbortError" }),
      ),
    )
    .mockResolvedValueOnce(jsonResponse([incidentB]));
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => {
    expect(result.current.state.status).toBe("success");
  });

  await act(async () => {
    result.current.refresh();
    result.current.refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  await waitFor(() => {
    expect(result.current.state.status === "success" && result.current.state.data).toEqual([
      incidentB,
    ]);
  });
  expect(result.current.state.status).not.toBe("error");
});

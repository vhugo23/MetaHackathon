import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useIncidents } from "./useIncidents";
import { ApiRequestError } from "../api/client";
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
  updated_at: "2026-07-18T10:00:00Z",
  resolved_at: null,
};

const incidentB: IncidentResponse = {
  ...incidentA,
  incident_id: "second-incident-id",
  fingerprint: "second-fingerprint",
  device_id: "leaf-02",
};

const incidentAcknowledged: IncidentResponse = {
  ...incidentA,
  incident_id: "acknowledged-incident-id",
  fingerprint: "acknowledged-fingerprint",
  status: "ACKNOWLEDGED",
};

/**
 * Routes a stubbed `fetch` between the one initial/refresh `GET /incidents`
 * queue and per-incident `POST .../resolve` handlers, so tests can control
 * each independently without caring about call ordering across the two.
 */
function createFetchRouter(): {
  fetchMock: ReturnType<typeof vi.fn>;
  queueGet: (handler: () => Promise<Response>) => void;
  setResolveHandler: (incidentId: string, handler: () => Promise<Response>) => void;
  calls: Array<{ url: string; init: RequestInit }>;
} {
  const getQueue: Array<() => Promise<Response>> = [];
  const resolveHandlers = new Map<string, () => Promise<Response>>();
  const calls: Array<{ url: string; init: RequestInit }> = [];

  const fetchMock = vi.fn().mockImplementation((url: string, init: RequestInit) => {
    calls.push({ url, init });
    if (init.method === "POST") {
      const handler = resolveHandlers.get(url);
      if (!handler) {
        throw new Error(`Unexpected POST to ${url}`);
      }
      return handler();
    }
    const handler = getQueue.shift();
    if (!handler) {
      throw new Error(`Unexpected GET to ${url}`);
    }
    return handler();
  });

  return {
    fetchMock,
    queueGet: (handler) => getQueue.push(handler),
    setResolveHandler: (incidentId, handler) =>
      resolveHandlers.set(`http://localhost:8080/incidents/${incidentId}/resolve`, handler),
    calls,
  };
}

function resolvedIncident(overrides: Partial<IncidentResponse> = {}): IncidentResponse {
  return {
    ...incidentA,
    status: "RESOLVED",
    updated_at: "2026-07-18T11:00:00Z",
    resolved_at: "2026-07-18T11:00:00Z",
    ...overrides,
  };
}

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
  // GET/refresh reconciliation (Gate 7B-C) retains `incidentA` as a
  // current-only incident (appended after the incoming list) rather than
  // dropping it — GET /incidents is unfiltered and append-only, so an
  // older/narrower response never implies deletion.
  await waitFor(() => {
    expect(result.current.state.status === "success" && result.current.state.data).toEqual([
      incidentB,
      incidentA,
    ]);
  });

  await act(async () => {
    first.resolve(jsonResponse([incidentA]));
    await first.promise.catch(() => {});
  });

  expect(result.current.state.status).toBe("success");
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    incidentB,
    incidentA,
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
  // See the equivalent note in the "stale success" test above — `incidentA`
  // is retained as a current-only incident under the Gate 7B-C merge.
  await waitFor(() => {
    expect(result.current.state.status === "success" && result.current.state.data).toEqual([
      incidentB,
      incidentA,
    ]);
  });

  await act(async () => {
    first.reject(new TypeError("late network failure"));
    await first.promise.catch(() => {});
  });

  expect(result.current.state.status).toBe("success");
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    incidentB,
    incidentA,
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

  // `incidentA` is retained as a current-only incident under the Gate 7B-C
  // merge — see the equivalent note in the "stale success" test above.
  await waitFor(() => {
    expect(result.current.state.status === "success" && result.current.state.data).toEqual([
      incidentB,
      incidentA,
    ]);
  });
  expect(result.current.state.status).not.toBe("error");
});

// =============================================================================
// resolveIncident (Day 7B, Gate 7B-C)
// =============================================================================

// --- eligibility -------------------------------------------------------

test("resolveIncident starts exactly one POST for an OPEN incident", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  const postCalls = router.calls.filter((call) => call.init.method === "POST");
  expect(postCalls).toHaveLength(1);
  expect(postCalls[0]?.url).toBe(
    `http://localhost:8080/incidents/${incidentA.incident_id}/resolve`,
  );
});

test("resolveIncident starts no POST for a RESOLVED incident", async () => {
  const router = createFetchRouter();
  const alreadyResolved = resolvedIncident();
  router.queueGet(() => Promise.resolve(jsonResponse([alreadyResolved])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(alreadyResolved.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
  expect(result.current.resolvingIds.has(alreadyResolved.incident_id)).toBe(false);
});

test("resolveIncident starts no POST for an ACKNOWLEDGED incident", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentAcknowledged])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentAcknowledged.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
});

test("resolveIncident starts no POST for an unknown future status", async () => {
  const router = createFetchRouter();
  const suppressed: IncidentResponse = { ...incidentA, status: "SUPPRESSED" };
  router.queueGet(() => Promise.resolve(jsonResponse([suppressed])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(suppressed.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
});

test("resolveIncident starts no POST for a missing incident id", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident("does-not-exist");
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
});

test("resolveIncident starts no POST while the hook is still loading", () => {
  const router = createFetchRouter();
  router.queueGet(() => new Promise(() => {}));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  expect(result.current.state.status).toBe("loading");

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
});

test("resolveIncident starts no POST when the hook is in the top-level error state", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.reject(new TypeError("network down")));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("error"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(0);
});

// --- duplicate and independent requests ---------------------------------

test("two immediate calls for the same incident issue exactly one POST (the duplicate guard runs before React commits pending state)", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
    result.current.resolveIncident(incidentA.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(1);
});

test("two different incident IDs may issue two concurrent POST requests, tracked independently in resolvingIds", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferredA = createDeferred<Response>();
  const deferredB = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferredA.promise);
  router.setResolveHandler(incidentB.incident_id, () => deferredB.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
    result.current.resolveIncident(incidentB.incident_id);
  });

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(2);
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(true);
  expect(result.current.resolvingIds.has(incidentB.incident_id)).toBe(true);
});

// --- pending and errors --------------------------------------------------

test("starting a request marks only that incident id as pending", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(true);
  expect(result.current.resolvingIds.has(incidentB.incident_id)).toBe(false);
});

test("a failure leaves incident data unchanged and stores a controlled message under only that incident id", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(true);

  await act(async () => {
    deferred.reject(new ApiRequestError("Incident 'x' was not found.", "incident_not_found"));
    await deferred.promise.catch(() => {});
  });

  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(false);
  expect(result.current.resolveErrors[incidentA.incident_id]).toBe("Incident 'x' was not found.");
  expect(result.current.resolveErrors[incidentB.incident_id]).toBeUndefined();
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    incidentA,
    incidentB,
  ]);
});

test("a retry after failure starts a second request, clearing the previous error, and a subsequent success clears it for good", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const firstAttempt = createDeferred<Response>();
  const secondAttempt = createDeferred<Response>();
  let attempt = 0;
  router.setResolveHandler(incidentA.incident_id, () => {
    attempt += 1;
    return attempt === 1 ? firstAttempt.promise : secondAttempt.promise;
  });
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });
  await act(async () => {
    firstAttempt.reject(new TypeError("network down"));
    await firstAttempt.promise.catch(() => {});
  });
  expect(result.current.resolveErrors[incidentA.incident_id]).toBeDefined();
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(false);

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(true);
  expect(result.current.resolveErrors[incidentA.incident_id]).toBeUndefined();

  const resolved = resolvedIncident();
  await act(async () => {
    secondAttempt.resolve(jsonResponse(resolved));
    await secondAttempt.promise;
  });

  expect(result.current.resolveErrors[incidentA.incident_id]).toBeUndefined();
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(false);
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    resolved,
  ]);
});

// --- successful replacement ----------------------------------------------

test("a successful resolution replaces the matching incident, preserves order and unrelated references, and leaves lastUpdatedAt/GET count unaffected", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));
  const lastUpdatedAtBefore =
    result.current.state.status === "success" ? result.current.state.lastUpdatedAt : "";
  const incidentBReferenceBefore =
    result.current.state.status === "success" ? result.current.state.data[1] : undefined;

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  const resolved = resolvedIncident();
  await act(async () => {
    deferred.resolve(jsonResponse(resolved));
    await deferred.promise;
  });

  expect(result.current.state.status).toBe("success");
  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([resolved, incidentB]);
  expect(data[1]).toBe(incidentBReferenceBefore);
  expect(data[0]?.updated_at).toBe(resolved.updated_at);
  expect(data[0]?.resolved_at).toBe(resolved.resolved_at);
  expect(result.current.state.status === "success" ? result.current.state.lastUpdatedAt : "").toBe(
    lastUpdatedAtBefore,
  );
  expect(router.calls.filter((call) => call.init.method === "GET")).toHaveLength(1);
  expect(result.current.resolveErrors[incidentA.incident_id]).toBeUndefined();
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(false);
});

// --- resolve-response staleness -------------------------------------------

test("an older POST response does not overwrite a newer current incident", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const resolveDeferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => resolveDeferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  // A concurrent refresh lands first, advancing the incident past what the
  // stale resolve response below will claim.
  const advancedIncident: IncidentResponse = {
    ...incidentA,
    updated_at: "2026-07-18T12:00:00Z",
    occurrence_count: 2,
  };
  router.queueGet(() => Promise.resolve(jsonResponse([advancedIncident])));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    const state = result.current.state;
    expect(state.status === "success" && !state.isRefreshing).toBe(true);
  });

  const staleResolved = resolvedIncident({ updated_at: incidentA.updated_at });
  await act(async () => {
    resolveDeferred.resolve(jsonResponse(staleResolved));
    await resolveDeferred.promise;
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([advancedIncident]);
});

test("a newer POST response replaces the current incident", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  const resolved = resolvedIncident({ updated_at: "2026-07-18T13:00:00Z" });
  await act(async () => {
    deferred.resolve(jsonResponse(resolved));
    await deferred.promise;
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([resolved]);
});

test("equal updated_at in a resolve response prefers RESOLVED over OPEN", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  const resolved = resolvedIncident({ updated_at: incidentA.updated_at });
  await act(async () => {
    deferred.resolve(jsonResponse(resolved));
    await deferred.promise;
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([resolved]);
});

test("an unparseable updated_at never falls back to lexical ordering when applying a resolve response", async () => {
  const router = createFetchRouter();
  const currentWithUnparseable: IncidentResponse = {
    ...incidentA,
    updated_at: "not-a-real-timestamp",
  };
  router.queueGet(() => Promise.resolve(jsonResponse([currentWithUnparseable])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  // Lexically, "not-a-real-timestamp" > "2000-01-01T00:00:00Z", which would
  // (wrongly) keep `current` under a naive string comparison. The lifecycle
  // fallback must instead prefer the incoming RESOLVED value.
  const resolved = resolvedIncident({ updated_at: "2000-01-01T00:00:00Z" });
  await act(async () => {
    deferred.resolve(jsonResponse(resolved));
    await deferred.promise;
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([resolved]);
});

// --- fetch/refresh reconciliation ------------------------------------------

test("a stale GET response cannot overwrite a same-id newer RESOLVED object", async () => {
  const router = createFetchRouter();
  const resolvedNow = resolvedIncident({ updated_at: "2026-07-18T11:00:00Z" });
  router.queueGet(() => Promise.resolve(jsonResponse([resolvedNow])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  const staleOpen: IncidentResponse = { ...incidentA, updated_at: "2026-07-18T10:00:00Z" };
  router.queueGet(() => Promise.resolve(jsonResponse([staleOpen])));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    const state = result.current.state;
    expect(state.status === "success" && !state.isRefreshing).toBe(true);
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([resolvedNow]);
});

test("a newer GET response replaces the current object", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  const advanced: IncidentResponse = {
    ...incidentA,
    updated_at: "2026-07-18T12:00:00Z",
    occurrence_count: 2,
  };
  router.queueGet(() => Promise.resolve(jsonResponse([advanced])));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    const state = result.current.state;
    expect(state.status === "success" && !state.isRefreshing).toBe(true);
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([advanced]);
});

test("equal updated_at in a GET response prefers RESOLVED over OPEN", async () => {
  const router = createFetchRouter();
  const resolvedNow = resolvedIncident({ updated_at: "2026-07-18T11:00:00Z" });
  router.queueGet(() => Promise.resolve(jsonResponse([resolvedNow])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  const sameInstantOpen: IncidentResponse = { ...incidentA, updated_at: "2026-07-18T11:00:00Z" };
  router.queueGet(() => Promise.resolve(jsonResponse([sameInstantOpen])));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    const state = result.current.state;
    expect(state.status === "success" && !state.isRefreshing).toBe(true);
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([resolvedNow]);
});

test("incoming-only incidents are added, current-only incidents are retained and appended in prior order, and incoming order is preserved", async () => {
  const router = createFetchRouter();
  const currentOnly1: IncidentResponse = { ...incidentA, incident_id: "current-only-1" };
  const currentOnly2: IncidentResponse = { ...incidentA, incident_id: "current-only-2" };
  const shared: IncidentResponse = { ...incidentA, incident_id: "shared-incident" };
  router.queueGet(() => Promise.resolve(jsonResponse([currentOnly1, shared, currentOnly2])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  const incomingOnly: IncidentResponse = { ...incidentA, incident_id: "incoming-only" };
  const sharedUpdated: IncidentResponse = {
    ...shared,
    updated_at: "2026-07-18T12:00:00Z",
    occurrence_count: 2,
  };
  router.queueGet(() => Promise.resolve(jsonResponse([incomingOnly, sharedUpdated])));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    const state = result.current.state;
    expect(state.status === "success" && !state.isRefreshing).toBe(true);
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([incomingOnly, sharedUpdated, currentOnly1, currentOnly2]);
});

test("a configuration-submission-style refresh still completes normally with reconciliation applied", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    const state = result.current.state;
    expect(state.status === "success" && !state.isRefreshing).toBe(true);
  });

  const data = result.current.state.status === "success" ? result.current.state.data : [];
  expect(data).toEqual([incidentA, incidentB]);
});

// --- cancellation and lifecycle --------------------------------------------

test("unmount aborts every active resolution controller", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  router.setResolveHandler(incidentA.incident_id, () => new Promise(() => {}));
  router.setResolveHandler(incidentB.incident_id, () => new Promise(() => {}));
  vi.stubGlobal("fetch", router.fetchMock);

  const { result, unmount } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
    result.current.resolveIncident(incidentB.incident_id);
  });

  const postCalls = router.calls.filter((call) => call.init.method === "POST");
  const signalA = postCalls.find((call) => call.url.includes(incidentA.incident_id))?.init
    .signal as AbortSignal;
  const signalB = postCalls.find((call) => call.url.includes(incidentB.incident_id))?.init
    .signal as AbortSignal;
  expect(signalA?.aborted).toBe(false);
  expect(signalB?.aborted).toBe(false);

  unmount();

  expect(signalA?.aborted).toBe(true);
  expect(signalB?.aborted).toBe(true);
});

test("an aborted resolution produces no visible error", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result, unmount } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  unmount();

  await act(async () => {
    deferred.reject(Object.assign(new Error("The operation was aborted."), { name: "AbortError" }));
    await deferred.promise.catch(() => {});
  });

  // Nothing to assert against a torn-down `result.current` beyond the
  // absence of a thrown/unhandled rejection above — an aborted completion
  // after unmount must be a safe, silent no-op.
});

test("late completion after unmount performs no state update and logs no act warning", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);
  const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

  const { result, unmount } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });

  unmount();

  await act(async () => {
    deferred.resolve(jsonResponse(resolvedIncident()));
    await deferred.promise;
  });

  expect(consoleErrorSpy).not.toHaveBeenCalled();
  consoleErrorSpy.mockRestore();
});

test("one incident's failure does not cancel or clear another pending incident", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferredA = createDeferred<Response>();
  const deferredB = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferredA.promise);
  router.setResolveHandler(incidentB.incident_id, () => deferredB.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
    result.current.resolveIncident(incidentB.incident_id);
  });

  await act(async () => {
    deferredA.reject(new TypeError("network down"));
    await deferredA.promise.catch(() => {});
  });

  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(false);
  expect(result.current.resolveErrors[incidentA.incident_id]).toBeDefined();
  expect(result.current.resolvingIds.has(incidentB.incident_id)).toBe(true);
  expect(result.current.resolveErrors[incidentB.incident_id]).toBeUndefined();
});

test("a retry's own completion applies correctly after an earlier failed attempt already settled and cleaned up", async () => {
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const firstAttempt = createDeferred<Response>();
  const secondAttempt = createDeferred<Response>();
  let attempt = 0;
  router.setResolveHandler(incidentA.incident_id, () => {
    attempt += 1;
    return attempt === 1 ? firstAttempt.promise : secondAttempt.promise;
  });
  vi.stubGlobal("fetch", router.fetchMock);

  const { result } = renderHook(() => useIncidents());
  await waitFor(() => expect(result.current.state.status).toBe("success"));

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });
  await act(async () => {
    firstAttempt.reject(new TypeError("network down"));
    await firstAttempt.promise.catch(() => {});
  });

  act(() => {
    result.current.resolveIncident(incidentA.incident_id);
  });
  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(true);

  const resolved = resolvedIncident();
  await act(async () => {
    secondAttempt.resolve(jsonResponse(resolved));
    await secondAttempt.promise;
  });

  expect(result.current.resolvingIds.has(incidentA.incident_id)).toBe(false);
  expect(result.current.state.status === "success" && result.current.state.data).toEqual([
    resolved,
  ]);
});

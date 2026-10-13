import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { MALFORMED_RESPONSE_MESSAGE } from "../api/client";
import {
  DEGRADED_COLLECTION_ID,
  diagnosisResponse,
  healthyDiagnosis,
  physicalDiagnosis,
} from "../test/diagnosisFixtures";
import { jsonResponse, notFoundNoSnapshotResponse } from "../test/networkFixtures";
import { useNetworkDiagnosis } from "./useNetworkDiagnosis";

async function flush(action: () => void): Promise<void> {
  await act(async () => {
    action();
    await Promise.resolve();
  });
}

interface Deferred {
  resolve: (response: Response) => void;
  reject: (error: unknown) => void;
  signal: AbortSignal;
}

/**
 * Parks every request. It ignores the abort signal when settling (like a
 * client that does not honor it), so the hook's own request-ID / collected_at
 * guards are what is under test.
 */
function parkedFetch() {
  const pending: Deferred[] = [];
  const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    return new Promise<Response>((resolve, reject) => {
      pending.push({ resolve, reject, signal: init.signal as AbortSignal });
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { pending, fetchMock };
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("useNetworkDiagnosis", () => {
  test("starts loading and issues exactly one GET /network/diagnosis", () => {
    const { fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    expect(result.current.state).toEqual({ status: "loading" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]![0]).toBe("http://localhost:8080/network/diagnosis");
  });

  test("success holds the response", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    const body = diagnosisResponse([physicalDiagnosis()]);
    await flush(() => pending[0]!.resolve(jsonResponse(body)));
    expect(result.current.state).toEqual({ status: "success", data: body, refreshError: null });
  });

  test("404 operational_state_not_found is no-snapshot, not an error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => pending[0]!.resolve(notFoundNoSnapshotResponse()));
    expect(result.current.state).toEqual({ status: "no-snapshot" });
  });

  test("a transport failure is an unreachable error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => pending[0]!.reject(new TypeError("Failed to fetch")));
    expect(result.current.state).toMatchObject({ status: "error", error: { kind: "unreachable" } });
  });

  test("an API error status is an api error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() =>
      pending[0]!.resolve(
        jsonResponse({ code: "persistence_error", detail: "Database failure." }, 500),
      ),
    );
    expect(result.current.state).toMatchObject({
      status: "error",
      error: { kind: "api", message: "Database failure." },
    });
  });

  test("a malformed response is a malformed error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => pending[0]!.resolve(jsonResponse({ diagnoses: [] })));
    expect(result.current.state).toMatchObject({
      status: "error",
      error: { kind: "malformed", message: MALFORMED_RESPONSE_MESSAGE },
    });
  });

  test("recovers from a transient error on the next poll", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => pending[0]!.reject(new TypeError("Failed to fetch")));
    expect(result.current.state.status).toBe("error");

    await flush(() => result.current.poll());
    expect(pending).toHaveLength(2);
    await flush(() => pending[1]!.resolve(jsonResponse(healthyDiagnosis())));
    expect(result.current.state).toMatchObject({ status: "success", refreshError: null });
  });

  test("a failed refresh keeps the previous response and flags the error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    const body = diagnosisResponse([physicalDiagnosis()]);
    await flush(() => pending[0]!.resolve(jsonResponse(body)));

    await flush(() => result.current.poll());
    await flush(() => pending[1]!.reject(new TypeError("Failed to fetch")));
    expect(result.current.state).toMatchObject({
      status: "success",
      data: body,
      refreshError: { kind: "unreachable" },
    });

    await flush(() => result.current.poll());
    await flush(() => pending[2]!.resolve(jsonResponse(body)));
    expect(result.current.state).toMatchObject({ status: "success", refreshError: null });
  });

  test("a 404 after a response keeps the held response and flags it", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    const body = diagnosisResponse([]);
    await flush(() => pending[0]!.resolve(jsonResponse(body)));
    await flush(() => result.current.poll());
    await flush(() => pending[1]!.resolve(notFoundNoSnapshotResponse()));
    expect(result.current.state).toMatchObject({ status: "success", data: body });
    expect((result.current.state as { refreshError: unknown }).refreshError).not.toBeNull();
  });
});

describe("useNetworkDiagnosis: polling protections", () => {
  test("poll is skipped while a request is in flight (no overlap)", async () => {
    const { fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => result.current.poll());
    await flush(() => result.current.poll());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("poll runs once the previous request settled", async () => {
    const { pending, fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => pending[0]!.resolve(jsonResponse(healthyDiagnosis())));
    await flush(() => result.current.poll());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("a superseded (manually refreshed) response is ignored", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    await flush(() => result.current.refresh());
    expect(pending).toHaveLength(2);
    expect(pending[0]!.signal.aborted).toBe(true);

    const stale = diagnosisResponse([physicalDiagnosis()], { collection_id: "stale" });
    const fresh = diagnosisResponse([], { collection_id: "fresh" });
    await flush(() => pending[1]!.resolve(jsonResponse(fresh)));
    await flush(() => pending[0]!.resolve(jsonResponse(stale)));
    expect(result.current.state).toMatchObject({ status: "success", data: fresh });
  });

  test("an older collected_at never replaces a newer response", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    const newer = diagnosisResponse([], {
      collection_id: "newer",
      collected_at: "2026-10-07T12:05:00Z",
    });
    const older = diagnosisResponse([physicalDiagnosis()], {
      collection_id: "older",
      collected_at: "2026-10-07T12:01:00Z",
    });
    await flush(() => pending[0]!.resolve(jsonResponse(newer)));
    await flush(() => result.current.poll());
    await flush(() => pending[1]!.resolve(jsonResponse(older)));
    expect(result.current.state).toMatchObject({ status: "success", data: newer });
  });

  test("a newer collected_at replaces the held response", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    const first = diagnosisResponse([], { collected_at: "2026-10-07T12:01:00Z" });
    const second = diagnosisResponse([physicalDiagnosis()], {
      collection_id: "second",
      collected_at: "2026-10-07T12:02:00Z",
    });
    await flush(() => pending[0]!.resolve(jsonResponse(first)));
    await flush(() => result.current.poll());
    await flush(() => pending[1]!.resolve(jsonResponse(second)));
    expect(result.current.state).toMatchObject({ status: "success", data: second });
  });

  test("the same instant with a different collection_id keeps the current response", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkDiagnosis());
    const current = diagnosisResponse([], { collection_id: DEGRADED_COLLECTION_ID });
    const conflicting = diagnosisResponse([physicalDiagnosis()], { collection_id: "other" });
    await flush(() => pending[0]!.resolve(jsonResponse(current)));
    await flush(() => result.current.poll());
    await flush(() => pending[1]!.resolve(jsonResponse(conflicting)));
    expect(result.current.state).toMatchObject({ status: "success", data: current });
  });

  test("unmount aborts the in-flight request and ignores its late response", async () => {
    const { pending } = parkedFetch();
    const { result, unmount } = renderHook(() => useNetworkDiagnosis());
    unmount();
    expect(pending[0]!.signal.aborted).toBe(true);
    // Late settle after unmount must not throw or update state.
    await act(async () => {
      pending[0]!.resolve(jsonResponse(healthyDiagnosis()));
      await Promise.resolve();
    });
    expect(result.current.state).toEqual({ status: "loading" });
  });

  test("the hook itself owns no timer (no runaway timers)", () => {
    vi.useFakeTimers();
    parkedFetch();
    const { unmount } = renderHook(() => useNetworkDiagnosis());
    expect(vi.getTimerCount()).toBe(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

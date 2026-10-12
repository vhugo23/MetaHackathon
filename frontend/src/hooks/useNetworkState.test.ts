import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { MALFORMED_RESPONSE_MESSAGE } from "../api/client";
import type { NetworkStateResponse } from "../api/networkStateTypes";
import {
  BASELINE_NETWORK_STATE,
  DEGRADED_NETWORK_STATE,
  cloneState,
  jsonResponse,
  notFoundNoSnapshotResponse,
} from "../test/networkFixtures";
import { useNetworkState } from "./useNetworkState";

/** Runs `action` inside `act` and lets the resulting promise callbacks settle. */
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
 * A fetch stub that parks every request so tests decide exactly when, and
 * in what order, responses arrive. It deliberately ignores the abort signal
 * when settling (like a client that does not honor it), so the hook's own
 * request-ID / collected_at guards are what is under test.
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

function snapshot(
  collectedAt: string,
  collectionId: string,
  base: NetworkStateResponse = BASELINE_NETWORK_STATE,
): NetworkStateResponse {
  return { ...cloneState(base), collected_at: collectedAt, collection_id: collectionId };
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("useNetworkState: states", () => {
  test("starts loading and issues exactly one GET /network/state", () => {
    const { pending, fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    expect(result.current.state).toEqual({ status: "loading" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]![0]).toBe("http://localhost:8080/network/state");
    expect(pending).toHaveLength(1);
  });

  test("success", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({
      status: "success",
      data: BASELINE_NETWORK_STATE,
      isRefreshing: false,
      refreshError: null,
    });
  });

  test("404 operational_state_not_found is the no-snapshot state, not an error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(notFoundNoSnapshotResponse()));
    expect(result.current.state).toMatchObject({ status: "no-snapshot", isRefreshing: false });
  });

  test("a transport failure is an unreachable error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.reject(new TypeError("Failed to fetch")));
    expect(result.current.state).toMatchObject({ status: "error", error: { kind: "unreachable" } });
  });

  test("an API error status is an api error (distinct from no-snapshot)", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
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
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse({ nodes: [] })));
    expect(result.current.state).toMatchObject({
      status: "error",
      error: { kind: "malformed", message: MALFORMED_RESPONSE_MESSAGE },
    });
  });

  test("manual refresh from error shows loading again, then recovers", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.reject(new TypeError("down")));
    act(() => result.current.refresh());
    expect(result.current.state).toEqual({ status: "loading" });
    await flush(() => pending[1]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state.status).toBe("success");
  });

  test("manual refresh marks isRefreshing and keeps the current data visible", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    act(() => result.current.refresh());
    expect(result.current.state).toMatchObject({
      status: "success",
      isRefreshing: true,
      data: BASELINE_NETWORK_STATE,
    });
    await flush(() => pending[1]!.resolve(jsonResponse(DEGRADED_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({
      status: "success",
      isRefreshing: false,
      data: DEGRADED_NETWORK_STATE,
    });
  });

  test("a no-snapshot state becomes success once a snapshot exists", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(notFoundNoSnapshotResponse()));
    act(() => result.current.refresh());
    await flush(() => pending[1]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state.status).toBe("success");
  });

  test("lastUpdatedAt is the client request-completion time", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-07T12:30:00Z"));
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({ lastUpdatedAt: "2026-10-07T12:30:00.000Z" });
  });
});

describe("useNetworkState: refresh failures keep the current snapshot", () => {
  async function loaded() {
    const harness = parkedFetch();
    const hook = renderHook(() => useNetworkState());
    await flush(() => harness.pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    return { ...harness, ...hook };
  }

  test("network failure on refresh retains data and records a refreshError", async () => {
    const { pending, result } = await loaded();
    act(() => result.current.refresh());
    await flush(() => pending[1]!.reject(new TypeError("Failed to fetch")));
    expect(result.current.state).toMatchObject({
      status: "success",
      data: BASELINE_NETWORK_STATE,
      isRefreshing: false,
      refreshError: { kind: "unreachable" },
    });
  });

  test("malformed response on refresh retains data", async () => {
    const { pending, result } = await loaded();
    act(() => result.current.refresh());
    await flush(() => pending[1]!.resolve(jsonResponse([])));
    expect(result.current.state).toMatchObject({
      status: "success",
      data: BASELINE_NETWORK_STATE,
      refreshError: { kind: "malformed" },
    });
  });

  test("a later successful refresh clears the refreshError", async () => {
    const { pending, result } = await loaded();
    act(() => result.current.refresh());
    await flush(() => pending[1]!.reject(new TypeError("x")));
    act(() => result.current.refresh());
    await flush(() => pending[2]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({ status: "success", refreshError: null });
  });

  test("an unexpected 404 after a snapshot keeps the snapshot", async () => {
    const { pending, result } = await loaded();
    act(() => result.current.refresh());
    await flush(() => pending[1]!.resolve(notFoundNoSnapshotResponse()));
    expect(result.current.state).toMatchObject({
      status: "success",
      data: BASELINE_NETWORK_STATE,
      refreshError: { kind: "api" },
    });
  });
});

describe("useNetworkState: stale-response protection", () => {
  test("a newer collected_at replaces the current snapshot", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    act(() => result.current.refresh());
    await flush(() => pending[1]!.resolve(jsonResponse(DEGRADED_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({ data: { collection_id: "collection-degraded" } });
  });

  test("an older collected_at cannot overwrite a newer snapshot", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(DEGRADED_NETWORK_STATE)));
    act(() => result.current.refresh());
    await flush(() => pending[1]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({
      status: "success",
      isRefreshing: false,
      data: { collection_id: "collection-degraded" },
    });
  });

  test("equal collected_at with a different collection_id cannot overwrite", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    act(() => result.current.refresh());
    await flush(() =>
      pending[1]!.resolve(jsonResponse(snapshot(BASELINE_NETWORK_STATE.collected_at, "impostor"))),
    );
    expect(result.current.state).toMatchObject({ data: { collection_id: "collection-baseline" } });
  });

  test("the same collection refreshes retrieval metadata", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    act(() => result.current.refresh());
    const later = { ...cloneState(BASELINE_NETWORK_STATE), retrieved_at: "2026-10-07T12:00:30Z" };
    await flush(() => pending[1]!.resolve(jsonResponse(later)));
    expect(result.current.state).toMatchObject({
      data: { collection_id: "collection-baseline", retrieved_at: "2026-10-07T12:00:30Z" },
    });
  });

  test("an out-of-order response from a superseded request is dropped", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    act(() => result.current.refresh()); // request #2 supersedes the mount request #1
    expect(pending).toHaveLength(2);
    expect(pending[0]!.signal.aborted).toBe(true);
    await flush(() => pending[1]!.resolve(jsonResponse(DEGRADED_NETWORK_STATE)));
    // The superseded (older) request answers late with an older snapshot.
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({ data: { collection_id: "collection-degraded" } });
  });

  test("even after reordered completion, state never moves to an older collected_at", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(snapshot("2026-10-07T12:00:10Z", "c10"))));
    const seen: string[] = [];
    for (const [index, at] of [
      "2026-10-07T12:00:05Z",
      "2026-10-07T12:00:20Z",
      "2026-10-07T12:00:15Z",
    ].entries()) {
      act(() => result.current.refresh());
      await flush(() => pending[index + 1]!.resolve(jsonResponse(snapshot(at, `c-${at}`))));
      seen.push(
        result.current.state.status === "success" ? result.current.state.data.collected_at : "none",
      );
    }
    expect(seen).toEqual(["2026-10-07T12:00:10Z", "2026-10-07T12:00:20Z", "2026-10-07T12:00:20Z"]);
  });
});

describe("useNetworkState: polling primitive and cleanup", () => {
  test("poll() is a no-op while a request is in flight (no overlapping requests)", () => {
    const { fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    act(() => result.current.poll());
    act(() => result.current.poll());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("poll() starts a request once idle, without flagging isRefreshing", async () => {
    const { pending, fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE)));
    act(() => result.current.poll());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.state).toMatchObject({ status: "success", isRefreshing: false });
    await flush(() => pending[1]!.resolve(jsonResponse(DEGRADED_NETWORK_STATE)));
    expect(result.current.state).toMatchObject({ data: { collection_id: "collection-degraded" } });
  });

  test("poll() resumes after a failed request", async () => {
    const { pending, fetchMock } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    await flush(() => pending[0]!.reject(new TypeError("down")));
    act(() => result.current.poll());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("a manual refresh aborts the in-flight request", () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    act(() => result.current.refresh());
    expect(pending[0]!.signal.aborted).toBe(true);
    expect(pending[1]!.signal.aborted).toBe(false);
  });

  test("unmount aborts the in-flight request and ignores its late response", async () => {
    const { pending } = parkedFetch();
    const { result, unmount } = renderHook(() => useNetworkState());
    unmount();
    expect(pending[0]!.signal.aborted).toBe(true);
    pending[0]!.resolve(jsonResponse(BASELINE_NETWORK_STATE));
    await waitFor(() => expect(result.current.state).toEqual({ status: "loading" }));
  });

  test("an aborted request is never surfaced as an error", async () => {
    const { pending } = parkedFetch();
    const { result } = renderHook(() => useNetworkState());
    act(() => result.current.refresh());
    const abortError = new DOMException("aborted", "AbortError");
    await flush(() => pending[0]!.reject(abortError));
    expect(result.current.state.status).toBe("loading");
  });
});

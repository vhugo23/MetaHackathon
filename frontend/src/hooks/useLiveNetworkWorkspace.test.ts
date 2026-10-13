import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { healthyDiagnosis } from "../test/diagnosisFixtures";
import { BASELINE_NETWORK_STATE, failureIncidents, jsonResponse } from "../test/networkFixtures";
import { LIVE_REFRESH_INTERVAL_MS, useLiveNetworkWorkspace } from "./useLiveNetworkWorkspace";

/** Runs `action` inside `act` and lets the resulting promise callbacks settle. */
async function flush(action: () => void): Promise<void> {
  await act(async () => {
    action();
    await Promise.resolve();
  });
}

type Call = { path: string; signal: AbortSignal; settle: (response: Response) => void };

/** Parks each request by path so tests control ordering and in-flight overlap. */
function parkedFetchByPath() {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init: RequestInit) => {
      return new Promise<Response>((resolve) => {
        calls.push({
          path: new URL(url).pathname,
          signal: init.signal as AbortSignal,
          settle: resolve,
        });
      });
    }),
  );
  return {
    calls,
    count: (path: string) => calls.filter((c) => c.path === path).length,
    last: (path: string) => calls.filter((c) => c.path === path).at(-1)!,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

async function settleInitial(net: ReturnType<typeof parkedFetchByPath>) {
  await flush(() => {
    net.last("/network/state").settle(jsonResponse(BASELINE_NETWORK_STATE));
    net.last("/incidents").settle(jsonResponse(failureIncidents()));
  });
}

test("the documented interval is 5 seconds", () => {
  expect(LIVE_REFRESH_INTERVAL_MS).toBe(5000);
});

test("loads network state and incidents on mount, as two independent requests", () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  expect(net.count("/network/state")).toBe(1);
  expect(net.count("/incidents")).toBe(1);
});

test("each interval tick refreshes BOTH network state and incidents", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  await settleInitial(net);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
  });
  expect(net.count("/network/state")).toBe(2);
  expect(net.count("/incidents")).toBe(2);
});

test("no tick fires before the interval elapses", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  await settleInitial(net);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS - 1);
  });
  expect(net.count("/network/state")).toBe(1);
  expect(net.count("/incidents")).toBe(1);
});

test("a slow request is never overlapped: ticks are skipped while it is in flight", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  // Initial requests never settle; five ticks pass.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS * 5);
  });
  expect(net.count("/network/state")).toBe(1);
  expect(net.count("/incidents")).toBe(1);
});

test("the two requests are independent: one in flight does not block the other", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  await flush(() => {
    net.last("/network/state").settle(jsonResponse(BASELINE_NETWORK_STATE));
  });
  // Incidents still in flight.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
  });
  expect(net.count("/network/state")).toBe(2);
  expect(net.count("/incidents")).toBe(1);
});

test("polling resumes after a failed tick", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  await settleInitial(net);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
  });
  await flush(() => {
    net
      .last("/network/state")
      .settle(jsonResponse({ code: "persistence_error", detail: "x" }, 500));
    net.last("/incidents").settle(jsonResponse({ code: "persistence_error", detail: "x" }, 500));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
  });
  expect(net.count("/network/state")).toBe(3);
  expect(net.count("/incidents")).toBe(3);
});

test("a failed background incident poll keeps the loaded incidents", async () => {
  const net = parkedFetchByPath();
  const { result } = renderHook(() => useLiveNetworkWorkspace());
  await settleInitial(net);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
  });
  await flush(() => {
    net.last("/network/state").settle(jsonResponse(BASELINE_NETWORK_STATE));
    net.last("/incidents").settle(jsonResponse({ code: "persistence_error", detail: "boom" }, 500));
  });
  expect(result.current.incidents.state).toMatchObject({
    status: "success",
    data: failureIncidents(),
    refreshError: "boom",
  });
});

test("manual refreshAll re-requests both endpoints immediately", async () => {
  const net = parkedFetchByPath();
  const { result } = renderHook(() => useLiveNetworkWorkspace());
  await settleInitial(net);
  act(() => result.current.refreshAll());
  expect(net.count("/network/state")).toBe(2);
  expect(net.count("/incidents")).toBe(2);
});

test("unmount clears the timer and aborts in-flight requests", async () => {
  const net = parkedFetchByPath();
  const { unmount } = renderHook(() => useLiveNetworkWorkspace());
  expect(net.last("/network/state").signal.aborted).toBe(false);
  unmount();
  expect(net.last("/network/state").signal.aborted).toBe(true);
  expect(net.last("/incidents").signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);

  await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS * 3);
  expect(net.count("/network/state")).toBe(1);
  expect(net.count("/incidents")).toBe(1);
});

test("each interval tick also re-reads GET /network/diagnosis, on the same 5 s cadence", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  expect(net.count("/network/diagnosis")).toBe(1);
  await flush(() => {
    net.last("/network/state").settle(jsonResponse(BASELINE_NETWORK_STATE));
    net.last("/network/diagnosis").settle(jsonResponse(healthyDiagnosis()));
    net.last("/incidents").settle(jsonResponse(failureIncidents()));
  });

  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
  });
  expect(net.count("/network/diagnosis")).toBe(2);
});

test("a diagnosis request still in flight is not overlapped by a tick", async () => {
  const net = parkedFetchByPath();
  renderHook(() => useLiveNetworkWorkspace());
  await flush(() => {
    net.last("/network/state").settle(jsonResponse(BASELINE_NETWORK_STATE));
    net.last("/incidents").settle(jsonResponse(failureIncidents()));
    // The diagnosis request is deliberately left unsettled.
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS * 3);
  });
  expect(net.count("/network/diagnosis")).toBe(1);
  expect(net.count("/network/state")).toBe(2); // only the first tick; later ones are in flight
});

test("unmount stops all three pollers and aborts the diagnosis request", async () => {
  const net = parkedFetchByPath();
  const { unmount } = renderHook(() => useLiveNetworkWorkspace());
  unmount();
  expect(net.last("/network/diagnosis").signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS * 3);
  });
  expect(net.count("/network/diagnosis")).toBe(1);
});

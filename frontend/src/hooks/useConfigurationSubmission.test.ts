import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useConfigurationSubmission } from "./useConfigurationSubmission";
import { ApiRequestError } from "../api/client";
import * as configurationsModule from "../api/configurations";
import type { ConfigurationSubmissionRequest, ConfigurationSubmissionResponse } from "../api/types";

vi.mock("../api/configurations", () => ({
  submitDeviceConfiguration: vi.fn(),
}));

const submitDeviceConfigurationMock = vi.mocked(configurationsModule.submitDeviceConfiguration);

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

const validRequest: ConfigurationSubmissionRequest = {
  vendor: "cisco-ios-xe",
  raw_config_text: "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n",
};

const validResponse: ConfigurationSubmissionResponse = {
  device_id: "spine-01",
  snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  normalized_config: {
    hostname: "spine-01",
    interfaces: [],
    routing: { bgp_neighbors: [] },
    acls: [],
  },
  violations_detected: 0,
  incidents_created: 0,
  incidents_updated: 0,
};

function abortError(): Error {
  return Object.assign(new Error("The operation was aborted."), { name: "AbortError" });
}

beforeEach(() => {
  submitDeviceConfigurationMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. Initial state
// ---------------------------------------------------------------------------

test("initial state is idle", () => {
  const { result } = renderHook(() => useConfigurationSubmission());

  expect(result.current.state).toEqual({ status: "idle" });
});

// ---------------------------------------------------------------------------
// 2. submit() call shape
// ---------------------------------------------------------------------------

test("submit calls submitDeviceConfiguration exactly once with the original device ID, request, and an AbortSignal", async () => {
  const deferred = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock.mockReturnValue(deferred.promise);

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
  const [deviceId, request, callOptions] = submitDeviceConfigurationMock.mock.calls[0]!;
  expect(deviceId).toBe("spine-01");
  expect(request).toEqual(validRequest);
  expect(callOptions?.signal).toBeInstanceOf(AbortSignal);

  await act(async () => {
    deferred.resolve(validResponse);
    await deferred.promise;
  });
});

// ---------------------------------------------------------------------------
// 3. submitting state
// ---------------------------------------------------------------------------

test("state becomes submitting while the request is pending", () => {
  const deferred = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock.mockReturnValue(deferred.promise);

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  expect(result.current.state).toEqual({ status: "submitting" });
});

// ---------------------------------------------------------------------------
// 4. success
// ---------------------------------------------------------------------------

test("a successful current request transitions to success with the exact response object", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(result.current.state).toEqual({ status: "success", response: validResponse });
  });
});

// ---------------------------------------------------------------------------
// 5. ApiRequestError
// ---------------------------------------------------------------------------

test("an ApiRequestError transitions to error with its controlled message and code", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(
    new ApiRequestError("vendor not recognized", "unsupported_vendor"),
  );

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(result.current.state).toEqual({
      status: "error",
      message: "vendor not recognized",
      code: "unsupported_vendor",
    });
  });
});

// ---------------------------------------------------------------------------
// 6. network / unexpected failure
// ---------------------------------------------------------------------------

test("a network or unexpected failure uses the stable safe network message, not the original thrown message", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(
    new TypeError("Failed to fetch: secret internal detail"),
  );

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(result.current.state.status).toBe("error");
  });
  const message = result.current.state.status === "error" ? result.current.state.message : "";
  expect(message).not.toContain("secret internal detail");
  expect(message).not.toContain("Failed to fetch");
  expect(message.length).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// 7. AbortError never surfaces
// ---------------------------------------------------------------------------

test("an AbortError does not surface as a visible error", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(abortError());

  const { result } = renderHook(() => useConfigurationSubmission());

  await act(async () => {
    result.current.submit("spine-01", validRequest);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(result.current.state.status).not.toBe("error");
});

// ---------------------------------------------------------------------------
// 8. unmount aborts
// ---------------------------------------------------------------------------

test("unmount aborts the active request", () => {
  let capturedSignal: AbortSignal | undefined;
  submitDeviceConfigurationMock.mockImplementation((_deviceId, _request, callOptions) => {
    capturedSignal = callOptions?.signal;
    return new Promise(() => {});
  });

  const { result, unmount } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  expect(capturedSignal?.aborted).toBe(false);
  unmount();
  expect(capturedSignal?.aborted).toBe(true);
});

// ---------------------------------------------------------------------------
// 9. supersession
// ---------------------------------------------------------------------------

test("a second submission aborts the first and starts exactly one new request", () => {
  const signals: AbortSignal[] = [];
  submitDeviceConfigurationMock.mockImplementation((_deviceId, _request, callOptions) => {
    signals.push(callOptions!.signal!);
    return new Promise(() => {});
  });

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });
  expect(signals[0]?.aborted).toBe(false);

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(2);
  expect(signals[0]?.aborted).toBe(true);
  expect(signals[1]?.aborted).toBe(false);
});

// ---------------------------------------------------------------------------
// 10. stale success guard
// ---------------------------------------------------------------------------

test("a late-resolving stale success cannot overwrite a newer result", async () => {
  const first = createDeferred<ConfigurationSubmissionResponse>();
  const second = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });
  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  const secondResponse: ConfigurationSubmissionResponse = {
    ...validResponse,
    snapshot_id: "second-snapshot",
  };
  await act(async () => {
    second.resolve(secondResponse);
    await second.promise;
  });

  await waitFor(() => {
    expect(result.current.state).toEqual({ status: "success", response: secondResponse });
  });

  await act(async () => {
    first.resolve(validResponse);
    await first.promise.catch(() => {});
  });

  expect(result.current.state).toEqual({ status: "success", response: secondResponse });
});

// ---------------------------------------------------------------------------
// 11. stale failure guard
// ---------------------------------------------------------------------------

test("a late-resolving stale failure cannot overwrite a newer result", async () => {
  const first = createDeferred<ConfigurationSubmissionResponse>();
  const second = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);

  const { result } = renderHook(() => useConfigurationSubmission());

  act(() => {
    result.current.submit("spine-01", validRequest);
  });
  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await act(async () => {
    second.resolve(validResponse);
    await second.promise;
  });

  await waitFor(() => {
    expect(result.current.state).toEqual({ status: "success", response: validResponse });
  });

  await act(async () => {
    first.reject(new ApiRequestError("stale failure", "device_conflict"));
    await first.promise.catch(() => {});
  });

  expect(result.current.state).toEqual({ status: "success", response: validResponse });
});

// ---------------------------------------------------------------------------
// 12. onSuccess called exactly once
// ---------------------------------------------------------------------------

test("onSuccess is called exactly once for a current successful request", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  const onSuccess = vi.fn();

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });
  expect(onSuccess).toHaveBeenCalledWith(validResponse);
});

// ---------------------------------------------------------------------------
// 13. onSuccess withheld in every non-current-success case
// ---------------------------------------------------------------------------

test("onSuccess is not called after an ApiRequestError", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(new ApiRequestError("bad", "invalid_request"));
  const onSuccess = vi.fn();

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(result.current.state.status).toBe("error");
  });
  expect(onSuccess).not.toHaveBeenCalled();
});

test("onSuccess is not called after a network failure", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(new TypeError("network down"));
  const onSuccess = vi.fn();

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(result.current.state.status).toBe("error");
  });
  expect(onSuccess).not.toHaveBeenCalled();
});

test("onSuccess is not called after an AbortError", async () => {
  submitDeviceConfigurationMock.mockRejectedValue(abortError());
  const onSuccess = vi.fn();

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  await act(async () => {
    result.current.submit("spine-01", validRequest);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(onSuccess).not.toHaveBeenCalled();
});

test("onSuccess is not called for a stale (superseded) success", async () => {
  const first = createDeferred<ConfigurationSubmissionResponse>();
  const second = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);
  const onSuccess = vi.fn();

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });
  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await act(async () => {
    second.resolve(validResponse);
    await second.promise;
  });
  await waitFor(() => {
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  await act(async () => {
    first.resolve({ ...validResponse, snapshot_id: "stale" });
    await first.promise.catch(() => {});
  });

  expect(onSuccess).toHaveBeenCalledTimes(1);
});

test("onSuccess is not called for a stale (superseded) failure", async () => {
  const first = createDeferred<ConfigurationSubmissionResponse>();
  const second = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);
  const onSuccess = vi.fn();

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });
  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await act(async () => {
    second.resolve(validResponse);
    await second.promise;
  });
  await waitFor(() => {
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  await act(async () => {
    first.reject(new ApiRequestError("stale", "device_conflict"));
    await first.promise.catch(() => {});
  });

  expect(onSuccess).toHaveBeenCalledTimes(1);
});

// ---------------------------------------------------------------------------
// 14 / 15. onSuccess callback-ref freshness, no restart/duplication
// ---------------------------------------------------------------------------

test("uses the latest onSuccess callback when its identity changes during an in-flight request", async () => {
  const deferred = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock.mockReturnValue(deferred.promise);
  const firstCallback = vi.fn();
  const secondCallback = vi.fn();

  const { result, rerender } = renderHook(
    ({ onSuccess }: { onSuccess: (response: ConfigurationSubmissionResponse) => void }) =>
      useConfigurationSubmission({ onSuccess }),
    { initialProps: { onSuccess: firstCallback } },
  );

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  rerender({ onSuccess: secondCallback });

  await act(async () => {
    deferred.resolve(validResponse);
    await deferred.promise;
  });

  expect(firstCallback).not.toHaveBeenCalled();
  expect(secondCallback).toHaveBeenCalledTimes(1);
  expect(secondCallback).toHaveBeenCalledWith(validResponse);
});

test("changing onSuccess identity does not restart or duplicate the POST", async () => {
  const deferred = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock.mockReturnValue(deferred.promise);

  const { result, rerender } = renderHook(
    ({ onSuccess }: { onSuccess: (response: ConfigurationSubmissionResponse) => void }) =>
      useConfigurationSubmission({ onSuccess }),
    { initialProps: { onSuccess: vi.fn() } },
  );

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  rerender({ onSuccess: vi.fn() });
  rerender({ onSuccess: vi.fn() });

  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);

  await act(async () => {
    deferred.resolve(validResponse);
    await deferred.promise;
  });
});

// ---------------------------------------------------------------------------
// 16 / 17. callback failure isolation
// ---------------------------------------------------------------------------

test("a synchronous onSuccess exception leaves the hook in success", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  const onSuccess = vi.fn(() => {
    throw new Error("callback exploded");
  });

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  await waitFor(() => {
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });
  expect(result.current.state).toEqual({ status: "success", response: validResponse });
});

test("a rejected onSuccess Promise leaves the hook in success and produces no unhandled rejection", async () => {
  submitDeviceConfigurationMock.mockResolvedValue(validResponse);
  const onSuccess = vi.fn().mockRejectedValue(new Error("callback rejected"));

  const { result } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  // If the hook failed to attach a .catch() to the callback's rejected
  // promise, this would surface as a real unhandled rejection — which
  // Vitest itself fails the run on, independent of any assertion below.
  await act(async () => {
    result.current.submit("spine-01", validRequest);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(result.current.state).toEqual({ status: "success", response: validResponse });
});

// ---------------------------------------------------------------------------
// 18. unmount before resolution
// ---------------------------------------------------------------------------

test("unmount before resolution prevents onSuccess", async () => {
  const deferred = createDeferred<ConfigurationSubmissionResponse>();
  submitDeviceConfigurationMock.mockReturnValue(deferred.promise);
  const onSuccess = vi.fn();

  const { result, unmount } = renderHook(() => useConfigurationSubmission({ onSuccess }));

  act(() => {
    result.current.submit("spine-01", validRequest);
  });

  unmount();

  await act(async () => {
    deferred.resolve(validResponse);
    await deferred.promise.catch(() => {});
  });

  expect(onSuccess).not.toHaveBeenCalled();
});

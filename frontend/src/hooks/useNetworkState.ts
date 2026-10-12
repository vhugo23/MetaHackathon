import { useCallback, useEffect, useRef, useState } from "react";
import { ApiRequestError, MALFORMED_RESPONSE_MESSAGE } from "../api/client";
import { fetchNetworkState, isOperationalStateNotFound } from "../api/networkState";
import type { NetworkStateResponse } from "../api/networkStateTypes";
import { reconcileNetworkState } from "../network/reconcile";

const NETWORK_ERROR_MESSAGE = "Could not reach the Meta RNE API. Check your connection and retry.";

/**
 * `unreachable`: the request itself failed (network down, CORS, refused);
 * `malformed`: the server answered 2xx with a body this client rejects;
 * `api`: the server answered with an error status (not "no snapshot").
 */
export type NetworkErrorKind = "unreachable" | "malformed" | "api";

export interface NetworkStateError {
  kind: NetworkErrorKind;
  message: string;
}

export type NetworkStateViewState =
  | { status: "loading" }
  /** 404 `operational_state_not_found`: nothing ingested yet — not a failure. */
  | { status: "no-snapshot"; isRefreshing: boolean; lastUpdatedAt: string }
  | {
      status: "success";
      data: NetworkStateResponse;
      /** Client time the last request completed (request metadata, not collection time). */
      lastUpdatedAt: string;
      isRefreshing: boolean;
      /** Set when the latest refresh failed; the previous snapshot stays visible. */
      refreshError: NetworkStateError | null;
    }
  | { status: "error"; error: NetworkStateError };

export interface UseNetworkStateResult {
  state: NetworkStateViewState;
  /** Manual refresh: supersedes any in-flight request and shows `isRefreshing`. */
  refresh: () => void;
  /** Background refresh: a no-op while a request is already in flight. */
  poll: () => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function classifyNetworkError(error: unknown): NetworkStateError {
  if (error instanceof ApiRequestError) {
    return {
      kind: error.message === MALFORMED_RESPONSE_MESSAGE ? "malformed" : "api",
      message: error.message,
    };
  }
  return { kind: "unreachable", message: NETWORK_ERROR_MESSAGE };
}

export function useNetworkState(): UseNetworkStateResult {
  const [state, setState] = useState<NetworkStateViewState>({ status: "loading" });
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const inFlightIdRef = useRef<number | null>(null);
  const isMountedRef = useRef(true);

  const runFetch = useCallback((controller: AbortController, requestId: number) => {
    const isCurrent = () => isMountedRef.current && requestIdRef.current === requestId;
    const settle = () => {
      if (inFlightIdRef.current === requestId) {
        inFlightIdRef.current = null;
      }
    };

    fetchNetworkState({ signal: controller.signal })
      .then((incoming) => {
        if (!isCurrent()) {
          return;
        }
        settle();
        const completedAt = new Date().toISOString();
        setState((previous) => {
          const kept = previous.status === "success" ? previous.data : null;
          return {
            status: "success",
            // Older collected_at never replaces a newer snapshot.
            data: reconcileNetworkState(kept, incoming),
            lastUpdatedAt: completedAt,
            isRefreshing: false,
            refreshError: null,
          };
        });
      })
      .catch((error: unknown) => {
        if (!isCurrent()) {
          return;
        }
        if (isAbortError(error)) {
          return;
        }
        settle();
        const completedAt = new Date().toISOString();
        if (isOperationalStateNotFound(error)) {
          setState((previous) =>
            previous.status === "success"
              ? {
                  // A snapshot cannot disappear; keep what we have and flag it.
                  ...previous,
                  isRefreshing: false,
                  refreshError: classifyNetworkError(error),
                }
              : { status: "no-snapshot", isRefreshing: false, lastUpdatedAt: completedAt },
          );
          return;
        }
        const classified = classifyNetworkError(error);
        setState((previous) =>
          previous.status === "success"
            ? { ...previous, isRefreshing: false, refreshError: classified }
            : { status: "error", error: classified },
        );
      });
  }, []);

  const beginNewRequest = useCallback(() => {
    abortControllerRef.current?.abort();
    const requestId = (requestIdRef.current += 1);
    inFlightIdRef.current = requestId;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    runFetch(controller, requestId);
  }, [runFetch]);

  const refresh = useCallback(() => {
    setState((previous) => {
      if (previous.status === "success" || previous.status === "no-snapshot") {
        return { ...previous, isRefreshing: true };
      }
      return { status: "loading" };
    });
    beginNewRequest();
  }, [beginNewRequest]);

  const poll = useCallback(() => {
    if (inFlightIdRef.current !== null) {
      return;
    }
    beginNewRequest();
  }, [beginNewRequest]);

  useEffect(() => {
    isMountedRef.current = true;
    beginNewRequest();
    return () => {
      isMountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, [beginNewRequest]);

  return { state, refresh, poll };
}

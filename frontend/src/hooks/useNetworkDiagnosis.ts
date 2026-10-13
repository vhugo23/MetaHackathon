import { useCallback, useEffect, useRef, useState } from "react";
import { fetchNetworkDiagnosis } from "../api/networkDiagnosis";
import type { NetworkDiagnosisResponse } from "../api/networkDiagnosisTypes";
import { isOperationalStateNotFound } from "../api/networkState";
import { reconcileNetworkDiagnosis } from "../network/reconcile";
import { classifyNetworkError, type NetworkStateError } from "./useNetworkState";

export type NetworkDiagnosisViewState =
  | { status: "loading" }
  /** 404 `operational_state_not_found` before any diagnosis response arrived. */
  | { status: "no-snapshot" }
  | {
      status: "success";
      data: NetworkDiagnosisResponse;
      /** Set when the latest refresh failed; the previous response stays held. */
      refreshError: NetworkStateError | null;
    }
  | { status: "error"; error: NetworkStateError };

export interface UseNetworkDiagnosisResult {
  state: NetworkDiagnosisViewState;
  /** Manual refresh: supersedes any in-flight request. */
  refresh: () => void;
  /** Background refresh: a no-op while a request is already in flight. */
  poll: () => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/**
 * Polls `GET /network/diagnosis` with the same protections as
 * `useNetworkState`: a poll is skipped while a request is in flight, a
 * superseded or post-unmount response is ignored, and an older `collected_at`
 * never replaces a newer response. Whether the held response matches the
 * displayed network snapshot is decided by the consumer (collection_id),
 * never here.
 */
export function useNetworkDiagnosis(): UseNetworkDiagnosisResult {
  const [state, setState] = useState<NetworkDiagnosisViewState>({ status: "loading" });
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

    fetchNetworkDiagnosis({ signal: controller.signal })
      .then((incoming) => {
        if (!isCurrent()) {
          return;
        }
        settle();
        setState((previous) => ({
          status: "success",
          data: reconcileNetworkDiagnosis(
            previous.status === "success" ? previous.data : null,
            incoming,
          ),
          refreshError: null,
        }));
      })
      .catch((error: unknown) => {
        if (!isCurrent() || isAbortError(error)) {
          return;
        }
        settle();
        const classified = classifyNetworkError(error);
        const notFound = isOperationalStateNotFound(error);
        setState((previous) => {
          if (previous.status === "success") {
            return { ...previous, refreshError: classified };
          }
          return notFound ? { status: "no-snapshot" } : { status: "error", error: classified };
        });
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

  return { state, refresh: beginNewRequest, poll };
}

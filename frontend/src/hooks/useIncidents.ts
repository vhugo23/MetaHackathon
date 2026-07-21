import { useCallback, useEffect, useRef, useState } from "react";
import { fetchIncidents } from "../api/incidents";
import { ApiRequestError } from "../api/client";
import type { IncidentResponse } from "../api/types";

const NETWORK_ERROR_MESSAGE = "Could not reach the Meta RNE API. Check your connection and retry.";

export type IncidentsState =
  | { status: "loading" }
  | { status: "success"; data: IncidentResponse[]; lastUpdatedAt: string; isRefreshing: boolean }
  | { status: "error"; message: string };

export interface UseIncidentsResult {
  state: IncidentsState;
  refresh: () => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function useIncidents(): UseIncidentsResult {
  const [state, setState] = useState<IncidentsState>({ status: "loading" });
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const isMountedRef = useRef(true);

  // Only ever called from a promise callback (.then()/.catch()), never
  // synchronously from the effect body below — that's what keeps the
  // mount effect itself free of a direct setState call.
  const runFetch = useCallback((controller: AbortController, requestId: number) => {
    fetchIncidents({ signal: controller.signal })
      .then((data) => {
        if (!isMountedRef.current || requestIdRef.current !== requestId) {
          return;
        }
        setState({
          status: "success",
          data,
          lastUpdatedAt: new Date().toISOString(),
          isRefreshing: false,
        });
      })
      .catch((error: unknown) => {
        if (!isMountedRef.current || requestIdRef.current !== requestId) {
          return;
        }
        if (isAbortError(error)) {
          // Deliberate cancellation (superseded by a newer request, or the
          // component is unmounting) — never a user-visible error.
          return;
        }
        const message = error instanceof ApiRequestError ? error.message : NETWORK_ERROR_MESSAGE;
        setState({ status: "error", message });
      });
  }, []);

  // Cancels whatever request is currently in flight (if any), mints a new
  // request ID — the authoritative staleness guard read back in
  // `runFetch`, independent of whether the underlying client actually
  // honors AbortSignal — and starts exactly one new request.
  const beginNewRequest = useCallback(() => {
    abortControllerRef.current?.abort();
    const requestId = (requestIdRef.current += 1);
    const controller = new AbortController();
    abortControllerRef.current = controller;
    runFetch(controller, requestId);
  }, [runFetch]);

  const refresh = useCallback(() => {
    setState((previous) =>
      previous.status === "success" ? { ...previous, isRefreshing: true } : { status: "loading" },
    );
    beginNewRequest();
  }, [beginNewRequest]);

  useEffect(() => {
    isMountedRef.current = true;
    // No setState call here: the initial `useState` value above is already
    // `{ status: "loading" }`, so the mount effect only needs to start the
    // request itself.
    beginNewRequest();

    return () => {
      isMountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, [beginNewRequest]);

  return { state, refresh };
}

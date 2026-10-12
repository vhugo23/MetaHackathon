import { useCallback, useEffect } from "react";
import { useIncidents, type UseIncidentsResult } from "./useIncidents";
import { useNetworkState, type UseNetworkStateResult } from "./useNetworkState";

/**
 * Frontend presentation cadence only. It is NOT the collector cadence: the
 * API serves the latest *ingested* state, and polling never collects,
 * ingests or changes anything - it only re-reads GET /network/state and
 * GET /incidents so the view converges during a live demo.
 */
export const LIVE_REFRESH_INTERVAL_MS = 5000;

export interface UseLiveNetworkWorkspaceResult {
  network: UseNetworkStateResult;
  incidents: UseIncidentsResult;
  /** Manual refresh of both (independent) requests. */
  refreshAll: () => void;
}

export function useLiveNetworkWorkspace(
  intervalMs: number = LIVE_REFRESH_INTERVAL_MS,
): UseLiveNetworkWorkspaceResult {
  const network = useNetworkState();
  const incidents = useIncidents();
  const { poll: pollNetwork, refresh: refreshNetwork } = network;
  const { poll: pollIncidents, refresh: refreshIncidents } = incidents;

  // The two requests are independent (no atomicity assumed); each `poll`
  // skips itself while its own previous request is still in flight, so a
  // slow server can never accumulate overlapping requests.
  useEffect(() => {
    const timer = setInterval(() => {
      pollNetwork();
      pollIncidents();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, pollNetwork, pollIncidents]);

  const refreshAll = useCallback(() => {
    refreshNetwork();
    refreshIncidents();
  }, [refreshNetwork, refreshIncidents]);

  return { network, incidents, refreshAll };
}

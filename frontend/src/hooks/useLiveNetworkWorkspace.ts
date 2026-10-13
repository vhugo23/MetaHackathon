import { useCallback, useEffect } from "react";
import { useIncidents, type UseIncidentsResult } from "./useIncidents";
import { useNetworkDiagnosis, type UseNetworkDiagnosisResult } from "./useNetworkDiagnosis";
import { useNetworkState, type UseNetworkStateResult } from "./useNetworkState";

/**
 * Frontend presentation cadence only. It is NOT the collector cadence: the
 * API serves the latest *ingested* state, and polling never collects,
 * ingests or changes anything - it only re-reads GET /network/state,
 * GET /network/diagnosis and GET /incidents so the view converges during a live demo.
 */
export const LIVE_REFRESH_INTERVAL_MS = 5000;

export interface UseLiveNetworkWorkspaceResult {
  network: UseNetworkStateResult;
  diagnosis: UseNetworkDiagnosisResult;
  incidents: UseIncidentsResult;
  /** Manual refresh of all (independent) requests. */
  refreshAll: () => void;
}

export function useLiveNetworkWorkspace(
  intervalMs: number = LIVE_REFRESH_INTERVAL_MS,
): UseLiveNetworkWorkspaceResult {
  const network = useNetworkState();
  const diagnosis = useNetworkDiagnosis();
  const incidents = useIncidents();
  const { poll: pollNetwork, refresh: refreshNetwork } = network;
  const { poll: pollDiagnosis, refresh: refreshDiagnosis } = diagnosis;
  const { poll: pollIncidents, refresh: refreshIncidents } = incidents;

  // The three requests are independent (no atomicity assumed); each `poll`
  // skips itself while its own previous request is still in flight, so a
  // slow server can never accumulate overlapping requests.
  useEffect(() => {
    const timer = setInterval(() => {
      pollNetwork();
      pollDiagnosis();
      pollIncidents();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, pollNetwork, pollDiagnosis, pollIncidents]);

  const refreshAll = useCallback(() => {
    refreshNetwork();
    refreshDiagnosis();
    refreshIncidents();
  }, [refreshNetwork, refreshDiagnosis, refreshIncidents]);

  return { network, diagnosis, incidents, refreshAll };
}

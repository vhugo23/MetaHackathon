import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiRequestError } from "../api/client";
import { fetchIncidents, filterAnomalyIncidentsForDevice } from "../api/incidents";
import { fetchRecentTelemetry } from "../api/telemetry";
import type { IncidentResponse, TelemetrySampleResponse } from "../api/types";

const NETWORK_ERROR_MESSAGE = "Could not reach the Meta RNE API. Check your connection and retry.";

/**
 * Fixed, deterministic query boundary for `GET
 * /devices/{device_id}/telemetry/recent`'s required `since` parameter —
 * never the browser's current clock. The telemetry simulator's scenarios
 * derive every `sampled_at` from one fixed historical base timestamp (see
 * CLAUDE.md's Day 11A entry), so a clock-based `since` would need to track
 * "now" relative to those fixed values; the epoch instead guarantees every
 * persisted sample for a device is included, independent of when the
 * browser happens to load the page.
 */
export const TELEMETRY_HISTORY_SINCE = "1970-01-01T00:00:00Z";

export interface TelemetryWorkspaceState {
  deviceId: string | undefined;
  samples: TelemetrySampleResponse[];
  anomalyIncidents: IncidentResponse[];
  telemetryError: string | undefined;
  incidentError: string | undefined;
  isInitialLoading: boolean;
  isRefreshing: boolean;
  lastRefreshedAt: string | undefined;
}

export interface UseTelemetryWorkspaceResult {
  state: TelemetryWorkspaceState;
  loadDevice: (deviceId: string) => void;
  refresh: () => void;
}

function initialState(): TelemetryWorkspaceState {
  return {
    deviceId: undefined,
    samples: [],
    anomalyIncidents: [],
    telemetryError: undefined,
    incidentError: undefined,
    isInitialLoading: false,
    isRefreshing: false,
    lastRefreshedAt: undefined,
  };
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function errorMessage(error: unknown): string {
  return error instanceof ApiRequestError ? error.message : NETWORK_ERROR_MESSAGE;
}

/**
 * Owns one device's telemetry + ANOMALY-incident snapshot for the
 * judge-facing telemetry workspace panel — entirely independent of
 * `useIncidents` (the existing full incident list keeps its own hook,
 * unchanged). Follows the same AbortController/monotonic-request-ID/
 * mounted-ref conventions as `useIncidents`/`useConfigurationSubmission`:
 * a new `loadDevice`/`refresh` call always aborts whatever request is
 * still in flight and starts exactly one new cycle, so a late-resolving
 * stale response (a prior device, a superseded refresh) can never
 * overwrite newer state, independent of whether the underlying client
 * honors `AbortSignal`.
 */
export function useTelemetryWorkspace(): UseTelemetryWorkspaceResult {
  const [state, setState] = useState<TelemetryWorkspaceState>(initialState);

  // Read synchronously by `refresh` (a stable, empty-deps callback) so its
  // "which device" decision never acts on a stale closure — synced via
  // `useLayoutEffect`, never assigned during render, matching
  // `useIncidents`'s `stateRef` precedent.
  const stateRef = useRef(state);
  useLayoutEffect(() => {
    stateRef.current = state;
  }, [state]);

  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, []);

  const beginFetch = useCallback((deviceId: string, isInitialLoad: boolean) => {
    abortControllerRef.current?.abort();
    const requestId = (requestIdRef.current += 1);
    const controller = new AbortController();
    abortControllerRef.current = controller;

    if (isInitialLoad) {
      setState({
        deviceId,
        samples: [],
        anomalyIncidents: [],
        telemetryError: undefined,
        incidentError: undefined,
        isInitialLoading: true,
        isRefreshing: false,
        lastRefreshedAt: undefined,
      });
    } else {
      setState((previous) => ({ ...previous, isRefreshing: true }));
    }

    void Promise.allSettled([
      fetchRecentTelemetry(deviceId, TELEMETRY_HISTORY_SINCE, { signal: controller.signal }),
      fetchIncidents({ signal: controller.signal }),
    ]).then(([telemetryResult, incidentsResult]) => {
      if (!isMountedRef.current || requestIdRef.current !== requestId) {
        // Superseded by a newer loadDevice/refresh call, or the component
        // has unmounted — never apply this result.
        return;
      }

      setState((previous) => {
        let samples = previous.samples;
        let telemetryError = previous.telemetryError;
        if (telemetryResult.status === "fulfilled") {
          samples = telemetryResult.value;
          telemetryError = undefined;
        } else if (!isAbortError(telemetryResult.reason)) {
          telemetryError = errorMessage(telemetryResult.reason);
        }

        let anomalyIncidents = previous.anomalyIncidents;
        let incidentError = previous.incidentError;
        if (incidentsResult.status === "fulfilled") {
          anomalyIncidents = filterAnomalyIncidentsForDevice(incidentsResult.value, deviceId);
          incidentError = undefined;
        } else if (!isAbortError(incidentsResult.reason)) {
          incidentError = errorMessage(incidentsResult.reason);
        }

        const anySucceeded =
          telemetryResult.status === "fulfilled" || incidentsResult.status === "fulfilled";

        return {
          deviceId,
          samples,
          anomalyIncidents,
          telemetryError,
          incidentError,
          isInitialLoading: false,
          isRefreshing: false,
          lastRefreshedAt: anySucceeded ? new Date().toISOString() : previous.lastRefreshedAt,
        };
      });
    });
  }, []);

  const loadDevice = useCallback(
    (rawDeviceId: string) => {
      const trimmed = rawDeviceId.trim();
      if (trimmed.length === 0) {
        return;
      }
      beginFetch(trimmed, true);
    },
    [beginFetch],
  );

  const refresh = useCallback(() => {
    const currentDeviceId = stateRef.current.deviceId;
    if (currentDeviceId === undefined) {
      return;
    }
    beginFetch(currentDeviceId, false);
  }, [beginFetch]);

  return { state, loadDevice, refresh };
}

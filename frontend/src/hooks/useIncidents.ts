import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchIncidents, resolveIncident as resolveIncidentRequest } from "../api/incidents";
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
  resolvingIds: ReadonlySet<string>;
  resolveErrors: Readonly<Record<string, string>>;
  resolveIncident: (incidentId: string) => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/**
 * Chooses which of two representations of the *same* incident_id is
 * authoritative, using parsed instants (never lexicographic string
 * comparison) so a valid-but-differently-formatted timestamp can never be
 * misjudged. Used both for GET /incidents list reconciliation and for
 * applying a resolve POST response — a stale response (older `updated_at`)
 * must never overwrite a newer one, regardless of which request (GET or
 * POST) produced it.
 */
function pickIncident(current: IncidentResponse, incoming: IncidentResponse): IncidentResponse {
  const currentUpdatedAt = Date.parse(current.updated_at);
  const incomingUpdatedAt = Date.parse(incoming.updated_at);
  const currentValid = !Number.isNaN(currentUpdatedAt);
  const incomingValid = !Number.isNaN(incomingUpdatedAt);

  if (currentValid && incomingValid && currentUpdatedAt !== incomingUpdatedAt) {
    return incomingUpdatedAt > currentUpdatedAt ? incoming : current;
  }

  if (currentValid && incomingValid) {
    // Equal instants.
    if (current.status === "RESOLVED" && incoming.status !== "RESOLVED") {
      return current;
    }
    if (incoming.status === "RESOLVED" && current.status !== "RESOLVED") {
      return incoming;
    }
    if (current.occurrence_count !== incoming.occurrence_count) {
      return current.occurrence_count > incoming.occurrence_count ? current : incoming;
    }
    return incoming;
  }

  // One or both timestamps failed to parse — never guess from raw string
  // ordering. Lifecycle safety: a RESOLVED incident is terminal in this UI,
  // so it always outranks a non-RESOLVED one; otherwise prefer the incoming
  // value so an ordinary server refresh can still update server-owned
  // fields.
  if (current.status === "RESOLVED" && incoming.status !== "RESOLVED") {
    return current;
  }
  if (incoming.status === "RESOLVED" && current.status !== "RESOLVED") {
    return incoming;
  }
  return incoming;
}

/**
 * Merges a freshly fetched incident list with the incident list already
 * held in state: matches by incident_id (via `pickIncident`), preserves the
 * incoming list's order, and appends any current-only incidents (an
 * incident the current GET response omitted, e.g. because it raced ahead
 * of a concurrently completed resolution or a newer ingestion) in their
 * existing order — correct because `GET /incidents` is unfiltered and
 * append-only for this scope; there is no deletion endpoint.
 */
function mergeIncidentLists(
  incoming: IncidentResponse[],
  current: IncidentResponse[],
): IncidentResponse[] {
  const currentById = new Map(current.map((incident) => [incident.incident_id, incident]));
  const incomingIds = new Set(incoming.map((incident) => incident.incident_id));

  const merged = incoming.map((incomingIncident) => {
    const currentIncident = currentById.get(incomingIncident.incident_id);
    return currentIncident ? pickIncident(currentIncident, incomingIncident) : incomingIncident;
  });

  const currentOnly = current.filter((incident) => !incomingIds.has(incident.incident_id));

  return [...merged, ...currentOnly];
}

export function useIncidents(): UseIncidentsResult {
  const [state, setState] = useState<IncidentsState>({ status: "loading" });
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const isMountedRef = useRef(true);

  // Always holds the latest committed state, read synchronously by
  // `resolveIncident` (a stable, empty-deps callback) so its eligibility
  // checks never act on a stale closure. Synced via `useLayoutEffect` —
  // never assigned directly during render, which React disallows for refs
  // — so it commits before the browser paints and before any event handler
  // can possibly run, following the same pattern as
  // `useConfigurationSubmission`'s `onSuccessRef`.
  const stateRef = useRef(state);
  useLayoutEffect(() => {
    stateRef.current = state;
  }, [state]);

  // The authoritative same-incident duplicate-request guard: keyed by
  // incident_id, populated synchronously before any asynchronous work
  // starts, so two calls issued before React commits a `resolvingIds`
  // state update still can't both start a POST.
  const activeResolutionRequestsRef = useRef<Map<string, AbortController>>(new Map());
  const [resolvingIds, setResolvingIds] = useState<ReadonlySet<string>>(new Set());
  const [resolveErrors, setResolveErrors] = useState<Readonly<Record<string, string>>>({});

  // Only ever called from a promise callback (.then()/.catch()), never
  // synchronously from the effect body below — that's what keeps the
  // mount effect itself free of a direct setState call.
  const runFetch = useCallback((controller: AbortController, requestId: number) => {
    fetchIncidents({ signal: controller.signal })
      .then((data) => {
        if (!isMountedRef.current || requestIdRef.current !== requestId) {
          return;
        }
        setState((previous) => ({
          status: "success",
          data: previous.status === "success" ? mergeIncidentLists(data, previous.data) : data,
          lastUpdatedAt: new Date().toISOString(),
          isRefreshing: false,
        }));
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

  const clearResolvingId = useCallback((incidentId: string) => {
    setResolvingIds((previous) => {
      if (!previous.has(incidentId)) {
        return previous;
      }
      const next = new Set(previous);
      next.delete(incidentId);
      return next;
    });
  }, []);

  const clearResolveError = useCallback((incidentId: string) => {
    setResolveErrors((previous) => {
      if (!(incidentId in previous)) {
        return previous;
      }
      const next = { ...previous };
      delete next[incidentId];
      return next;
    });
  }, []);

  const resolveIncident = useCallback(
    (incidentId: string) => {
      const currentState = stateRef.current;
      if (currentState.status !== "success") {
        return;
      }
      const incident = currentState.data.find((item) => item.incident_id === incidentId);
      if (!incident || incident.status !== "OPEN") {
        return;
      }
      if (activeResolutionRequestsRef.current.has(incidentId)) {
        return;
      }

      const controller = new AbortController();
      // Inserted synchronously, before any `await`/`.then()` — this is what
      // makes the map (not `resolvingIds` React state) the authoritative
      // duplicate guard: two synchronous calls for the same incident_id
      // both run to this point before React ever commits a state update,
      // but only the first can find the map empty.
      activeResolutionRequestsRef.current.set(incidentId, controller);

      setResolvingIds((previous) => {
        const next = new Set(previous);
        next.add(incidentId);
        return next;
      });
      clearResolveError(incidentId);

      resolveIncidentRequest(incidentId, { signal: controller.signal })
        .then((resolved) => {
          // A later retry may have already installed a newer controller for
          // this same incident_id — only the request whose controller is
          // still the map's current entry may clean up or apply its result.
          if (
            !isMountedRef.current ||
            activeResolutionRequestsRef.current.get(incidentId) !== controller
          ) {
            return;
          }
          activeResolutionRequestsRef.current.delete(incidentId);
          clearResolvingId(incidentId);
          clearResolveError(incidentId);

          setState((previous) => {
            if (previous.status !== "success") {
              return previous;
            }
            const index = previous.data.findIndex((item) => item.incident_id === incidentId);
            if (index === -1) {
              // The incident no longer exists in the current list (e.g. a
              // concurrent refresh's response hadn't included it) — never
              // insert it via the resolution response.
              return previous;
            }
            const current = previous.data[index]!;
            const chosen = pickIncident(current, resolved);
            if (chosen === current) {
              return previous;
            }
            const nextData = previous.data.slice();
            nextData[index] = chosen;
            // `lastUpdatedAt` is deliberately left unchanged — it represents
            // the last list fetch/refresh, not a local resolution clock.
            return { ...previous, data: nextData };
          });
        })
        .catch((error: unknown) => {
          if (
            !isMountedRef.current ||
            activeResolutionRequestsRef.current.get(incidentId) !== controller
          ) {
            return;
          }
          activeResolutionRequestsRef.current.delete(incidentId);
          clearResolvingId(incidentId);
          if (isAbortError(error)) {
            // Deliberate cancellation (unmount) — never a user-visible error.
            return;
          }
          const message = error instanceof ApiRequestError ? error.message : NETWORK_ERROR_MESSAGE;
          setResolveErrors((previous) => ({ ...previous, [incidentId]: message }));
        });
    },
    [clearResolveError, clearResolvingId],
  );

  useEffect(() => {
    isMountedRef.current = true;
    // No setState call here: the initial `useState` value above is already
    // `{ status: "loading" }`, so the mount effect only needs to start the
    // request itself.
    beginNewRequest();

    // Captured once per effect run (mount only, given the empty-ish
    // `[beginNewRequest]` deps below never actually change) so the cleanup
    // closure never reads a ref that could differ by the time it runs.
    const activeRequests = activeResolutionRequestsRef.current;

    return () => {
      isMountedRef.current = false;
      abortControllerRef.current?.abort();
      activeRequests.forEach((controller) => controller.abort());
      activeRequests.clear();
    };
  }, [beginNewRequest]);

  return { state, refresh, resolvingIds, resolveErrors, resolveIncident };
}

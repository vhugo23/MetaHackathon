import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { submitDeviceConfiguration } from "../api/configurations";
import { ApiRequestError } from "../api/client";
import type { ConfigurationSubmissionRequest, ConfigurationSubmissionResponse } from "../api/types";

const NETWORK_ERROR_MESSAGE = "Could not reach the Meta RNE API. Check your connection and retry.";

export type ConfigurationSubmissionState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "success"; response: ConfigurationSubmissionResponse }
  | { status: "error"; message: string; code?: string };

export interface UseConfigurationSubmissionOptions {
  onSuccess?: (response: ConfigurationSubmissionResponse) => void | Promise<void>;
}

export interface UseConfigurationSubmissionResult {
  state: ConfigurationSubmissionState;
  submit: (deviceId: string, request: ConfigurationSubmissionRequest) => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function useConfigurationSubmission(
  options: UseConfigurationSubmissionOptions = {},
): UseConfigurationSubmissionResult {
  const [state, setState] = useState<ConfigurationSubmissionState>({ status: "idle" });
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const isMountedRef = useRef(true);

  // Always holds the latest committed callback without the submit closure
  // capturing a stale one. Synced via useLayoutEffect (never assigned
  // directly during render, which React disallows for refs) rather than a
  // passive useEffect — a passive effect can still be pending when an
  // already-in-flight POST's promise resolves, which would let a stale
  // callback run instead of the latest one; useLayoutEffect commits
  // synchronously before the browser paints and before any later
  // microtask/task can observe the ref.
  const onSuccessRef = useRef(options.onSuccess);
  useLayoutEffect(() => {
    onSuccessRef.current = options.onSuccess;
  }, [options.onSuccess]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, []);

  // Cancels whatever submission is currently in flight (if any), mints a new
  // request ID, and starts exactly one new POST — the same
  // abort-then-supersede pattern as useIncidents.beginNewRequest.
  const submit = useCallback((deviceId: string, request: ConfigurationSubmissionRequest) => {
    abortControllerRef.current?.abort();
    const requestId = (requestIdRef.current += 1);
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setState({ status: "submitting" });

    submitDeviceConfiguration(deviceId, request, { signal: controller.signal })
      .then((response) => {
        if (!isMountedRef.current || requestIdRef.current !== requestId) {
          return;
        }
        setState({ status: "success", response });

        const onSuccess = onSuccessRef.current;
        if (!onSuccess) {
          return;
        }
        // onSuccess is an external follow-up effect: its outcome is
        // deliberately never awaited, never allowed to change the
        // already-committed success state, and never invoked a second time
        // no matter how it fails.
        try {
          const outcome = onSuccess(response);
          if (outcome instanceof Promise) {
            outcome.catch(() => {
              // Swallowed — a rejected callback must never surface as an
              // unhandled rejection or affect submission state.
            });
          }
        } catch {
          // Swallowed — a synchronous callback exception must never affect
          // submission state.
        }
      })
      .catch((error: unknown) => {
        if (!isMountedRef.current || requestIdRef.current !== requestId) {
          return;
        }
        if (isAbortError(error)) {
          return;
        }
        if (error instanceof ApiRequestError) {
          setState({ status: "error", message: error.message, code: error.code });
          return;
        }
        setState({ status: "error", message: NETWORK_ERROR_MESSAGE });
      });
  }, []);

  return { state, submit };
}

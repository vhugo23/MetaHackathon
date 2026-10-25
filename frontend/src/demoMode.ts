/**
 * Public-demo read-only mode (conference deployment). `VITE_DEMO_READ_ONLY` is
 * a build-time, non-secret flag; only the exact value "true" (any case) turns
 * it on, so a missing or mistyped value leaves the normal controls in place.
 * It only hides mutation controls: the backend enforces the same restriction
 * itself (`META_RNE_DEMO_READ_ONLY`), so this never replaces the server guard.
 * Read on every call (not cached at import) so tests can stub the environment.
 */
export function isDemoReadOnly(): boolean {
  return (import.meta.env.VITE_DEMO_READ_ONLY ?? "").trim().toLowerCase() === "true";
}

export const DEMO_READ_ONLY_LABEL = "Read-only demo";

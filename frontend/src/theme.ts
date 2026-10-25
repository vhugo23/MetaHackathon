export type Theme = "light" | "dark";

const STORAGE_KEY = "meta-rne-theme";

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/**
 * The approved product identity is the dark infrastructure console, so a
 * first visit is dark regardless of the OS preference; only an explicit,
 * stored choice changes it.
 */
export const DEFAULT_THEME: Theme = "dark";

/** The user's explicitly persisted choice, or `null` if none was ever made. */
export function getStoredTheme(): Theme | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isTheme(stored) ? stored : null;
  } catch {
    // Storage disabled/unavailable — treat as "no stored preference" rather
    // than fail theme resolution entirely.
    return null;
  }
}

export function storeTheme(theme: Theme): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Ignore persistence failures (e.g. storage disabled/full) — the
    // in-memory theme selection still applies for the current session.
  }
}

/** A stored user choice wins; otherwise the dark default applies. */
export function resolveInitialTheme(): Theme {
  return getStoredTheme() ?? DEFAULT_THEME;
}

import { useEffect, useLayoutEffect, useState } from "react";
import { IncidentDashboard } from "./pages/IncidentDashboard";
import { NetworkWorkspace } from "./pages/NetworkWorkspace";
import { resolveInitialTheme, storeTheme, type Theme } from "./theme";

type View = "network" | "tools";

/**
 * Two views selected by the URL hash (no router dependency): the live
 * network workspace is the default; `#tools` is the existing incident /
 * configuration / telemetry dashboard, unchanged. Only the mounted view
 * runs its requests, so the tools view does not poll.
 */
function viewFromHash(): View {
  return window.location.hash === "#tools" ? "tools" : "network";
}

export function App() {
  const [view, setView] = useState<View>(viewFromHash);
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme());

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const onHashChange = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function toggleTheme() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    storeTheme(next);
  }

  const toggleLabel = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__identity">
          <p className="app-title">Meta RNE Platform</p>
          <p className="app-descriptor">
            Multi-vendor configuration policy and incident operations
          </p>
        </div>
        <button type="button" className="theme-toggle" onClick={toggleTheme}>
          {toggleLabel}
        </button>
      </header>
      <nav className="app-nav" aria-label="Primary">
        <a href="#network" aria-current={view === "network" ? "page" : undefined}>
          Live Network
        </a>
        <a href="#tools" aria-current={view === "tools" ? "page" : undefined}>
          Incidents &amp; tools
        </a>
      </nav>
      {view === "network" ? <NetworkWorkspace /> : <IncidentDashboard />}
    </div>
  );
}

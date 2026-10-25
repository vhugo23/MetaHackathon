import { useEffect, useLayoutEffect, useState } from "react";
import { HeaderStatusSlotContext } from "./components/headerStatusSlot";
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
  const [statusSlot, setStatusSlot] = useState<HTMLElement | null>(null);

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
    <div className={`app app--${view}`}>
      <header className="app-header">
        <div className="app-brand">
          <span className="app-brand__mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" focusable="false">
              <circle cx="12" cy="5" r="2.4" />
              <circle cx="5" cy="19" r="2.4" />
              <circle cx="19" cy="19" r="2.4" />
              <path d="M12 7.4 6.2 16.9M12 7.4l5.8 9.5M7.4 19h9.2" />
            </svg>
          </span>
          <div className="app-header__identity">
            <p className="app-title">NetGuardian</p>
            <p className="app-descriptor">Network Reliability &amp; RCA Platform</p>
          </div>
        </div>
        <nav className="app-nav" aria-label="Primary">
          <a href="#network" aria-current={view === "network" ? "page" : undefined}>
            Live Network
          </a>
          <a href="#tools" aria-current={view === "tools" ? "page" : undefined}>
            Incidents &amp; Tools
          </a>
        </nav>
        <div className="app-header__status" ref={setStatusSlot} />
        <button type="button" className="theme-toggle" onClick={toggleTheme}>
          {toggleLabel}
        </button>
      </header>
      <HeaderStatusSlotContext.Provider value={statusSlot}>
        {view === "network" ? <NetworkWorkspace /> : <IncidentDashboard />}
      </HeaderStatusSlotContext.Provider>
    </div>
  );
}

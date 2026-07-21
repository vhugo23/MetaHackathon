import { useLayoutEffect, useState } from "react";
import { IncidentDashboard } from "./pages/IncidentDashboard";
import { resolveInitialTheme, storeTheme, type Theme } from "./theme";

export function App() {
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme());

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

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
      <IncidentDashboard />
    </div>
  );
}

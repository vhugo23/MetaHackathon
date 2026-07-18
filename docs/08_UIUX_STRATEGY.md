# UI/UX Design Strategy — Maestro

Maestro is an **operator tool used under stress**, not a consumer product — the UX bar is "correct, fast to scan, hard to misread at 2am," not visual polish. Scoped lighter than the deep-build systems per the roadmap tiering, but still designed deliberately rather than left as Grafana defaults.

## 1. Persona-Driven Views

| Persona | Primary view | Design priority |
|---|---|---|
| Nadia (NOC/SRE) | Incident View dashboard (`12_MONITORING_OBSERVABILITY.md` §5) | Time-to-understanding: severity, affected components, and linked playbook visible without a click |
| Marcus (Network) | BGP + Fleet Health dashboards | Route-table and session-state changes over time, not just current-state snapshots |
| Priya (Security) | Audit/RBAC log view | Every remediation action's actor, approval chain, and break-glass usage, filterable and exportable |
| Recruiter (external) | Public read-only demo dashboard | Immediately legible without domain knowledge — labeled clearly, annotated with what's currently simulated/healthy vs. faulted |

## 2. Design Principles for Operator Dashboards

- **Status before detail:** color-coded severity at the top of every view, detail below the fold — an operator should know "is this bad" in under 2 seconds.
- **Colorblind-safe palettes:** red/green alone is not sufficient for status — pair color with icons/labels (a real accessibility requirement, not a nice-to-have).
- **No silent state:** every dashboard panel shows its own data freshness (last updated timestamp) — a stale-but-green panel is more dangerous than an obviously broken one, directly relevant given this project's whole premise is about monitoring that can silently fail.
- **Drill-down, not drill-required:** the top-level view must be actionable alone; clicking into detail is for investigation, not baseline understanding.
- **Mobile-legible:** on-call response often starts on a phone — the Incident View is tested at a narrow viewport, not just desktop.

## 3. What's Implemented vs. Documented

Grafana dashboards (Fleet Health, BGP, DNS, Incident View, OOB Status) are fully built per `12_MONITORING_OBSERVABILITY.md`. A dedicated custom frontend beyond Grafana/FastAPI's auto-docs is documented as a v2 idea (e.g., a React operator console) but not built — consistent with the project's scope tiering, and an honest answer if asked in an interview ("I prioritized the monitoring substance over a custom UI given the timeline").

## 4. Why This Matters for Each Role

PM: persona-driven design and prioritizing "what ships" vs. "what's documented" is core product thinking. SRE: dashboard design directly affects real incident response speed — this isn't a cosmetic concern. SWE: even without a custom frontend, API response shape design (`07_API_SPECIFICATION.md`) is itself a UX decision for whoever consumes the API next.

# Deployment Strategy — Maestro

## 1. Two Deployment Surfaces (don't conflate them)

This project has two distinct things that get "deployed," each with its own strategy — conflating them is a common design mistake worth explicitly avoiding:

1. **The NetSentinel platform itself** (the API services, Grafana, Prometheus, etc.) — deployed via CI/CD to the Oracle VM.
2. **Configuration pushed to the simulated fleet** — deployed via the Config System's canary/rollback mechanism (`01_PRD.md`, `05_NETWORK_ARCHITECTURE.md`).

## 2. Platform Deployment (surface 1)

- **Strategy:** rolling restart via Docker Compose (`docker compose up -d`, which recreates changed containers with minimal downtime) for Phases 1-7; migrates to a rolling-update Kubernetes Deployment in Phase 8.
- **Immutable, SHA-tagged images** (see `11_CICD_DESIGN.md`) — rollback is always "redeploy previous SHA," never a manual patch.
- **Smoke tests post-deploy:** each service's `/health` endpoint plus one real read endpoint, checked automatically before a deploy is marked complete; failure auto-triggers redeploy of the last known-good SHA.
- **Database migrations:** Alembic migrations run as a separate, explicit CI step before the new application version starts — never implicit, never skipped.

## 3. Fleet Configuration Deployment (surface 2)

- **Strategy:** canary-first staged rollout — push to one designated canary device, run automated health checks (BGP session state, interface status) for a defined soak period, then either proceed to full fleet push or automatically roll back.
- **Rollback trigger:** any canary health check failure within the soak window.
- **This is deliberately the more conservative of the two strategies** — it's the direct answer to the real incident's root cause, so it gets the most scrutiny.

## 4. Deployment Checklist (both surfaces)

- [ ] CI pipeline green (lint, tests, security scan)
- [ ] Schema validation passed (fleet config only)
- [ ] Canary/staged step completed successfully
- [ ] Smoke tests passed post-deploy
- [ ] Dashboards show no new anomalies for 5 minutes post-deploy
- [ ] Rollback path confirmed available before considering the deploy final

## 5. Rollback Design

Both surfaces treat rollback as a first-class, tested path, not an emergency improvisation: platform rollback is "redeploy previous image SHA," fleet rollback is "reapply the last known-good config version," both triggered automatically on health-check failure and available as a one-command manual override.

## 6. Why This Matters for Each Role

DevOps/Infra: canary + automatic rollback is the single most transferable pattern in this entire project — nearly every large infra org uses some version of it. SRE: deployment safety is inseparable from reliability engineering. Network: config rollback specifically maps to real network-change-management practice. PM: a deployment checklist is a lightweight but real process artifact, worth having ready to discuss.

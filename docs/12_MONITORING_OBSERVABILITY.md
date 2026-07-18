# Monitoring & Observability Strategy — Maestro

## 1. The Core Lesson This Doc Is Built Around

Meta's own postmortem for the October 4, 2021 outage noted that internal monitoring tools were themselves degraded during the incident, because they depended on the same network/DNS infrastructure that had failed. **A monitoring system that shares a failure domain with the thing it monitors will fail exactly when you need it most.** Every design decision below traces back to this one sentence.

## 2. Golden Signals (per component)

| Signal | What's tracked | Why |
|---|---|---|
| Latency | API p50/p95/p99, DNS resolution time, BGP convergence time | Distinguishes "slow" from "down" — the harder, more realistic detection problem |
| Traffic | Requests/sec per service, route announcements/sec | Baseline for anomaly detection |
| Errors | HTTP error rate, DNS SERVFAIL rate, BGP session flap count | Direct incident signal |
| Saturation | CPU/RAM per container, connection pool usage | Predicts capacity-driven incidents before they're outages |

## 3. Two Monitoring Paths (this is the architectural core of this doc)

| | Primary Path | Out-of-Band (OOB) Path |
|---|---|---|
| Network | Shares the app/fleet network | Separate Docker bridge network, no shared dependency |
| DNS dependency | Yes (resolves via internal DNS) | No — uses static IPs / direct container addressing |
| What it collects | Full metric set (rich, high-cardinality) | Minimal but critical: is each router/service host reachable, is each BGP session up, is DNS itself responding |
| Failure behavior | Expected to degrade when the network fails (this is disclosed and intentional, not a bug) | Must survive the exact scenario in `04_SYSTEM_ARCHITECTURE.md` §3 |
| Where it reports | Same Grafana instance, separate dashboard row | Same Grafana instance, separate dashboard row, plus a direct Alertmanager route that doesn't depend on the primary Prometheus |

Both paths feed the same Grafana instance for demo convenience, but Grafana itself is reachable via a network path independent of the fleet — this is validated explicitly in `14_TESTING_STRATEGY.md` via a "kill the fleet network, confirm dashboards and alerts still work" test.

## 4. SLIs, SLOs, and Error Budgets

| Service Level Indicator (SLI) | Service Level Objective (SLO) | Error budget |
|---|---|---|
| % of fault injections detected within 30s | 95% | 5% of injected faults may take longer, tracked monthly |
| % of alerts that are actionable (not noise) | 90% precision | 10% false-positive budget before detector retuning is triggered |
| Out-of-band path uptime during primary-path failure | 99.9% | Effectively zero-tolerance — this is the path's entire purpose |
| API availability (own platform, not simulated fleet) | 99% | ~7hrs/month |

Error budgets are tracked and, when exhausted, trigger a documented policy: pause new feature work in that area, prioritize reliability fixes — the real SRE practice, applied to a portfolio project.

## 5. Dashboards

1. **Fleet Health** — device status, interface counters, uptime.
2. **BGP** — session states, route-table size over time, flap history.
3. **DNS & Service Discovery** — query volume, SERVFAIL rate, registry sync lag.
4. **Incident View** — active alerts, correlated timeline (config push → BGP flap → DNS failure → service errors), overlaid on one timeline — this is the single dashboard built to make the cascading-failure story visible at a glance.
5. **Out-of-Band Status** — deliberately isolated, minimal, boring — its job is to stay green and truthful when everything else is red.

## 6. Alerting Design

- Alertmanager groups related alerts (one BGP flap shouldn't fire 10 downstream alerts) and routes by severity: `critical` → Slack + PagerDuty-style escalation (simulated), `warning` → Slack only, `info` → dashboard only.
- Every alert links to its relevant playbook in `16_INCIDENT_RESPONSE_PLAYBOOKS.md` directly in the notification.

## 7. Why This Matters for Each Role

SRE: this document *is* the observability half of the SRE discipline. Infra/DevOps: dashboard-as-code (Grafana JSON in version control) and Prometheus scrape-config design. Network: BGP/DNS-specific metrics are genuinely specialized knowledge. Security: the OOB path is also a security control (visibility survives an attack that takes down the primary path). PM: SLOs/error budgets are a directly reusable PM/SRE-hybrid skill.

# Product Requirements Document (PRD) — Maestro

**Status:** Draft v1 · **Owner:** Hugo · **Last updated:** 2026-07-17

## 1. Vision

Maestro is a self-hosted platform that simulates a small but real data-center network, then deliberately reproduces the causal chain behind the October 4, 2021 Meta outage — a config error cascading through BGP, into DNS, into total service-discovery failure — and builds the prevention, detection, mitigation, and recovery systems that would have shortened it. The product is both the platform itself and the documentation trail that proves the builder understands *why* each piece exists.

## 2. Problem Statement

Large-scale infrastructure failures are rarely caused by one dramatic event — they're caused by a small error (a bad config push) propagating through systems that were never designed to fail independently (BGP, DNS, monitoring, access). Engineers entering infrastructure, SRE, network, and security roles are rarely given hands-on exposure to this *causal chain* — they learn Docker, or they learn Kubernetes, or they learn "what is DNS," but rarely build a system where breaking one thing breaks the next thing on purpose, and then fix the chain end to end.

## 3. Personas

| Persona | Description | What they need from NetSentinel |
|---|---|---|
| **Nadia, NOC/SRE Engineer** | First responder when an alert fires. Needs to know *what* broke, *why*, and *what to do*, fast. | Clear dashboards, actionable alerts, runbooks, low MTTD |
| **Marcus, Network Engineer** | Owns the routing/DNS layer. Needs visibility into BGP session state and route churn. | Route-table visibility, BGP session dashboards, config diff/rollback tooling |
| **Priya, Security Engineer** | Cares about blast radius, access control, and whether the recovery path itself is trustworthy. | RBAC audit trail, break-glass access logging, threat model documentation |
| **Recruiter/Interviewer (external persona)** | Evaluates this project as a portfolio artifact in ~5 minutes. | A README that states the problem, the architecture diagram, a live demo link, and clear evidence of judgment (not just code) |

## 4. Goals

1. Reproduce, at small scale, the real causal chain: config error → BGP withdrawal → DNS failure → service discovery loss → cascading failure.
2. Build real prevention (validated config + rollback), detection (out-of-band monitoring), mitigation (automated recovery + circuit breakers), and recovery (playbooks + break-glass access) for that chain.
3. Produce a full, real documentation set (this folder) that mirrors what a real engineering org produces — not as busywork, but because writing them is how the underlying judgment gets tested.
4. Ship something live, on a $0 budget, that a recruiter can open in a browser.

## 5. Non-Goals (v1)

- Full production-scale multi-vendor device support (one router OS — FRRouting — is enough to prove the pattern).
- Full active-active multi-region disaster recovery (documented as a design exercise, not deep-built — see `10_INFRASTRUCTURE_ARCHITECTURE.md`).
- A polished end-user UI beyond operational dashboards (this is an operator tool, not a consumer product — see `08_UIUX_STRATEGY.md`).
- Enterprise-grade zero-trust (mTLS everywhere, SPIFFE/SPIRE) — documented, minimally implemented, flagged as Phase 8 stretch.

## 6. User Stories

**Prevention**
- As Marcus, I want config changes validated against a schema before they can be pushed, so a malformed config never reaches a device.
- As Marcus, I want a canary rollout with automatic rollback, so one bad config can't take down the whole fleet.

**Detection**
- As Nadia, I want a monitoring path that doesn't depend on the same DNS/network it watches, so I still have visibility during a total network failure.
- As Nadia, I want anomaly detection on routing and DNS metrics, so I'm alerted before a human would notice from raw graphs.

**Mitigation**
- As Nadia, I want known failure signatures to trigger automated remediation, so common failures resolve before I'm even paged.
- As a downstream service, I want to degrade gracefully (serve stale-cached data) rather than hard-fail when DNS is unreachable.

**Recovery**
- As Nadia, I want a documented playbook per failure type, so I know exactly what to do at 2am without reasoning from scratch.
- As Priya, I want a break-glass access path that doesn't depend on the primary network, so responders can reach the system even during a total outage.

**Portfolio/PM**
- As Hugo, I want every phase's scope decision documented and justified, so I can defend prioritization choices in an interview.

## 7. Success Metrics / KPIs

| Metric | Definition | Target |
|---|---|---|
| MTTD | Time from fault injection to first alert firing | < 30 seconds |
| MTTR | Time from alert to resolution (auto or human) | < 5 minutes for auto-remediated classes |
| Blast radius reduction | % of fleet unaffected due to canary/staged rollout catching a bad config | 100% (canary catches it before fleet-wide push) |
| Alert precision | % of fired alerts that reflect a real injected fault | > 90% |
| Documentation completeness | % of the 17 deliverables complete and internally consistent | 100% by end of Phase 7 |
| Portfolio readiness | Live demo URL + 3+ demo GIFs + README meeting the standard in `17_PORTFOLIO_DOCUMENTATION.md` | Done by end of Phase 7 |

## 8. Prioritization Framework

Scope decisions use **RICE** (Reach × Impact × Confidence ÷ Effort) for feature-level calls within a phase, and simple **MoSCoW** for phase-level scope cuts. The tier decision in the roadmap (deep build vs. documented vs. stretch, see `NetSentinel_Roadmap.md` §1) is the top-level MoSCoW application: Must-have = the causal chain end to end; Should-have = automated recovery depth; Could-have = load balancing/DR implementation; Won't-have (v1) = multi-region active-active, full zero-trust mesh.

## 9. Cadence

- **Weekly:** update `PROGRESS.md` (shipped / learned / next) — doubles as stakeholder communication practice.
- **Per phase:** re-confirm scope against RICE/MoSCoW before starting; update this PRD if scope shifted.
- **End of project:** one retrospective doc — what was cut, why, and what v2 would include.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Scope creep back toward "build everything deep" | Scope tiering in roadmap §1 is the enforcement mechanism — revisit before each phase |
| Free-tier cloud limits hit | Oracle Always-Free is permanent, not trial-based; documented fallback is Fly.io/Railway for smaller pieces |
| Solo-dev timeline slip | Phase 8 is explicitly stretch/optional — Phases 0-7 alone satisfy the PRD's goals |

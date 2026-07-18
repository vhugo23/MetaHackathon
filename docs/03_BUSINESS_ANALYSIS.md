# Business Analysis — Maestro

## 1. Why This Problem Matters (Industry Context)

Large-scale outages are expensive and increasingly public. The October 4, 2021 Meta outage is a useful case study specifically because it's unusually well documented: Meta published a public post-mortem explaining the BGP/DNS causal chain, and the market reaction was visible and measurable. Widely reported estimates at the time put Meta's stock decline that day at roughly 5%, and various outlets estimated tens of millions of dollars in lost advertising revenue for the ~6-hour window — figures that are directionally consistent across sources but vary depending on methodology, so they should be treated as illustrative rather than precise.

The broader pattern generalizes far beyond Meta: outages at Cloudflare, AWS, Google Cloud, and Fastly in recent years have repeatedly traced back to the same root-cause categories this project addresses — unvalidated configuration changes, BGP/routing misconfigurations, DNS as a single point of failure, and monitoring systems that share fate with the systems they observe. This is not a Meta-specific problem; it's a structural property of large distributed systems, which is exactly why reliability engineering (SRE) has become its own discipline and career track industry-wide.

## 2. Market/Industry Landscape

| Category | Representative companies | What they sell | What NetSentinel teaches about that market |
|---|---|---|---|
| Observability platforms | Datadog, Grafana Labs, New Relic, Honeycomb | Metrics/logs/traces as a paid service | Building Prometheus/Grafana yourself teaches what these platforms actually do under the hood — a strong "I understand what I'd be buying" signal |
| Incident management | PagerDuty, Opsgenie, incident.io | Alert routing, on-call scheduling, postmortem tooling | Alertmanager + your own playbooks teaches the same concepts these tools productize |
| Network automation | Cisco, Arista, Juniper (vendor tooling), Itential, NetBrain | Multi-vendor config automation | Netmiko/NAPALM + Jinja2 is the open-source version of what these enterprise tools charge for |
| Cloud/CDN reliability | Cloudflare, Fastly, AWS | Reliable delivery at global scale | Understanding BGP/DNS/anycast at small scale is foundational to understanding how these companies operate at large scale |

## 3. Cost-of-Failure Framing

A useful mental model to carry into interviews: every hour of downtime for a service with meaningful revenue-per-minute translates directly into a dollar figure, which is why reliability investment has a defensible ROI even though it looks like "nothing happening" when it works. This project makes that concrete by measuring your own MTTD/MTTR numbers (see `01_PRD.md` §7) — you can literally say "my system detects this failure class in under 30 seconds; the real 2021 incident took roughly 45 minutes just to begin external communication, and hours to fully resolve."

## 4. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Unvalidated config reaches production | High (this is the actual root cause being modeled) | Critical | Schema validation + canary + auto-rollback (Phase 1) |
| Monitoring fails alongside the network it watches | Medium | Critical | Out-of-band monitoring path (Phase 4) |
| Recovery tooling itself becomes unreachable | Medium | High | Break-glass access path (Phase 7) |
| Free-tier infra limits block the demo | Low | Medium | Oracle Always-Free is permanent and generously sized; Compose resource limits set per service |
| Solo timeline slips | Medium | Medium | Explicit MoSCoW scope tiering; Phase 8 is optional |

## 5. Stakeholder Analysis

| Stakeholder | Interest | How this project addresses it |
|---|---|---|
| Recruiters/hiring managers | Evidence of real engineering judgment, not just code volume | Full documentation set + live demo + explicit scope-decision rationale |
| Future team leads | Can this person reason about tradeoffs and communicate them? | PRD/TRD/prioritization artifacts are the evidence |
| You (career ROI) | Maximize learning breadth × depth per hour invested | Deep-build tiering (roadmap §1) is the direct mechanism |

## 6. Career ROI

Reliability/infrastructure/security skills are consistently among the highest-demand, highest-comp early-career specializations at large tech companies, precisely because the failure modes this project addresses (misconfiguration, routing failures, cascading dependency failures) are universal, not company-specific. A candidate who can speak concretely about *why* a canary rollout exists, *why* monitoring needs an independent failure domain, and can point to a working system that demonstrates both, is differentiated from candidates who list "Docker, Kubernetes, AWS" as keywords without a system that ties them together.

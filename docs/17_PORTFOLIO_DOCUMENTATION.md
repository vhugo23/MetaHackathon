# Portfolio Documentation — Maestro

## 1. Repo Structure

```
MetaHackathon/
├── README.md                 # entry point — see §2
├── Maestro_Roadmap.md
├── docs/                     # this folder — the full 17-deliverable set
├── fleet/                    # Containerlab topology + FRR configs
├── config-system/            # schema, Jinja2 templates, push automation
├── services/
│   ├── inventory-api/
│   ├── registry-dns-api/
│   └── ops-api/
├── monitoring/                # Prometheus/Grafana/Alertmanager configs
├── detection/                 # anomaly detection + fault injection tooling
├── recovery/                  # runbook engine + LLM RCA integration
├── infra/                     # Terraform + Docker Compose / Helm
└── .github/workflows/         # CI/CD
```

One monorepo is recommended over split repos at this scale — easier to navigate as a reviewer, and the `docs/` folder ties it together as one coherent story rather than fragments.

## 2. README Standard (root)

Every strong portfolio repo README answers, in order, in under 60 seconds of reading:
1. **What is this** (2-3 sentences, name the real incident it's inspired by)
2. **Architecture diagram** (the failure-chain sequence diagram from `04_SYSTEM_ARCHITECTURE.md` §3 is the strongest single image to lead with)
3. **Live demo link**
4. **How to run it locally** (`docker compose up`, 3-5 commands max)
5. **What I learned / would do differently** — this line, more than any code, is what differentiates a portfolio project from a class assignment

## 3. Demo Assets to Capture (per phase)

- Phase 1: GIF of a bad config being rejected/rolled back.
- Phase 2: terminal recording of a live BGP route withdrawal.
- Phase 3: GIF of service discovery failing when DNS is killed, then recovering.
- Phase 4: side-by-side dashboard GIF — primary monitoring going dark while OOB stays green.
- Phase 5: example LLM-generated RCA report (real output, not mocked).
- Phase 6: the actual postmortem document from your own game day.
- Phase 7: live URL + CI/CD green-badge screenshot.

## 4. Resume Bullets (write these now, build toward them)

- *"Designed and built a network resilience platform reproducing the causal chain behind a real large-scale infrastructure outage (BGP route withdrawal → DNS failure → service discovery loss), with sub-30s automated detection via an independently-failing-domain monitoring architecture."*
- *"Implemented a config-automation pipeline with schema validation, canary rollout, and automatic rollback, directly modeled on the root-cause gap identified in a real public postmortem."*
- *"Built an LLM-assisted root-cause-analysis copilot with structured-output guardrails, integrated into an incident response workflow with human-in-the-loop approval for high-risk automated remediation."*
- *"Authored a full technical documentation suite (PRD, TRD, security architecture, incident playbooks) for a self-directed infrastructure project, deployed at $0 cost on free-tier cloud infrastructure with full CI/CD."*

## 5. Presenting This Project (interview / demo day framing)

Lead with the real incident (30 seconds), then the one architecture diagram, then **one live demo of the failure cascade actually happening** (this is the moment that makes the project memorable — don't just show static dashboards, trigger a fault live if possible), then close with what was deliberately scoped out and why (`NetSentinel_Roadmap.md` §1) — this last part signals judgment, not just execution.

## 6. Target Audience Alignment

| Company type | What to emphasize |
|---|---|
| Meta, Google, hyperscalers | The BGP/DNS causal chain, out-of-band monitoring principle, gNMI/OpenConfig awareness (Phase 8) |
| Cloudflare, Fastly | Network/DNS depth, incident response playbooks |
| Datadog, Honeycomb | Monitoring/observability architecture, SLOs/error budgets |
| Cisco, Arista | Config automation, multi-vendor-ready schema design |
| Amazon, Microsoft (general infra/SDE) | Full-stack breadth: API design, CI/CD, IaC, security — the whole documentation suite |

## 7. Why This Matters for Each Role

PM: packaging and stakeholder-facing communication is a real PM deliverable, not an afterthought. All roles: the difference between "I built X" and "I can explain why X exists, what it cost me to scope it down, and what I'd do at v2" is the difference that gets remembered after a 30-minute interview.

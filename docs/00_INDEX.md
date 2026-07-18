# Maestro — Documentation Index & Role/Skill Matrix

This folder contains the full deliverable set for NetSentinel. Read `../NetSentinel_Roadmap.md` first for the phased build plan — these documents are the artifacts each phase produces.

## Document Map

| File | Deliverable |
|---|---|
| `01_PRD.md` | Product Requirements Document |
| `02_TRD.md` | Technical Requirements Document |
| `03_BUSINESS_ANALYSIS.md` | Business Analysis |
| `04_SYSTEM_ARCHITECTURE.md` | System Architecture Diagrams |
| `05_NETWORK_ARCHITECTURE.md` | Network Architecture Diagrams |
| `06_DATABASE_DESIGN.md` | Database Design |
| `07_API_SPECIFICATION.md` | API Specifications |
| `08_UIUX_STRATEGY.md` | UI/UX Design Strategy |
| `09_SECURITY_ARCHITECTURE.md` | Security Architecture |
| `10_INFRASTRUCTURE_ARCHITECTURE.md` | Infrastructure Architecture |
| `11_CICD_DESIGN.md` | CI/CD Design |
| `12_MONITORING_OBSERVABILITY.md` | Monitoring & Observability Strategy |
| `13_AI_INTEGRATION.md` | AI Integration Opportunities |
| `14_TESTING_STRATEGY.md` | Testing Strategy |
| `15_DEPLOYMENT_STRATEGY.md` | Deployment Strategy |
| `16_INCIDENT_RESPONSE_PLAYBOOKS.md` | Incident Response Playbooks |
| `17_PORTFOLIO_DOCUMENTATION.md` | Portfolio Documentation |

Roles referenced throughout: **SWE** (Software Engineer), **BE** (Backend Engineer), **DevOps**, **Infra** (Infrastructure Engineer), **Net** (Network Engineer), **SRE**, **Sec** (Security/Cybersecurity Engineer), **AI** (AI/ML Engineer), **PM** (Product Manager).

---

## Master Role/Skill Matrix

Every major component, mapped to: why it exists in real systems, which specific challenge from the October 4, 2021 Meta outage it addresses, the skills it builds, and who benefits most.

| Component | Why it exists in real systems | Outage challenge it addresses | Skills developed | Primary roles |
|---|---|---|---|---|
| **Config schema validation + staged rollout + auto-rollback** | The root trigger of the real incident was an unvalidated maintenance command pushed fleet-wide with no canary. Every large infra org now gates config changes this way. | Network misconfiguration | IaC principles, JSON Schema, Jinja2 templating, canary deployment, idempotent automation | Infra, DevOps, Net, SRE |
| **BGP simulation + self-withdrawal on health-check failure** | Real hyperscalers (Meta included) use BGP self-withdrawal as a safety mechanism — it's exactly what turned a backbone outage into a global DNS outage. Understanding it is understanding the actual incident. | BGP routing failures, cascading infrastructure failures | Routing protocols, route propagation, failure-mode design, `tc netem` fault injection | Net, Infra, SRE |
| **Self-hosted DNS + service discovery via DNS** | Meta's internal services located each other via DNS; when DNS died, so did service discovery, badge systems, and the tools needed to fix the outage. This is the single most important causal link in the incident. | DNS service disruption, loss of service discovery | DNS internals, service discovery patterns, distributed systems dependency chains | Net, BE, SWE, SRE |
| **Out-of-band monitoring (independent failure domain)** | Meta's own monitoring degraded because it shared infrastructure with what it monitored. This is the single most cited lesson from the public postmortem. | Monitoring blind spots | Observability architecture, failure-domain isolation, redundant telemetry paths | SRE, Infra, DevOps |
| **Anomaly detection (statistical + ML)** | Manual threshold-watching doesn't scale past a handful of devices; every large monitoring org layers statistical/ML detection on top of raw metrics. | Incident detection and response | Time-series analysis, scikit-learn (Isolation Forest), explainable-first ML design | AI, SRE, BE |
| **Automated recovery / runbook engine** | "Turn it off and on again" is genuinely how most real incidents resolve — automating the safe subset is standard SRE practice. | Automated recovery mechanisms | Event-driven automation, webhook design, safe-action tiering (auto vs. human-gated) | SRE, DevOps, BE |
| **Circuit breakers + DNS fallback caching** | Services that hard-fail the instant a dependency is unreachable amplify outages; resilient services degrade gracefully. | Cascading infrastructure failures | Resilience patterns, distributed systems design, graceful degradation | BE, SWE, SRE |
| **LLM-powered RCA copilot** | Manually reading logs during an incident is slow; LLM-assisted triage is an emerging pattern at companies like Datadog, Honeycomb, and internally at most hyperscalers. | Incident detection and response | LLM API integration, prompt design for structured analysis, human-in-the-loop AI system design | AI, SRE, PM |
| **Incident response playbooks + blameless postmortems** | The real incident's duration was extended by unclear escalation paths and physical access issues; documented playbooks and practiced response reduce MTTR directly. | Incident detection and response, operational visibility | Incident command, severity classification, postmortem writing | SRE, PM, all roles |
| **CI/CD pipeline with security scanning** | Nearly universal in industry; config/code changes without automated gates is how the real incident's root-cause command shipped in the first place. | Network misconfiguration, operational visibility | GitHub Actions, container/dependency scanning, deployment gating | DevOps, Infra, Sec |
| **Zero-trust-informed RBAC + break-glass access** | The real incident's recovery was slowed because normal access paths were down; a documented out-of-band access method is now standard DR practice. | Cascading infrastructure failures, monitoring blind spots | Access control design, least-privilege, threat modeling (STRIDE) | Sec, Infra, SRE |
| **Cloud deployment (free-tier VM + k3s)** | Real-world deployment target — every skill here transfers directly to AWS/GCP/Azure equivalents. | Infrastructure resilience and redundancy | Cloud fundamentals, container orchestration, networking/firewalls | Infra, DevOps, Net |
| **Service registry + relational data model** | Every distributed system needs an authoritative source of truth for "what exists and where" — this is literally what broke in the real incident. | Loss of service discovery | Relational modeling, ORM/migrations, distributed state management | BE, SWE |
| **FastAPI microservice APIs** | REST/API design is the most universally tested skill in new-grad interviews; splitting inventory/metrics/remediation into logical services teaches real service boundaries. | Operational visibility | API design, microservices architecture, async Python | SWE, BE |
| **Load balancing (documented + minimal impl.)** | Standard redundancy mechanism; explicitly scoped light per the Phase-cut decision but still designed and partially built. | Infrastructure resilience and redundancy | L4/L7 load balancing concepts, health-check design | Net, Infra |
| **Disaster recovery / multi-region design (documented)** | Full active-active DR is enterprise-scale; documented here as a design exercise rather than deep build, per timeline scope. | Infrastructure resilience and redundancy | DR planning, RTO/RPO definition, failover strategy | SRE, Infra, PM |
| **Capacity planning (documented)** | Meta's own incident began as a capacity-assessment task; understanding *why* that process exists is part of understanding the incident. | Network misconfigurations, cascading infrastructure failures | Capacity modeling, demand forecasting | SRE, Infra, PM |
| **PRD/TRD/prioritization practice** | Every phase is scoped, justified, and prioritized the way a real team would — this is the PM layer that makes the technical work defensible. | (cross-cutting) | PRD writing, RICE/MoSCoW, KPI definition, roadmapping, stakeholder comms | PM, all roles |

---

## How to Use This Matrix in an Interview

For any question like *"tell me about a project where you [debugged a production issue / designed a resilient system / worked with networking]"* — find the row, and you have: the real-world justification, the specific technical skill, and the specific outage lesson it traces back to, already articulated.

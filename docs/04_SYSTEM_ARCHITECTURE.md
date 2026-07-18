# System Architecture — Maestro

## 1. Component Overview

```mermaid
graph TB
    subgraph FLEET["Simulated Fleet (Containerlab + FRRouting)"]
        R1[Router 1]
        R2[Router 2]
        R3[Router 3]
        SRV1[Service A]
        SRV2[Service B]
        DNS1[DNS Server - CoreDNS]
    end

    subgraph PREVENT["Prevent: Config System"]
        SCHEMA[Schema Validator]
        TEMPL[Jinja2 Templates]
        PUSH[Config Push - Netmiko]
        SCHEMA --> TEMPL --> PUSH --> R1 & R2 & R3
    end

    subgraph DETECT["Detect: Monitoring (primary + out-of-band)"]
        PROM[(Prometheus - primary path)]
        OOB[Out-of-Band Collector<br/>independent network path]
        ANOM[Anomaly Detector]
        GRAF[Grafana]
        R1 & R2 & R3 & SRV1 & SRV2 & DNS1 -->|primary path| PROM
        R1 & R2 & R3 --> OOB
        PROM --> ANOM --> GRAF
        OOB --> GRAF
    end

    subgraph MITIGATE["Mitigate: Resilience"]
        CB[Circuit Breakers<br/>in SRV1/SRV2]
        RUNBOOK[Runbook Engine]
        ANOM --> RUNBOOK
        RUNBOOK -->|auto-fix| PUSH
        SRV1 -.-> CB
        SRV2 -.-> CB
    end

    subgraph RECOVER["Recover: Response"]
        ALERT[Alertmanager]
        LLM[LLM RCA - Claude API]
        HUMAN[On-call - Slack]
        BREAKGLASS[Break-glass Access]
        ANOM --> ALERT --> LLM --> HUMAN
        HUMAN -.uses.-> BREAKGLASS
    end

    subgraph CORE["Core Services (FastAPI + Postgres)"]
        API[Backend API]
        PG[(PostgreSQL:<br/>inventory, registry, alerts)]
        API <--> PG
        SRV1 & SRV2 -->|service discovery| DNS1
        DNS1 -->|resolves via registry| API
    end
```

## 2. Component Responsibilities

| Component | Responsibility | Failure mode it must survive |
|---|---|---|
| Fleet (Containerlab/FRR) | Simulated network substrate | N/A — this is the thing that fails |
| Config System | Validate + safely push device config | Must catch bad config before fleet-wide push |
| Primary Prometheus | Standard metrics collection | Expected to degrade/fail when network fails — this is intentional |
| Out-of-Band Collector | Independent telemetry path | Must stay up when primary path is down |
| Anomaly Detector | Statistical/ML detection on top of metrics | Must not depend on primary path alone |
| Runbook Engine | Automated remediation for known signatures | Must be conservative — no action outside its known-safe set |
| Circuit Breakers (in services) | Graceful degradation when a dependency is unreachable | Must not cascade a DNS failure into a full service crash |
| Alertmanager + LLM RCA | Routing + explaining alerts | Must reach on-call via a path independent of the failure |
| Break-glass Access | Human access path during total network failure | Must not depend on the primary network at all |
| Backend API + Postgres | Source of truth for inventory/registry/alerts | Standard HA/backup practices (see `10_INFRASTRUCTURE_ARCHITECTURE.md`) |

## 3. Failure-Chain Walkthrough (the scenario this system is built to survive)

```mermaid
sequenceDiagram
    participant Op as Operator (bad config)
    participant Cfg as Config System
    participant R as Routers
    participant DNS as DNS Server
    participant Svc as Services
    participant Mon as Monitoring (primary)
    participant OOB as Out-of-Band Monitoring
    participant Human as On-call

    Op->>Cfg: Push config change
    Cfg->>Cfg: Schema validation PASSES (simulating the real gap)
    Cfg->>R: Push to canary device
    Cfg->>R: Health check FAILS
    Cfg->>Cfg: Automatic rollback triggered
    Note over Cfg,R: Prevention layer stops it here in the happy path.<br/>For the demo, force a bypass to show the full cascade:
    R->>R: Backbone link down (forced fault injection)
    R->>DNS: BGP session lost, DNS's own route withdrawn (self-withdrawal safety mechanism)
    DNS-->>Svc: DNS becomes unreachable
    Svc->>Svc: Service discovery fails, circuit breakers trip, serve stale cache
    Mon--xMon: Primary monitoring path also loses visibility (shares fate)
    OOB->>Human: Out-of-band path still reports state, alert fires
    Human->>Human: Follows playbook (docs/16), uses break-glass access
    Human->>R: Manually/automatically re-announces route once healthy
    DNS->>Svc: Resolution restored
```

This diagram is the single most important artifact in the whole project — it's the thing to walk through in an interview or presentation.

## 4. Design Principles

1. **No component's monitoring shares a failure domain with the component itself** (the core lesson of the real incident).
2. **Prevention is cheaper than detection; detection is cheaper than recovery** — effort is weighted accordingly (see roadmap tiering).
3. **Automate the boring 80%, gate the risky 20% behind a human** (runbook engine tiering).
4. **Every action is logged with actor and timestamp** — auto or human, for postmortem accuracy.

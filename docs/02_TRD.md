# Technical Requirements Document (TRD) — Maestro

**Status:** Draft v1 · Companion to `01_PRD.md`

## 1. Functional Requirements

| ID | Requirement | Phase |
|---|---|---|
| FR-1 | System shall validate device configs against a JSON Schema before allowing push | 1 |
| FR-2 | System shall support staged/canary config rollout with automatic rollback on health-check failure | 1 |
| FR-3 | System shall run real BGP sessions (eBGP) between simulated routers | 2 |
| FR-4 | System shall support programmatic BGP route withdrawal triggered by a failed health check | 2 |
| FR-5 | System shall resolve internal service locations via self-hosted DNS, not hardcoded IPs | 3 |
| FR-6 | System shall maintain a service registry as the source of truth for service discovery | 3 |
| FR-7 | System shall expose an out-of-band monitoring path independent of the primary network/DNS path | 4 |
| FR-8 | System shall detect anomalies in routing/DNS/host metrics via statistical and ML methods | 4 |
| FR-9 | System shall auto-remediate a defined set of known failure signatures | 5 |
| FR-10 | System shall implement circuit-breaker and stale-cache-fallback behavior in service-to-service calls | 5 |
| FR-11 | System shall generate an LLM-assisted RCA summary on alert firing | 5 |
| FR-12 | System shall provide a documented, working break-glass access path independent of the primary network | 7 |
| FR-13 | System shall log all automated and human remediation actions with timestamp and actor | 5, 9 |

## 2. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Availability (of NetSentinel's own monitoring/API, not the simulated fleet) | ≥ 99% during active demo/portfolio review periods |
| Performance | Alert fire within 30s of fault injection (MTTD target); API p95 latency < 300ms |
| Scalability | Architecture must support adding a second router vendor/OS without redesigning the config pipeline (even if not implemented) |
| Security | No secrets in source control; least-privilege API tokens; all inter-service auth via API keys or mTLS (stretch) |
| Observability | Every service emits Prometheus-compatible metrics and structured logs |
| Cost | $0 recurring infrastructure cost (free-tier only) |
| Maintainability | Every infra change goes through IaC + CI, never manual server edits |

## 3. Constraints

- Solo developer, 1-2 months intensive timeline.
- $0 budget — free-tier cloud only (see `10_INFRASTRUCTURE_ARCHITECTURE.md`).
- Simulated hardware only (Containerlab/FRRouting containers) — no access to real DC hardware, which is expected and disclosed, not hidden.
- Python-first stack, given target skill level (comfortable with code, new to infra).

## 4. Technology Decisions & Rationale

| Decision | Chosen | Rejected alternative(s) | Rationale |
|---|---|---|---|
| Network simulation | Containerlab + FRRouting | GNS3, EVE-NG, hardcoded fake metrics | Free, container-native (fits Docker-first learning path), runs real routing protocols — genuine telemetry instead of synthetic data |
| Backend framework | FastAPI | Flask, Django | Async-native, auto-generated OpenAPI docs, current industry standard for Python microservices |
| Database | PostgreSQL | MongoDB, SQLite | Relational modeling is the more broadly tested interview skill; real FK constraints matter for a service-registry data model |
| Metrics | Prometheus + Grafana | Datadog (paid), CloudWatch (AWS lock-in) | Free, CNCF-graduated, the de facto industry standard for self-hosted observability |
| DNS | CoreDNS | BIND9 | Container-native, Go-based (same ecosystem as Kubernetes), simpler plugin model for a learning project |
| Orchestration | Docker Compose → k3s | Full Kubernetes (k8s), Nomad | k3s gives the real Kubernetes API surface at a fraction of the resource footprint — runs on a free-tier VM |
| Cloud host | Oracle Cloud Always-Free | AWS/GCP free tier (12-month expiring) | Only major provider with a genuinely permanent free compute tier at meaningful size (4 OCPU/24GB ARM) |
| CI/CD | GitHub Actions | Jenkins, GitLab CI | Free for public repos, zero infra to maintain, universal in industry |
| AI/RCA | Claude API | Self-hosted LLM | No GPU budget; API-based integration is itself the more common industry pattern for this use case |

## 5. Assumptions

- "Devices" are containers, not physical hardware — disclosed explicitly in all documentation, not represented as real hardware.
- Traffic/load volumes are illustrative, not at hyperscaler scale — the project demonstrates *patterns*, not scale.
- Free-tier cloud resource limits (CPU/RAM on the Oracle VM) are sufficient for the full stack running simultaneously; validated in Phase 7, with Docker Compose resource limits enforced per service.

## 6. Dependencies

External: Docker, Containerlab, FRRouting images, Prometheus/Grafana/Alertmanager, PostgreSQL, CoreDNS, GitHub Actions, Oracle Cloud account, Cloudflare account, Anthropic API key.

Internal (build order): Fleet (P1) → Config system (P1) → BGP (P2) → DNS (P3) → Monitoring (P4) → Recovery (P5) → Incident response (P6) → Deploy (P7).

## 7. Out of Scope (v1)

Full zero-trust mesh (mTLS everywhere via SPIFFE/SPIRE), multi-region active-active DR, production-scale load testing, multi-vendor device support beyond FRRouting, mobile/consumer-facing UI.

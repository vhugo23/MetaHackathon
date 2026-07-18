# AI Integration Opportunities — Maestro

AI is used where it demonstrably beats simpler alternatives, not everywhere it could theoretically be applied — the discipline of saying "no" to AI is itself part of what this document demonstrates.

## 1. Anomaly Detection (statistical-first, ML-second)

- **Baseline:** rolling z-score / EWMA on key metrics (interface error rate, DNS query latency, BGP session flap frequency). Simple, explainable, fast — and genuinely what most production monitoring systems run first, before reaching for ML.
- **Layered ML:** Isolation Forest (scikit-learn) trained on labeled normal-vs-faulted windows generated from your own fault-injection tooling, as a second detector for multivariate anomalies the univariate baselines miss.
- **Why explainable-first matters:** a detector that can't say *why* it fired is unusable during an incident — this is a deliberate, defensible design choice, not a limitation to apologize for.

## 2. Predictive Outage Detection

Lightweight trend analysis (linear regression / simple exponential smoothing on saturation metrics) to flag "this device is trending toward a capacity-driven failure" before it crosses a hard threshold — directly related to the capacity-planning theme in `10_INFRASTRUCTURE_ARCHITECTURE.md` §6. Deliberately not a deep-learning forecasting model: at this data volume, a heavier model would overfit and add complexity without added accuracy — a judgment call worth stating explicitly in a presentation.

## 3. LLM-Powered RCA Copilot

**Flow:** alert fires → collector gathers the relevant time-windowed logs/metrics/BGP-state/config-diff → structured prompt sent to Claude API → structured response returned (not free text) with fields: `likely_cause`, `supporting_evidence`, `confidence`, `suggested_next_step`, `related_playbook`.

**Example prompt shape (abbreviated):**
```
You are assisting an SRE during an active incident. Given the following
time-windowed evidence, identify the most likely root cause.

Evidence:
- BGP session r1-leaf-a <-> spine-1 went from Established to Idle at 14:32:07
- Config push c1a2... applied to r1-leaf-a at 14:31:55 (26s before session drop)
- DNS SERVFAIL rate on dns-a rose from 0% to 94% at 14:32:41

Respond in this exact JSON schema: {likely_cause, supporting_evidence[],
confidence (low/med/high), suggested_next_step, related_playbook}
```

**Guardrails (important, and worth presenting explicitly):**
- The model never executes anything directly — its output is a *recommendation* surfaced to a human or to the runbook engine's evaluation step, never a direct trigger for a routing-affecting action.
- Every RCA response includes the raw evidence alongside the summary, so a human can verify rather than blindly trust — mitigates the real risk of a confidently-wrong LLM summary during a high-stress incident.
- Confidence field is used to route: `low confidence` → always escalate to human, never auto-remediate off of it alone.

## 4. Intelligent Alerting

LLM-assisted alert *grouping/deduplication* as a stretch enhancement on top of Alertmanager's rule-based grouping: given a burst of related alerts, classify whether they're one incident or several, reducing on-call noise. Explicitly lower priority than the RCA copilot — rule-based grouping (Alertmanager) already handles the common case well, so this is where "don't over-apply AI" is demonstrated.

## 5. Where AI Is Deliberately Not Used

- **Config generation:** Jinja2 templates, not LLM-generated configs — config correctness needs to be deterministic and schema-validated, not probabilistic. Worth stating explicitly: this is a considered exclusion, not an oversight.
- **Auto-remediation decision-making:** the runbook engine is rule-based (known signature → known safe action), not an LLM deciding what to do — auto-executed actions need to be predictable and auditable.

## 6. Why This Matters for Each Role

AI/ML: end-to-end applied ML, from feature/label design (using your own fault injector to generate training data) through explainability tradeoffs. SRE: judgment about where automation should and shouldn't make decisions. PM: "where does AI actually add value vs. hype" is an increasingly core PM evaluation skill. Security: LLM guardrails (never direct-execute, always show evidence) is applied AI-security thinking.

# Incident Response Playbooks — Maestro
## 1. Severity Matrix

| Severity | Definition | Example | Response |
|---|---|---|---|
| SEV-1 | Full loss of a critical function (DNS, service discovery) fleet-wide | DNS unreachable across all leaves | Immediate page, break-glass access authorized, all-hands until resolved |
| SEV-2 | Degraded but partially functional | One leaf's BGP session flapping, others healthy | Page on-call, standard response, no break-glass needed |
| SEV-3 | Isolated, non-cascading issue | Single device high CPU, no user-facing impact | Ticket + async investigation |
| SEV-4 | Informational / self-resolved | Transient latency blip, auto-recovered | Logged, reviewed in weekly ops review only |

## 2. Playbook A — BGP Route Withdrawal Cascade

**Trigger:** Alert `bgp_session_state_change` with `state=idle` on ≥2 sessions within 60s, correlated with a preceding config push.

1. **Confirm scope** — check the BGP dashboard: is this isolated to one device or spreading? (spreading = likely SEV-1)
2. **Check for a recent config push** — `GET /devices/{id}/configs` for the last 30 minutes. If a push immediately precedes the withdrawal, this is very likely the root cause — do not treat as coincidental.
3. **Verify automatic rollback fired** — if the Config System's canary/rollback should have caught this, check why it didn't (rollback bug vs. push bypassed canary — both are P0 findings for the postmortem).
4. **If rollback didn't auto-trigger:** manually invoke `POST /devices/{id}/configs/{config_id}/rollback` — requires `admin` role.
5. **Confirm recovery:** BGP session returns to `Established`, route reappears in routing table.
6. **Check downstream impact:** did this cascade into DNS unreachability (Playbook B)? If yes, treat as one SEV-1 incident, not two.

## 3. Playbook B — DNS / Service Discovery Failure

**Trigger:** Alert `dns_servfail_rate_high` or `service_registry_sync_lag_high`.

1. **Check the primary vs. out-of-band monitoring paths** — if OOB shows the network is otherwise healthy but DNS specifically is down, this is a DNS-layer issue, not a full network failure (narrows scope quickly).
2. **Check whether this traces back to a BGP withdrawal** (Playbook A) — if the DNS host itself self-withdrew its route, this is the exact real-incident pattern; go to Playbook A first, DNS should self-resolve once the route is restored.
3. **If DNS is down independent of routing:** check CoreDNS container health directly via the OOB path (`docker exec` over the break-glass SSH access if the primary path is also degraded).
4. **Confirm services degrade gracefully:** check that circuit breakers have tripped and services are serving stale-cached data rather than hard-failing — if not, this is itself a bug worth a postmortem action item.
5. **Recovery:** restart/reconnect CoreDNS, confirm `dns_servfail_rate` returns to baseline, confirm services resume live (non-cached) resolution.

## 4. Playbook C — Monitoring Blind Spot (primary path down)

**Trigger:** Primary Prometheus stops reporting, OR an operator suspects an incident but the primary dashboards show nothing wrong (the exact failure mode this whole project is designed around).

1. **Immediately switch to the Out-of-Band Status dashboard** — this is the designed answer to this exact scenario; if OOB is also silent, this is the most severe possible finding (both failure domains compromised) and warrants an immediate SEV-1 declaration regardless of confirmed user impact.
2. **Use break-glass access** if the primary access path is unreachable — its use is automatically logged (see `09_SECURITY_ARCHITECTURE.md` §5); this is expected and correct, not a violation.
3. **Diagnose via OOB-collected data only** until the primary path is confirmed safe to trust again.
4. **Postmortem must include:** why did the primary path fail silently instead of alerting on its own failure? (a monitoring system failing to alert on its own failure is a distinct, specifically trackable bug class.)

## 5. Blameless Postmortem Template

```markdown
# Postmortem: [Incident Title]

**Date:** | **Severity:** | **Duration:** | **Author:**

## Summary
One paragraph, plain language.

## Timeline
| Time | Event |
|---|---|

## Root Cause
What actually happened, traced to its origin (not just the proximate symptom).

## Impact
What was affected, for how long, measured against the SLOs in docs/12.

## What Went Well
## What Went Poorly
## Action Items
| Action | Owner | Due date |
|---|---|---|

## Lessons for the Broader System
(e.g., "this is the second time a config bypassed canary — the canary logic itself needs review")
```

Blameless means: the postmortem investigates the *system* that allowed the failure, never assigns fault to an individual — this is standard, real SRE culture (formalized publicly by Google's SRE book and adopted industry-wide) and is applied here even though you're a team of one.

## 6. Why This Matters for Each Role

SRE: incident command, severity classification, and blameless postmortems are core, differentiated SRE skills rarely taught before real job experience. PM: postmortems are a stakeholder-communication artifact — practice writing them clearly for a non-technical audience too. Security: break-glass usage logging ties directly into the security architecture. All roles: every new-grad technical interview eventually asks "tell me about a time something broke" — having a real, well-documented incident (even a self-inflicted one from a game day) is a strong answer.

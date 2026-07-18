# Security Architecture — Maestro

## 1. Threat Model (STRIDE)

| Component | Threat | STRIDE category | Mitigation |
|---|---|---|---|
| Config push pipeline | Malicious/malformed config pushed to fleet | Tampering | Schema validation, canary rollout, signed commits, CI gate |
| Ops API | Unauthorized remediation action (e.g., someone triggers a routing change without approval) | Elevation of Privilege | RBAC (below), admin-only gate on routing-affecting actions, full audit log |
| Service registry | Rogue service registers itself, poisoning discovery | Spoofing | Service-account API keys, registration scoped per-owner-team |
| DNS | Cache poisoning / unauthorized zone edits | Tampering, Spoofing | Registry is sole source of truth; CoreDNS reads only, never manually edited |
| Metrics/monitoring path | Attacker suppresses alerts to hide an ongoing incident | Repudiation | Out-of-band path (independent of primary), all alert state changes logged immutably |
| Break-glass access | Break-glass credential misuse | Elevation of Privilege | Short-lived, single-use tokens; every use triggers an automatic audit alert (see below) |
| Secrets (API keys, DB creds) | Leakage via source control or logs | Information Disclosure | No secrets in repo (pre-commit secret scanning), environment-injected via CI secrets store |
| Public-facing dashboard/API | DoS, unauthorized read of internal topology | Denial of Service, Info Disclosure | Rate limiting, Cloudflare in front (also gets free DDoS mitigation), read-only public views only |

## 2. Zero-Trust Principles Applied

Full zero-trust (mTLS everywhere via SPIFFE/SPIRE) is scoped as Phase 8 stretch (see roadmap §1) given the timeline, but the *principles* are applied now, not deferred:

- **Verify explicitly:** every API call carries a scoped credential; nothing is trusted by network location alone (a device on the "internal" Docker network still needs a valid API key).
- **Least privilege:** three-tier RBAC (`viewer` / `operator` / `admin`) with routing-affecting actions requiring `admin`, enforced per-endpoint (see `07_API_SPECIFICATION.md`).
- **Assume breach:** the out-of-band monitoring path and break-glass access exist under the assumption that the primary path *will* fail or be compromised — design for graceful continued operation, not just prevention.

## 3. RBAC Matrix

| Role | Read metrics/alerts | Push config (canary) | Push config (fleet-wide) | Approve routing-affecting remediation | Manage users |
|---|---|---|---|---|---|
| viewer | ✅ | ❌ | ❌ | ❌ | ❌ |
| operator | ✅ | ✅ | ❌ (requires admin co-sign) | ❌ | ❌ |
| admin | ✅ | ✅ | ✅ | ✅ | ✅ |

## 4. Secrets Management

- No secrets committed to source control — enforced via a pre-commit hook (`detect-secrets` or `gitleaks`) plus GitHub secret scanning on the repo.
- CI/CD secrets (cloud credentials, Anthropic API key, DB password) live in GitHub Actions Secrets, injected as environment variables at deploy time only.
- On the deployed VM, secrets are provided via a `.env` file with `600` permissions, excluded via `.gitignore`, and rotated manually on a documented schedule (Phase 8 stretch: migrate to a proper secrets manager like Vault or Oracle's own vault service).

## 5. Break-Glass Access Design

Directly answers the real incident's "engineers couldn't get in" problem:

- A documented, separate SSH path into the Oracle VM via the OOB-equivalent network path (a secondary security-group rule scoped to a specific known IP/key, not part of the main app's ingress).
- Break-glass credential use is **always logged and always triggers an alert** — the tradeoff of having emergency access is that its use is never silent.
- Documented in `16_INCIDENT_RESPONSE_PLAYBOOKS.md` as the explicit first step when the primary access path is suspected down.

## 6. Security Monitoring

- Failed auth attempts on any API tracked and rate-limited (basic brute-force protection).
- Config push attempts that fail schema validation are logged and, above a threshold, trigger a security-review alert (repeated failures could indicate probing, not just mistakes).
- Container images scanned with Trivy in CI; dependencies scanned with `pip-audit`/Dependabot — both block merge on critical findings (see `11_CICD_DESIGN.md`).

## 7. What's Explicitly Deferred (and why that's a defensible decision)

Full mTLS service mesh, SPIFFE/SPIRE workload identity, and a real secrets manager (Vault) are documented as the "what we'd do at v2/enterprise scale" answer, not silently omitted — see roadmap Phase 8. This is itself a security-engineering skill: knowing what to prioritize under real constraints, and being able to name the gap rather than hide it.

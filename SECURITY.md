# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x | ✅ Active support |
| 1.x | ❌ End of life |

## Reporting a Vulnerability

**DO NOT OPEN A PUBLIC ISSUE.** RMI is a commercial security platform — vulnerabilities directly affect customer safety.

| Contact | Details |
|---------|---------|
| **Email:** | security@rugmunch.io |
| **PGP Key:** | Available on request |
| **Response time:** | Within 24 hours |
| **Disclosure:** | Coordinated after fix deployment (max 90 days) |

### What to Include

- Type of vulnerability (RCE, auth bypass, data exposure, etc.)
- Affected endpoint/component
- Steps to reproduce
- Proof of concept (if available)
- Impact assessment

### What You'll Receive

- Confirmation within 24 hours
- Regular status updates
- Credit in release notes (or anonymity on request)
- Bug bounty at our discretion

---

## Security Model

### Trust Boundary

```
[Internet] ← Cloudflare WAF → [nginx Gateway] ← mTLS → [rmi-backend]
    │                             │                            │
    │ TLS 1.3                    │ Rate limit                 │ JWT verify
    │                             │ CSP/HSTS                   │ API key check
    │                             │ Request validation          │ x402 proof
    ▼                             ▼                            ▼
[Attacker] ──── blocked ────▶  [Gateway] ──── verified ────▶ [App]
```

### Authentication Methods

| Method | Use Case | Security Level |
|--------|----------|---------------|
| **JWT Bearer** (RS256) | User-facing API, Web UI | High — RSA-signed, short-lived (7d) |
| **API Key** (`X-API-Key`) | Service-to-service | Medium — static, rotate 90d |
| **x402 Proof** (`X-Payment-Proof`) | Pay-per-use endpoints | High — on-chain verifiable |
| **Web3 Wallet** (EIP-712/SIP) | Wallet auth | High — signature-based |
| **Cookie Session** | Web UI | High — HttpOnly + SameSite=Strict + CSRF |

### Authorization Model

| Tier | Rate Limit | Features | Auth Method |
|------|-----------|----------|-------------|
| Anonymous | 10 req/min | Public endpoints only | None |
| Free | 10 req/min, 100/day | Basic scan, market data | API key or JWT |
| Pro | 60 req/min, 10K/day | Deep scan, AI, alerts | JWT |
| Enterprise | 300 req/min, unlimited | All features, SLA, white-label | Custom |
| x402 | Pay-per-call | Premium AI, reports, forensic | x402 proof |

---

## Security Stack

| Layer | Tool | Scope |
|-------|------|-------|
| **Pre-commit** | gitleaks, bandit, ruff | Secrets, SAST, lint |
| **Pre-push** | mypy, deptry, lefthook | Types, deps, gates |
| **CI/CD** | Trivy, CodeQL, SonarQube | Container, code, quality |
| **Dependencies** | Renovate, dependabot | Weekly CVE scanning |
| **Network** | Cloudflare WAF, nginx rate limit | DDoS, brute force |
| **Runtime** | read-only rootfs, cap_drop ALL, no-new-privileges | Container escape prevention |
| **Monitoring** | Prometheus, Grafana, PagerDuty | Anomaly detection |

---

## Incident Response Plan

### Classification

| Severity | Definition | Response Time | Notify |
|----------|-----------|---------------|--------|
| **CRITICAL** | Active breach, data exfiltration, service-wide outage | <15 min | Security Lead + DevOps SRE |
| **HIGH** | Unauthorized access, credential compromise, DoS | <1 hour | Security Lead + DevOps SRE |
| **MEDIUM** | Suspicious activity, probe detected, isolated exploit | <4 hours | Security Lead |
| **LOW** | Policy violation, failed login spike | <24 hours | DevOps SRE |

### Response Phases

#### Phase 1: Detection & Triage (0-15 min)
1. Confirm incident via Grafana, nginx access logs, auth logs
2. Classify severity
3. Declare incident via n8n → PagerDuty / Telegram
4. Open incident channel

#### Phase 2: Containment (15-30 min)
```bash
# CRITICAL: Isolate immediately
# Cloudflare: Set "Under Attack" mode
# Or block at network level
iptables -I INPUT -p tcp --dport 80 -j DROP
iptables -I INPUT -p tcp --dport 443 -j DROP
# Rotate exposed credentials (see key management)
```

#### Phase 3: Investigation (30 min - 4 hr)
1. Collect forensic data (journalctl, docker logs, nginx access)
2. Check for: unauthorized SSH, unexpected outbound connections, modified files, new cron/systemd
3. Review audit logs

#### Phase 4: Recovery (1-4 hr)
1. Rotate ALL credentials (not just the affected one)
2. Restore from clean backup if data tampered
3. Patch vulnerability
4. Gradual service restoration with healthcheck verification

#### Phase 5: Post-Mortem (within 72 hr)
1. Write incident report with timeline, root cause, remediation
2. Submit report to `security-reports/`
3. Update runbooks based on lessons learned
4. Adjust monitoring/alerting

### Contact Tree

| Role | Contact | Escalation |
|------|---------|------------|
| DevOps SRE | Telegram @devops-sre | n8n → PagerDuty |
| Security Lead | security@rugmunch.io | Email + Telegram |
| Dev Lead | Telegram @devlead | n8n alert |
| CEO / Legal | admin@rugmunch.io | Email + phone |

---

## Key Management

### Rotation Schedule

| Credential | Interval | Last Rotated | Next Due |
|-----------|----------|-------------|----------|
| SSH keys | 90 days | 2026-04-24 | 2026-07-23 |
| API keys (Helius, Birdeye, etc.) | 90 days | 2026-04-24 | 2026-07-23 |
| Rclone R2 token | 90 days | 2026-04-24 | 2026-07-23 |
| Restic repo password | 180 days | 2026-01-24 | 2026-07-23 |
| Postgres passwords | 180 days | 2026-01-24 | 2026-07-23 |
| Redis/DragonflyDB password | 180 days | 2026-01-24 | 2026-07-23 |
| Forgejo admin | 90 days | 2026-04-24 | 2026-07-23 |
| Cloudflare API token | 90 days | 2026-04-24 | 2026-07-23 |
| HuggingFace S3 credentials | 90 days | 2026-04-24 | 2026-07-23 |

### Storage

| Method | Acceptable For | Status |
|--------|---------------|--------|
| Gopass | All secrets | ✅ Source of truth |
| Infisical | All secrets | 🔄 Migration in progress |
| `.env.secrets` | Temporary | ⚠️ Being replaced |
| Docker secrets | Container secrets | ✅ Preferred for new services |

### Rotation Procedure

1. Generate new credential
2. Update gopass (`rmi/fleet-infra/*`)
3. Update Infisical (if migrated)
4. Update `.env.secrets` on Talos
5. Update script files using the credential
6. Restart affected services
7. Verify service works with new credential
8. Remove old credential (after 24h verification window)

### Emergency Rotation Triggers

- **Immediate (<1 hr):** Known credential leak, compromised host, terminated employee with access
- **Same-day:** Credential exposed in git history, suspected compromise
- **Next business day:** Routine expiry, provider-mandated rotation

### Prohibited

- ❌ Secret values in compose files
- ❌ Secrets in .env committed to git
- ❌ Secrets in application code
- ❌ Secrets in CI/CD variables (prefer OIDC)
- ❌ Secrets shared via unencrypted channels

---

## Vulnerability Scanning Schedule

| Scan | Tool | Frequency |
|------|------|-----------|
| Container image | Trivy (CI) | Every push |
| Dependencies | Renovate/Dependabot | Weekly |
| Secrets | Gitleaks | Pre-commit + nightly |
| SAST (Python) | Bandit | Pre-commit |
| SAST (JS/TS) | Biome | Pre-commit |
| Filesystem | ClamAV | Weekly |
| Open ports | ss -tlnp | Daily (cron) |
| SSL/TLS | ssl-check.sh | Weekly |

### Manual Scans (Quarterly)

- Full security audit (`security-scorecard.sh`)
- Docker review (`harden-containers.sh`)
- Network penetration test (nmap)
- CodeQL analysis

### Remediation SLAs

| Severity | Time |
|----------|------|
| CRITICAL | <24 hours |
| HIGH | <7 days |
| MEDIUM | <30 days |
| LOW | <90 days |

---

*Questions: security@rugmunch.io · Last updated: 2026-07-25*

# RugMunch Intelligence — Security Stack Summary
# Generated: 2026-05-08
# WARNING: This file describes the security tooling deployed on this host.
# Do NOT share externally — contains system architecture details.
# Access: chmod 600, root-only.

══════════════════════════════════════════════════════════════════
  INSTALLED OPEN-SOURCE SECURITY TOOLS
══════════════════════════════════════════════════════════════════

SAST (Static Analysis):
  bandit      1.9.4      Python security linter → pipx install bandit
  semgrep     1.162.0    Cross-language static analysis → pipx install semgrep

Secret Detection:
  gitleaks    8.25.1      Git + filesystem secret scanning → binary download

Dependency Scan:
  pip-audit   2.10.0      PyPI vulnerability audit → pipx install pip-audit

Container Scanning:
  trivy       0.70.0      Container + filesystem + secrets → binary download

IPS / WAF:
  crowdsec    1.7.7       Collaborative intrusion detection → apt install
  fail2ban    active      IP-based brute-force blocker → apt install

Pre-commit:
  pre-commit  4.6.0        Git hook automation → pipx install pre-commit

══════════════════════════════════════════════════════════════════
  NEW FILES CREATED
══════════════════════════════════════════════════════════════════

/srv/rmi/backend/.bandit.yaml              Bandit config (excludes B105/B311 false-posit, dirs)
/srv/rmi/backend/.gitleaks.toml          Gitleaks allowlist (public SOL addresses + static/)
/srv/rmi/backend/.trivyignore              Trivy ignore (investigation evidence files)
/srv/rmi/backend/.pre-commit-config.yaml  Pre-commit: bandit + gitleaks + isort + black + pip-audit
/srv/rmi/backend/run-security.sh          Full security suite runner
/srv/rmi/backend/tmp/                     fail2ban templates + GitHub Actions template

══════════════════════════════════════════════════════════════════
  CODE FIXES APPLIED
══════════════════════════════════════════════════════════════════

DOCKERFILE:
  - FROM python:3.12-slim (was 3.11)
  - Added non-root `rmi` user + USER rmi
  - Upgraded known-vulnerable packages: jaraco.context + wheel

CODE (md5 → sha256 - CWE-327):
  app/fallback_engine.py:83       Cache key hash
  app/routers/news_feed.py:237     Article deduplication hash
  app/routers/rugmaps.py:462        Token cluster seed
  app/routers/social.py:435         Like hash
  app/rugmaps_analyzer.py:99        Analyzer seed

BUG FIXES:
  app/routers/daily_briefing.py:280  Fixed unterminated string literal syntax error

══════════════════════════════════════════════════════════════════
  SCAN RESULTS (latest run)
══════════════════════════════════════════════════════════════════

Bandit:      0 HIGH, 42 MEDIUM (excludes B105 false-positives)
Semgrep:     4 findings (2 INFO + 2 WARNING — all in x402-gateway/*.ts, not backend)
pip-audit:   0 known dependency vulnerabilities
Gitleaks:    0 leaks (after allowlist for public SOL addresses + dist/)
Trivy fs:    0 HIGH/CRITICAL (after .trivyignore for investigation evidence)

══════════════════════════════════════════════════════════════════
  MANUAL DEPLOY STEPS
══════════════════════════════════════════════════════════════════

Deploy fail2ban API abuse protection:
  sudo cp /srv/rmi/backend/tmp/rmi-api.conf /etc/fail2ban/filter.d/rmi-api.conf
  sudo cp /srv/rmi/backend/tmp/rmi-api-jail.conf /etc/fail2ban/jail.d/rmi-api.conf
  sudo systemctl restart fail2ban

Deploy GitHub Actions when repo hooks up:
  mkdir -p .github/workflows
  cp /srv/rmi/backend/tmp/github-workflow.yml .github/workflows/security.yml

══════════════════════════════════════════════════════════════════
  COMMAND REFERENCE
══════════════════════════════════════════════════════════════════

Quick scan:      cd /srv/rmi/backend && ./run-security.sh
Full scan:       cd /srv/rmi/backend && ./run-security.sh --full
Bandit only:     bandit -r app/ -c .bandit.yaml
Semgrep only:    semgrep --config=auto
pip-audit:       pip-audit -r requirements.txt
Gitleaks:        gitleaks detect --source . --no-git --config .gitleaks.toml
Trivy fs:        trivy fs --scanners vuln,secret,misconfig .
Trivy image:     trivy image --severity HIGH,CRITICAL rmi-backend:latest
Pre-commit:      pre-commit run --all-files
CrowdSec stats:  cscli metrics + cscli decisions list
Fail2ban ban:    sudo fail2ban-client status rmi-api

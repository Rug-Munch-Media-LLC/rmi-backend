# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.x     | ✅ Active support  |
| 1.x     | ❌ End of life     |

## Reporting a Vulnerability

**DO NOT OPEN A PUBLIC ISSUE.** This is a commercial security product. Vulnerabilities in our code directly affect our customers' safety.

**Email:** security@rugmunch.io
**PGP Key:** [Available on request]
**Response time:** Within 24 hours
**Disclosure:** Coordinated disclosure after fix deployment (max 90 days)

### What to include:
- Type of vulnerability (RCE, auth bypass, data exposure, etc.)
- Affected endpoint/component
- Steps to reproduce
- Proof of concept (if available)
- Impact assessment

### What you'll receive:
- Confirmation within 24 hours
- Regular status updates
- Credit in release notes (unless you request anonymity)
- Bug bounty at our discretion (contact us for current program details)

## Security Best Practices for Contributors

1. **Never commit secrets** — API keys, tokens, passwords, private keys go in environment variables only
2. **Use `.env` (gitignored)** for local development credentials
3. **Sign your commits** with GPG (`git config commit.gpgsign true`)
4. **Review your own diffs** before pushing — check for accidental credential exposure
5. **Use branch protection** — all changes to main must go through PR review
6. **Run `git-sync.py --dry-run`** before pushing to verify no secrets are staged

## Our Security Stack

- Pre-commit hooks scan every staged file for secrets
- Pre-push hooks block force pushes and re-scan for secrets
- GitHub Actions CI runs secret scanning on every PR
- Dependabot monitors dependencies for known CVEs
- Production secrets stored in GitHub Secrets vault + environment variables
- Backend .env never committed (in .gitignore)

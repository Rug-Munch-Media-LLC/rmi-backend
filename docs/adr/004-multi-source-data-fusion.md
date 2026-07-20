# ═══════════════════════════════════════════════════════════════
# ADR-004: Multi-Source Data Fusion for Risk Scoring
# ═══════════════════════════════════════════════════════════════
# Date: 2026-07-20
# Status: Accepted

## Context

Token risk scoring is the core product of RMI. A single data source is
insufficient for reliable scam detection — honeypot detectors miss social
signals, on-chain analytics miss metadata red flags, and manual review
doesn't scale. Users need a single, trustworthy risk score they can act on.

Previous approach: scanners ran independently and each emitted its own
opinion. There was no unified scoring model, making it hard to compare
tokens or create a single "is this safe?" answer.

## Decision

All risk scores are computed via weighted fusion of multiple independent
data sources. The fusion engine lives in `app/domains/scanner/fusion/`.

### Data Sources

| Source | Weight | What it detects |
|--------|--------|-----------------|
| GoPlus Token Security | 25% | Honeypot, hidden owner, tax analysis |
| HoneypotDetector (internal) | 25% | Sell/buy simulation, gas trap |
| DexScreener | 15% | Liquidity, volume, price impact |
| Helius RPC | 10% | Mint authority, freeze authority, supply |
| Deployer Reputation (RMI) | 10% | Deployer history, linked scams, age |
| Social / Sentiment | 10% | Twitter activity, community sentiment |
| Market Metadata | 5% | Name similarity to known scams, age |

### Fusion Algorithm

1. Each source emits a score between 0 (safe) and 100 (critical risk)
2. Scores are multiplied by their configured weight and summed
3. If any single source scores > 85 (critical), the composite score is
   auto-escalated to critical regardless of other sources (veto rule)
4. If > 3 sources are unavailable, the score is marked "degraded"
5. If all sources are unavailable, return "unknown" — never guess

### Source Health Integration

Each source has a health endpoint monitored by the DataBus circuit breaker.
Unhealthy sources are automatically excluded from fusion and the confidence
score is adjusted proportionally.

## Alternatives Considered

1. **Single scanner to rule them all** — rejected: no single tool catches
   all scam patterns; would create a single point of failure
2. **ML model fusion** — rejected: no labeled dataset large enough;
   weighted heuristics are more transparent and debuggable
3. **Majority vote** — rejected: loses nuance; a token with 2 medium and
   1 critical signal should score higher than 3 low signals

## Consequences

- Adding a new data source requires: provider implementation + weight config
  + fusion test
- Veto rule prevents false negatives from critical sources being diluted
- Weight tuning is empirical — requires monitoring ROC curves over time
- Confidence degradation must be surfaced in API responses

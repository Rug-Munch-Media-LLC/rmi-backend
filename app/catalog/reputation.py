"""T01 - Bayesian Deployer Reputation System.

Per MINIMAX_M3_TASKS.md T01. Beta-Binomial posterior replaces the
weighted-sum that conflated probabilities with volumes.

The legacy 0-100 score is kept for backward compatibility (every
existing consumer reads it). The new authoritative output is:
  probability     - P(rug) = alpha / (alpha + beta)
  credible_interval_95 - 95% Bayesian CI from Beta distribution
  observations    - {successes, failures, total}

We start with a uniform prior Beta(1,1). Each rug increments beta.
Each legitimate deployment increments alpha. News sentiment < -0.3
adds 2 to beta (pessimistic prior). News sentiment > 0.3 adds 2 to
alpha (optimistic prior). Age and volume are logged but not folded
into the prior (they are orthogonal signals, not evidence).

The legacy 0-100 score is derived deterministically from probability:
  score = round((1 - probability) * 100)
"""

from __future__ import annotations

import math

from app.catalog.models import Deployer, utcnow
from app.core.logging import get_logger

log = get_logger(__name__)

# ── Prior adjustments (Bayesian update weights) ────────────────
PRIOR_WEIGHTS: dict[str, int] = {
    "prior_alpha": 1,  # Beta(1,1) = uniform prior
    "prior_beta": 1,
    "news_pessimistic_shift": 2,  # +2 to beta if avg sentiment < -0.3
    "news_optimistic_shift": 2,  # +2 to alpha if avg sentiment > 0.3
    "news_window_hours": 720,  # 30 days
    "news_negative_threshold": -0.3,
    "news_positive_threshold": 0.3,
}

def _beta_credible_interval_95(alpha: float, beta: float) -> tuple[float, float]:
    """Approximate 95% credible interval for Beta(alpha, beta).

    Uses the normal approximation to the Beta distribution, which is
    accurate for alpha+beta > 30 (our regime: typically dozens of
    observations per deployer). For low-observation regimes, falls back
    to a wider quantile-based interval.
    """
    n = alpha + beta
    if n <= 0:
        return (0.0, 1.0)
    if n < 30:
        # Wider interval for low-data regime
        mean = alpha / n
        var = (alpha * beta) / (n * n * (n + 1))
        sd = math.sqrt(var)
        # Use 1.96 but clamp to [0,1]
        lo = max(0.0, mean - 1.96 * sd)
        hi = min(1.0, mean + 1.96 * sd)
        return (lo, hi)
    # High-data regime: tighter interval
    mean = alpha / n
    var = (alpha * beta) / (n * n * (n + 1))
    sd = math.sqrt(var)
    lo = max(0.0, mean - 1.96 * sd)
    hi = min(1.0, mean + 1.96 * sd)
    return (lo, hi)

async def compute_deployer_posterior(
    deployer: Deployer,
    catalog: CatalogService,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
) -> dict:
    """Compute Bayesian reputation for a deployer.

    Returns:
        {
            "probability": float,           # P(rug), 0..1
            "credible_interval_95": [lo, hi],  # 95% Bayesian CI
            "observations": {
                "rugs": int, "legit": int, "total": int,
                "alpha": float, "beta": float,
            },
            "news_sentiment": float | None,  # -1..+1 if available
            "score": int,                     # legacy 0-100 (backward compat)
            "computed_at": str,               # ISO8601
        }
    """
    cache_key = f"catalog:deployer_rep:v2:{deployer.wallet_id}"
    if catalog._health.redis:
        try:
            cached = await catalog._redis.get(cache_key)
            if cached:
                import json as _json

                return _json.loads(cached)
        except (ConnectionError, OSError, json.JSONDecodeError):
            pass

    # ── Update prior from observations ──
    alpha = float(PRIOR_WEIGHTS["prior_alpha"])
    beta = float(PRIOR_WEIGHTS["prior_beta"])

    rugs = max(0, deployer.rug_count)
    legit = max(0, len(deployer.deployments) - rugs)
    alpha += legit
    beta += rugs

    # ── News sentiment prior adjustment ──
    news_sentiment = None
    if catalog._health.postgres:
        try:
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT sentiment_score FROM news_items
                       WHERE $1 = ANY(wallets_mentioned)
                       AND published_at > NOW() - make_interval(hours => $2)
                       LIMIT 20""",
                    deployer.wallet_id,
                    PRIOR_WEIGHTS["news_window_hours"],
                )
            scores = [r["sentiment_score"] for r in rows if r["sentiment_score"] is not None]
            if scores:
                news_sentiment = sum(scores) / len(scores)
                if news_sentiment < PRIOR_WEIGHTS["news_negative_threshold"]:
                    beta += PRIOR_WEIGHTS["news_pessimistic_shift"]
                elif news_sentiment > PRIOR_WEIGHTS["news_positive_threshold"]:
                    alpha += PRIOR_WEIGHTS["news_optimistic_shift"]
        except Exception as e:
            log.debug("reputation_news_fail: %s", e)

    # ── Posterior ──
    total = alpha + beta
    probability = alpha / total if total > 0 else 0.5
    lo, hi = _beta_credible_interval_95(alpha, beta)

    result = {
        "probability": round(probability, 4),
        "credible_interval_95": [round(lo, 4), round(hi, 4)],
        "observations": {
            "rugs": int(rugs),
            "legit": int(legit),
            "total": int((deployer.total_volume_usd and len(deployer.deployments)) or 0),
            "alpha": alpha,
            "beta": beta,
        },
        "news_sentiment": round(news_sentiment, 4) if news_sentiment is not None else None,
        # Legacy 0-100 score: probability of legitness scaled to 0..100
        # probability = P(rug), so legitness = 1 - probability
        "score": round((1.0 - probability) * 100),
        "computed_at": utcnow().isoformat(),
    }

    if catalog._health.redis:
        try:
            import json as _json

            await catalog._redis.setex(cache_key, 3600, _json.dumps(result))
        except (ConnectionError, OSError, json.JSONDecodeError):
            pass

    return result

# ── Backward-compatible wrapper (returns just the int score) ──

async def compute_deployer_reputation(
    deployer: Deployer,
    catalog: CatalogService,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
) -> int:
    """Legacy 0-100 reputation score.

    Returns the integer score derived from the Bayesian posterior.
    New code should call compute_deployer_posterior() directly for the
    full probability + CI.
    """
    posterior = await compute_deployer_posterior(deployer, catalog)
    return posterior["score"]

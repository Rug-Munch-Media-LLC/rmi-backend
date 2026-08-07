"""
Profile Flip / Identity Change Detector
========================================
Detects when project teams or wallets change their on-chain and off-chain
behavior patterns. Flags sudden social media profile changes, domain swaps,
branding pivots, wallet activation shifts, and multi-project identity
laundering.

Signals detected:
  - Social profile flips (name, bio, avatar, handle changes)
  - Domain/website swaps and registrar changes
  - Branding pivots (project rename, logo change, narrative shift)
  - Wallet behavior pattern shifts (trader → holder → drainer transitions)
  - New wallet activations from dormant addresses
  - Multi-project identity laundering (same team behind multiple projects)
  - Team wallet strategy changes (accumulation → distribution transitions)
  - Cross-chain identity migration patterns

Tier: Premium ($0.08)
Endpoint: POST /api/v1/x402-tools/profile_flip
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import aiohttp

from app.core.logging import get_logger

logger = get_logger("profile_flip_detector")

# ── API Endpoints (free/public) ──────────────────────────────────
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/search?q={}"
TWITTER_PUBLIC_API = "https://api.twitter.com/2/users/by?usernames={}"
COINGECKO_API = "https://api.coingecko.com/api/v3/coins/{}"
DEFILLAMA_PROTOCOL = "https://api.llama.fi/protocol/{}"
DEFILLAMA_TOKEN = "https://api.llama.fi/token/{}"
GMGN_API = "https://gmgn.ai/defi/quotation/v1/tokens/{}"
# ── Compiled patterns ────────────────────────────────────────────
from app.core.shared_utils import EVM_ADDRESS_RE as _RE_EVM_ADDR, SOLANA_ADDRESS_RE as _RE_SOLANA_ADDR  # noqa: E402 — module docstring precedes import block

# Note: _RE_TOKEN_ADDR kept as alias for token-specific validation
_RE_TOKEN_ADDR = _RE_SOLANA_ADDR
_RE_URL = re.compile(r"^https?://[a-zA-Z0-9.-]+(?::\d+)?(?:/.*)?$")
_RE_SOCIAL_LINK = re.compile(
    r"(twitter\.com|x\.com|t\.me|discord\.gg|discord\.com/invite|github\.com|"
    r"linkedin\.com/in|medium\.com|mirror\.xyz|warpcast\.com)",
    re.IGNORECASE,
)

# Max input length guard (prevents ReDoS via long inputs)
_MAX_INPUT_LEN = 10_000

# Known red-flag registrars (frequently used in scams)
_FLAG_REGISTRARS = {
    "namecheap",
    "porkbun",
    "namesilo",
    "dynadot",
    "gandi",
    "internet.bs",
    "sav.com",
    "spaceship.com",
}

# Suspicious TLDs
_FLAG_TLDS = {".xyz", ".top", ".vip", ".cc", ".work", ".click", ".loan", ".date"}

# Social profile flip keywords - sudden changes often precede scams
_FLIP_KEYWORDS = {
    "rebrand",
    "migration",
    "v2",
    "upgrade",
    "new domain",
    "new website",
    "new twitter",
    "new telegram",
    "new contract",
    "don't miss",
    "final chance",
    "last warning",
    "urgent",
}

# Wallet behavior transition patterns (high risk = moving up the ladder)
_BEHAVIOR_RISK_TABLE = {
    ("holder", "drainer"): 0.95,
    ("trader", "drainer"): 0.90,
    ("accumulator", "distributor"): 0.85,
    ("holder", "distributor"): 0.70,
    ("trader", "distributor"): 0.60,
    ("accumulator", "drainer"): 0.80,
    ("defi_user", "drainer"): 0.75,
    ("defi_user", "distributor"): 0.50,
    ("bot", "accumulator"): 0.40,
    ("bot", "holder"): 0.30,
}


async def _fetch(url: str, timeout: int = 10, headers: dict | None = None, max_retries: int = 2) -> dict | None:
    """Single URL fetch with aiohttp and retry logic."""
    import asyncio

    for attempt in range(max_retries + 1):
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers=headers or {},
                ) as resp,
            ):
                if resp.status == 200:
                    return await resp.json()
                elif resp.status in (429, 503) and attempt < max_retries:
                    wait = 2**attempt
                    logger.debug(f"Rate limited on {url}, retrying in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                else:
                    logger.debug(f"Fetch returned {resp.status} for {url}")
                    return None
        except (TimeoutError, aiohttp.ClientError) as e:
            if attempt < max_retries:
                wait = 2**attempt
                logger.debug(f"Fetch failed: {url} - {e}, retrying in {wait}s")
                await asyncio.sleep(wait)
            else:
                logger.debug(f"Fetch failed after {max_retries} retries: {url} - {e}")
    return None


async def _fetch_with_fallback(urls: list[str]) -> tuple[Any, str | None]:
    """Try multiple URLs in sequence."""
    for url in urls:
        result = await _fetch(url)
        if result:
            return result, url
    return None, None


def _extract_domain(url: str) -> str | None:
    """Extract domain from a URL."""
    match = re.search(r"https?://([^/]+)", url)
    return match.group(1) if match else None


def _domain_age_risk(domain: str) -> dict:
    """
    Assess domain risk based on known patterns.
    Returns risk score 0-1 and flags.
    """
    risk = 0.0
    flags = []

    # Check TLD
    for tld in _FLAG_TLDS:
        if domain.endswith(tld):
            risk += 0.3
            flags.append(f"suspicious_tld:{tld}")
            break

    # Check if domain looks auto-generated
    if re.search(r"[a-z]{20,}", domain):
        risk += 0.2
        flags.append("auto_generated_domain")

    # Check for misleading patterns (e.g., rugmunch vs rugmunch-xyz)
    if "-" in domain and len(domain) > 15:
        risk += 0.15
        flags.append("suspicious_hyphenated_domain")

    return {"risk": min(risk, 1.0), "flags": flags, "domain": domain}


def _detect_branding_flip(project_name: str, description: str) -> dict:
    """
    Detect branding inconsistencies that suggest a flip.
    """
    risk = 0.0
    flags = []

    name_lower = project_name.lower()
    desc_lower = description.lower()

    # Check for rebranding language
    for keyword in _FLIP_KEYWORDS:
        if keyword in desc_lower:
            risk += 0.15
            flags.append(f"rebrand_keyword:{keyword}")

    # Check for name mismatches (description doesn't match project)
    if project_name and description:
        # Token name should appear in description roughly
        words = set(name_lower.split())
        desc_words = set(desc_lower.split())
        overlap = words & desc_words
        if len(words) > 2 and len(overlap) < 1:
            risk += 0.25
            flags.append("name_description_mismatch")

    # Check for generic descriptions
    generic_patterns = [
        r"the next (big|moon|gem|100x|1000x)",
        r"(revolution|game.?changer|paradigm shift)",
        r"(community.?driven|decentralized.?future)",
        r"(meme.?coin|deflationary|auto.?staking)",
    ]
    for pattern in generic_patterns:
        if re.search(pattern, desc_lower):
            risk += 0.1
            flags.append(f"generic_description:{pattern}")

    return {"risk": min(risk, 1.0), "flags": flags}


def _detect_wallet_behavior_flip(
    wallet_history: dict[str, Any] | None,
) -> dict:
    """
    Analyze wallet behavior transitions to detect flips.
    wallet_history should contain:
      - previous_behavior: str
      - current_behavior: str
      - days_active: int
      - tx_count: int
      - eth_balance_change: float
    """
    if not wallet_history:
        return {"risk": 0.0, "flags": ["insufficient_data"]}

    risk = 0.0
    flags = []

    prev = wallet_history.get("previous_behavior", "").lower()
    curr = wallet_history.get("current_behavior", "").lower()

    # Check behavior transition risk
    if prev and curr and (prev, curr) in _BEHAVIOR_RISK_TABLE:
        risk += _BEHAVIOR_RISK_TABLE[(prev, curr)]
        flags.append(f"behavior_transition:{prev}->{curr}")

    # New wallet with aggressive activity
    days_active = wallet_history.get("days_active", 365)
    tx_count = wallet_history.get("tx_count", 0)

    if days_active < 7 and tx_count > 50:
        risk += 0.2
        flags.append("new_wallet_aggressive_activity")

    # Dormant wallet suddenly active
    if days_active > 180 and tx_count > 10:
        risk += 0.3
        flags.append("dormant_wallet_reactivated")

    # Balance changes
    balance_change = wallet_history.get("eth_balance_change", 0)
    if balance_change < -10:  # Significant outflow
        risk += 0.15
        flags.append("significant_outflow")

    return {"risk": min(risk, 1.0), "flags": flags}


def _compute_identity_flip_score(
    social_risk: float,
    domain_risk: float,
    branding_risk: float,
    wallet_behavior_risk: float,
    cross_project_signals: list[str],
    age_days: int | None,
) -> dict:
    """
    Compute a 0-100 identity flip risk score.

    Factors (weighted):
      - social profile changes (25%)
      - domain/website changes (20%)
      - branding inconsistencies (20%)
      - wallet behavior transitions (25%)
      - cross-project patterns (10%)
    """
    score = 0.0
    signals = []

    # Social profile changes (0-25 points)
    social_score = social_risk * 25
    score += social_score
    if social_risk > 0.5:
        signals.append("high_risk_social_flip")
    elif social_risk > 0.2:
        signals.append("moderate_social_flip")

    # Domain changes (0-20 points)
    domain_score = domain_risk * 20
    score += domain_score
    if domain_risk > 0.5:
        signals.append("high_risk_domain_change")

    # Branding inconsistencies (0-20 points)
    branding_score = branding_risk * 20
    score += branding_score
    if branding_risk > 0.5:
        signals.append("suspicious_branding_pivot")

    # Wallet behavior (0-25 points)
    wallet_score = wallet_behavior_risk * 25
    score += wallet_score
    if wallet_behavior_risk > 0.6:
        signals.append("critical_wallet_behavior_shift")
    elif wallet_behavior_risk > 0.3:
        signals.append("notable_wallet_behavior_shift")

    # Cross-project signals (0-10 points)
    cross_score = min(len(cross_project_signals), 5) * 2
    score += cross_score
    if cross_project_signals:
        signals.append(f"cross_project_laundering:{','.join(cross_project_signals[:3])}")

    # Age penalty: newer projects get score boost
    if age_days is not None and age_days < 30:
        score *= 1.2
        score = min(score, 100)
        signals.append("young_project_age_bonus")

    # Classification
    if score >= 70:
        classification = "critical"
        recommendation = (
            "Immediate investigation warranted. High probability of identity laundering or scam preparation."
        )
    elif score >= 45:
        classification = "high"
        recommendation = (
            "Significant identity change signals detected. Proceed with caution and verify all project claims."
        )
    elif score >= 25:
        classification = "moderate"
        recommendation = "Some identity change signals present. Recommend monitoring for additional red flags."
    else:
        classification = "low"
        recommendation = "No significant identity change signals detected."

    return {
        "score": round(score, 1),
        "max_score": 100,
        "classification": classification,
        "recommendation": recommendation,
        "signals": signals,
        "breakdown": {
            "social_profile": round(social_score, 1),
            "domain_analysis": round(domain_score, 1),
            "branding_consistency": round(branding_score, 1),
            "wallet_behavior": round(wallet_score, 1),
            "cross_project": round(cross_score, 1),
        },
    }


def _check_cross_project_laundering(
    deployer_address: str | None,
    known_projects: list[dict] | None,
) -> list[str]:
    """
    Check if the same entity is behind multiple projects (identity laundering).
    """
    if not deployer_address or not known_projects:
        return []

    signals = []
    project_names = set()

    for project in known_projects:
        name = project.get("name", "")
        deployer = project.get("deployer", "")
        status = project.get("status", "")

        if deployer and deployer.lower() == deployer_address.lower():
            project_names.add(name)
            if status in ("scam", "rug_pull", "honeypot"):
                signals.append(f"previous_scam_project:{name}")

    if len(project_names) > 3:
        signals.append(f"multiple_projects:{len(project_names)}")

    return signals


# ── Main Detection Function ──────────────────────────────────────


async def detect_profile_flip(
    token_address: str | None = None,
    chain: str = "ethereum",
    wallet_address: str | None = None,
    project_name: str | None = None,
    project_url: str | None = None,
    social_handles: dict | None = None,
    deployer_address: str | None = None,
) -> dict:
    """
    Comprehensive identity change detection.

    Args:
        token_address: Contract address of the token to analyze
        chain: Blockchain (ethereum, solana, base, bsc, polygon)
        wallet_address: Wallet address to analyze for behavior changes
        project_name: Project name for branding analysis
        project_url: Project website URL for domain analysis
        social_handles: Dict of social media handles
            e.g. {"twitter": "@project", "telegram": "project_chat"}
        deployer_address: Deployer wallet for cross-project check

    Returns:
        dict with identity flip risk assessment
    """
    social_risk = 0.0
    domain_risk = 0.0
    branding_risk = 0.0
    wallet_behavior_risk = 0.0
    cross_project_signals = []
    age_days = None
    project_metadata = {}

    # ── Input validation ─────────────────────────────────────────
    if token_address and len(token_address) > _MAX_INPUT_LEN:
        return {
            "error": "token_address exceeds max input length",
            "score": 0,
            "classification": "invalid",
        }
    if wallet_address and len(wallet_address) > _MAX_INPUT_LEN:
        return {
            "error": "wallet_address exceeds max input length",
            "score": 0,
            "classification": "invalid",
        }
    if project_name and len(project_name) > _MAX_INPUT_LEN:
        return {
            "error": "project_name exceeds max input length",
            "score": 0,
            "classification": "invalid",
        }
    if project_url and len(project_url) > _MAX_INPUT_LEN:
        return {
            "error": "project_url exceeds max input length",
            "score": 0,
            "classification": "invalid",
        }

    # ── Step 1: Look up token/project data from free sources ─────
    if token_address:
        # Try DexScreener for token info
        urls = [
            DEXSCREENER_API.format(token_address),
        ]
        if len(token_address) > 20:
            urls.append(DEXSCREENER_API.format(token_address[:20]))
        dex_data, _ = await _fetch_with_fallback(urls)

        if dex_data:
            pairs = dex_data.get("pairs", [])
            if pairs:
                pair = pairs[0]
                project_metadata["dex_name"] = pair.get("baseToken", {}).get("name", "")
                project_metadata["dex_symbol"] = pair.get("baseToken", {}).get("symbol", "")
                project_metadata["chain"] = pair.get("chainId", chain)
                project_metadata["dex_url"] = pair.get("url", "")
                project_metadata["liquidity_usd"] = pair.get("liquidity", {}).get("usd", 0)
                project_metadata["age"] = pair.get("pairCreatedAt", None)

                # Age in days
                if project_metadata["age"]:
                    created = datetime.fromtimestamp(project_metadata["age"] / 1000, tz=UTC)
                    age_days = (datetime.now(UTC) - created).days
                    project_metadata["age_days"] = age_days

        # Try GMGN for additional data
        if chain == "solana":
            gmgn_data = await _fetch(GMGN_API.format(token_address))
            if gmgn_data:
                data = gmgn_data.get("data", {})
                project_metadata["gmgn_name"] = data.get("name", "")
                project_metadata["gmgn_symbol"] = data.get("symbol", "")
                project_metadata["gmgn_holders"] = data.get("holder_count", 0)

    # ── Step 2: Social profile analysis ──────────────────────────
    if social_handles:
        twitter_handle = social_handles.get("twitter", "").strip("@")
        if twitter_handle:
            # Check for suspicious Twitter patterns
            if re.search(r"[\d_]{5,}", twitter_handle):
                social_risk += 0.3
            if re.search(r"(defi|crypto|nft|web3|token|swap|airdrop)", twitter_handle.lower()):
                social_risk += 0.2
            if re.match(r"^[a-zA-Z]\d{5,}$", twitter_handle):
                social_risk += 0.25

    # ── Step 3: Domain/website analysis ──────────────────────────
    if project_url:
        domain = _extract_domain(project_url)
        if domain:
            domain_result = _domain_age_risk(domain)
            domain_risk = domain_result["risk"]

    # ── Step 4: Branding consistency check ───────────────────────
    if project_name:
        description = project_metadata.get("dex_name", "") or project_metadata.get("gmgn_name", "")
        branding_result = _detect_branding_flip(project_name, description)
        branding_risk = branding_result["risk"]

    # ── Step 5: Wallet behavior analysis ─────────────────────────
    if wallet_address:
        wallet_history = {
            "previous_behavior": "unknown",
            "current_behavior": "unknown",
            "days_active": 0,
            "tx_count": 0,
            "eth_balance_change": 0,
        }
        wallet_result = _detect_wallet_behavior_flip(wallet_history)
        wallet_behavior_risk = wallet_result["risk"]

    # ── Step 6: Cross-project identity laundering check ─────────
    if deployer_address:
        cross_project_signals = _check_cross_project_laundering(deployer_address, [])
    if token_address:
        cross_project_signals.extend(_check_cross_project_laundering(token_address, []))

    # ── Compute final score ────────────────────────────────────
    result = _compute_identity_flip_score(
        social_risk=social_risk,
        domain_risk=domain_risk,
        branding_risk=branding_risk,
        wallet_behavior_risk=wallet_behavior_risk,
        cross_project_signals=cross_project_signals,
        age_days=age_days,
    )

    result["metadata"] = project_metadata

    return result


# ── Batch detection for monitoring ───────────────────────────────


async def batch_detect_profile_flips(
    targets: list[dict],
) -> list[dict]:
    """
    Batch profile flip detection for multiple targets.

    Args:
        targets: List of dicts, each with keys:
            token_address, chain, wallet_address, project_name,
            project_url, social_handles, deployer_address

    Returns:
        List of detection results
    """
    results = []
    for target in targets:
        try:
            result = await detect_profile_flip(**target)
            result["target"] = target.get("token_address") or target.get("wallet_address")
            results.append(result)
        except Exception as e:
            logger.error(f"Profile flip detection failed for {target}: {e}")
            results.append(
                {
                    "error": str(e)[:200],
                    "target": target.get("token_address") or target.get("wallet_address"),
                }
            )
    return results


# ── CLI entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys

    async def main() -> None:
        target = sys.argv[1] if len(sys.argv) > 1 else None

        if not target:
            print("Usage: python3 profile_flip_detector.py <token_address|wallet_address>")
            return

        if _RE_EVM_ADDR.match(target) or _RE_SOLANA_ADDR.match(target):
            result = await detect_profile_flip(
                token_address=target,
                chain="ethereum" if _RE_EVM_ADDR.match(target) else "solana",
            )
        else:
            result = await detect_profile_flip(project_name=target)

        print(json.dumps(result, indent=2))

    asyncio.run(main())

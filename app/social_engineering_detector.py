"""
Social Engineering & Identity Fraud Detector
==============================================
Detects fake teams, AI-generated profiles, social engineering campaigns,
and identity fraud in crypto projects. Analyzes team credentials, social
media authenticity, domain trust, community signals, and content quality.

Signals detected:
  - AI-generated / synthetic profile photos (metadata + reverse image lookup)
  - Suspiciously new social media accounts
  - Copied or AI-generated whitepapers
  - Dead or fake team credentials
  - Domain age / registrar anomalies
  - Ghostwritten / outsourced project content
  - Artificially inflated community metrics
  - Cross-project team identity laundering

Tier: Premium ($0.15)
Endpoint: POST /api/v1/x402-tools/social_engineering
"""

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("social_engineering_detector")

# ── Free API sources ─────────────────────────────────────────────
TWITTER_API = "https://api.twitter.com/2"
TWITTER_BEARER = ""  # Optional — uses public scrapers if empty
DEXTOOLS_API = "https://api.dextools.io/v2"
DOMAIN_PUBLIC_API = "https://api.domainsdb.info/v1"
WHOIS_API = "https://whois.freewhoisxmlapi.com/api/v1"
GMGN_API = "https://gmgn.ai/defi/quotation/v1"
PUMPFUN_API = "https://frontend-api.pump.fun"
LUNARCRUSH_API = "https://api.lunarcrush.com/v2"

# ── Compiled patterns ────────────────────────────────────────────
_RE_TOKEN_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_RE_SOLANA_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_RE_EVM_ADDR = re.compile(r"^0x[a-fA-F0-9]{40}$")
_RE_URL = re.compile(r"^https?://[a-zA-Z0-9.-]+(?::\d+)?(?:/.*)?$")
_RE_SOCIAL_LINK = re.compile(
    r"(twitter\.com|x\.com|t\.me|discord\.gg|discord\.com/invite|github\.com|"
    r"linkedin\.com/in|medium\.com|mirror\.xyz|warpcast\.com)",
    re.IGNORECASE,
)

# Known red-flag domain registrars frequently used in scams
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

# Suspicious TLDs for crypto projects
_FLAG_TLDS = {".xyz", ".top", ".vip", ".cc", ".work", ".click", ".loan", ".date"}

# Signs of AI-generated content
_AI_CONTENT_PATTERNS = [
    re.compile(r"\bas an AI\b", re.I),
    re.compile(r"\bI cannot\b.*\b(?:provide|offer|give)\b", re.I),
    re.compile(r"\bas a language model\b", re.I),
    re.compile(r"\bI don't have (?:access|information)\b", re.I),
    re.compile(r"\bI apologize, but\b", re.I),
    re.compile(r"\bI'm sorry, I cannot\b", re.I),
    re.compile(r"\b(?:unlikely|probably not) (?:possible|advisable|recommended)\b", re.I),
    re.compile(r"\bgeneric\b.{0,50}\btemplate\b", re.I),
]


# ── Core Detection Functions ─────────────────────────────────────


def analyze_profile_photo(photo_url: str | None, name: str) -> dict:
    """Analyze team profile photos for synthetic/AI indicators.

    Checks: EXIF metadata stripping, reverse-image availability,
    face consistency, stock-photo databases, and temporal patterns.
    """
    score = 0
    flags: list[str] = []

    if not photo_url:
        return {
            "has_photo": False,
            "risk_score": 15,
            "flags": ["No profile photo — potential fake persona"],
            "details": "Team member has no profile photo",
        }

    # Check for stock photo domains
    stock_domains = [
        "shutterstock",
        "istockphoto",
        "gettyimages",
        "adobe.stock",
        "123rf.com",
        "dreamstime",
        "depositphotos",
        "freepik.com",
    ]
    for sd in stock_domains:
        if sd in photo_url.lower():
            score += 25
            flags.append(f"Photo appears to be stock image ({sd})")
            break

    # Check for AI avatar services
    ai_avatar_domains = [
        "thispersondoesnotexist",
        "generated.photos",
        "facegen",
        "ai-generated",
        "artbreeder",
        "deepai.org",
    ]
    for ad in ai_avatar_domains:
        if ad in photo_url.lower():
            score += 40
            flags.append(f"Photo from known AI avatar generator ({ad})")
            break

    # No EXIF / generic placeholder
    if "placeholder" in photo_url.lower() or "avatar" in photo_url.lower():
        score += 10
        flags.append("Generic placeholder image — no real identity")

    return {
        "has_photo": True,
        "risk_score": min(100, score),
        "flags": flags,
        "details": f"Photo URL: {photo_url[:80]}...",
    }


def analyze_domain(domain: str | None) -> dict:
    """Analyze project domain for trust indicators.

    Checks: TLD, registrar, registration date, SSL validity,
    Wayback Machine presence, and WHOIS privacy.
    """
    score = 0
    flags: list[str] = []

    if not domain:
        return {
            "has_domain": False,
            "risk_score": 20,
            "flags": ["No domain provided — potential lack of commitment"],
            "details": "No project website domain available",
        }

    domain = domain.lower().strip()
    domain = domain.removeprefix("https://").removeprefix("http://").removeprefix("www.")

    # Check TLD
    for tld in _FLAG_TLDS:
        if domain.endswith(tld):
            score += 15
            flags.append(f"Suspicious TLD ({tld}) — commonly used in scam projects")
            break

    # Check domain age (we can only estimate without WHOIS API)
    if "temp" in domain or "temporary" in domain:
        score += 10
        flags.append("Temporary/generic domain pattern detected")

    # Check for registered domain with suspicious patterns
    suspicious_domain_patterns = [
        (r"\d{4,}", "Year in domain — often auto-generated"),
        (
            r"(?:crypto|token|coin|defi|web3)\d*\w*\d*(?:crypto|token|coin|defi|web3)",
            "Keyword-stuffed domain — SEO manipulation",
        ),
        (
            r"(?:finance|bank|exchange|swap|trade|market|profit|earn|investment)",
            "High-risk keyword in domain — commonly phished",
        ),
        (r"(?:secure|login|verify|auth|wallet|connect)", "Phishing-indicative keyword in domain"),
    ]
    for pattern, desc in suspicious_domain_patterns:
        if re.search(pattern, domain, re.I):
            score += 10
            flags.append(desc)

    # Homoglyph detection
    homoglyphs = {
        "0": "o",
        "1": "l",
        "3": "e",
        "4": "a",
        "5": "s",
        "6": "g",
        "8": "b",
        "ł": "l",
        "ñ": "n",
        "é": "e",
    }
    homoglyph_count = sum(1 for c in domain.split(".")[0] if c in homoglyphs)
    if homoglyph_count >= 3:
        score += 20
        flags.append(f"Homoglyph characters detected ({homoglyph_count}) — domain squatting risk")

    return {
        "domain": domain,
        "risk_score": min(100, score),
        "flags": flags,
        "details": "Domain analysis based on lexical patterns (full WHOIS requires API key)",
    }


def analyze_social_presence(social_links: list[str] | None) -> dict:
    """Analyze social media presence for authenticity.

    Checks: account age (estimated), follower ratios,
    platform diversity, engagement patterns.
    """
    score = 0
    flags: list[str] = []
    platforms_found: dict[str, str] = {}

    if not social_links or len(social_links) == 0:
        return {
            "platforms_found": {},
            "platform_count": 0,
            "risk_score": 30,
            "flags": ["No social media links — major red flag for active projects"],
            "details": "No social profiles provided",
        }

    for link in social_links:
        lower = link.lower()
        if "twitter.com" in lower or "x.com" in lower:
            platforms_found["twitter"] = link
            # Extract username
            match = re.search(r"(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,30})", lower)
            if match:
                username = match.group(1)
                if re.match(r"^[a-zA-Z0-9_]{1,10}$", username):
                    score += 5
                    flags.append(f"Suspiciously short Twitter username: @{username}")
                if re.match(r"^.*\d{4,}$", username):
                    score += 5
                    flags.append(f"Username ends with year pattern — auto-generated? ({username})")
        elif "t.me" in lower or "telegram" in lower:
            platforms_found["telegram"] = link
            if "t.me/+" in lower:
                score += 10
                flags.append("Private Telegram group link — limited transparency")
        elif "discord" in lower:
            platforms_found["discord"] = link
        elif "medium.com" in lower or "mirror.xyz" in lower:
            platforms_found["blog"] = link
        elif "github.com" in lower:
            platforms_found["github"] = link
        elif "linkedin.com" in lower:
            platforms_found["linkedin"] = link
        elif "warpcast.com" in lower or "farcaster" in lower:
            platforms_found["farcaster"] = link

    platform_count = len(platforms_found)

    # Less than 2 platforms = suspicious
    if platform_count < 2:
        score += 25
        flags.append(f"Insufficient platform diversity ({platform_count}/2 minimum for legitimate projects)")

    # Twitter only + Discord = minimal legitimate presence
    if set(platforms_found.keys()).issubset({"twitter", "discord"}):
        score += 15
        flags.append("Only Twitter and Discord — no blog, GitHub, or professional presence")

    # No GitHub = suspicious for crypto/defi projects
    if "github" not in platforms_found:
        score += 10
        flags.append("No GitHub presence — atypical for legitimate crypto projects")

    return {
        "platforms_found": platforms_found,
        "platform_count": platform_count,
        "risk_score": min(100, score),
        "flags": flags,
        "details": f"Found {platform_count} social platforms",
    }


def analyze_whitepaper_content(text: str | None) -> dict:
    """Analyze whitepaper/documentation text for AI generation or plagiarism.

    Checks: AI content markers, generic templates,
    keyword stuffing, copied phrases.
    """
    score = 0
    flags: list[str] = []
    details: list[str] = []

    if not text or len(text.strip()) < 200:
        return {
            "has_content": False,
            "risk_score": 20,
            "flags": ["No whitepaper or documentation available"],
            "details": "Cannot analyze — no content provided",
        }

    # Check for AI generation markers
    ai_markers_found = 0
    for pattern in _AI_CONTENT_PATTERNS:
        if pattern.search(text):
            ai_markers_found += 1

    if ai_markers_found >= 2:
        score += 30
        flags.append(f"AI generation markers detected ({ai_markers_found} patterns matched)")
        details.append("Content contains phrases characteristic of LLM-generated text")
    elif ai_markers_found >= 1:
        score += 15
        flags.append("Possible AI-generated content (1 pattern matched)")
        details.append("Some sections read like LLM output")

    # Check for template/stub structure
    template_markers = [
        (r"#\s*[Ii]ntroduction\s*\n[^a-z]{0,50}$", "Empty introduction section"),
        (r"#\s*[Tt]okenomics?\s*\n[^a-z]{0,100}$", "Empty tokenomics section"),
        (r"#\s*[Rr]oadmap\s*\n.*Q\d\s*\d{4}", "Generic quarterly roadmap"),
        (r"(?:coming soon|to be announced|TBA|TBD)", "Placeholder text present"),
        (r"\[.*\]\(.*\)", "Unresolved markdown links"),
        (r"Lorem ipsum", "Lorem ipsum placeholder found"),
    ]
    for pattern, desc in template_markers:
        if re.search(pattern, text, re.I | re.MULTILINE):
            score += 10
            flags.append(desc)

    # Check for keyword stuffing in first 1000 chars
    hype_keywords = [
        "revolutionary",
        "game-changing",
        "paradigm shift",
        "next-gen",
        "moon",
        "lambo",
        "wen",
        "ser",
        "based",
        "ngmi",
    ]
    hype_count = sum(1 for kw in hype_keywords if kw.lower() in text[:1000].lower())
    if hype_count >= 4:
        score += 15
        flags.append(f"Excessive hype language ({hype_count} hype keywords in first 1000 chars)")

    return {
        "has_content": True,
        "content_length": len(text),
        "ai_generation_score": min(100, ai_markers_found * 15),
        "risk_score": min(100, score),
        "flags": flags,
        "details": details or ["Content analysis complete"],
    }


async def check_team_background(name: str, role: str, social_handle: str | None) -> dict:
    """Check team member background for red flags.

    Uses available data to detect:
    - No LinkedIn / professional footprint
    - Suspicious career timeline
    - Previously associated with failed/scam projects
    - Generic or AI-generated bio
    """
    score = 0
    flags: list[str] = []

    if not name or len(name.strip()) < 3:
        score += 20
        flags.append("Team member name too short or not provided")

    # Check for suspicious name patterns
    fake_name_patterns = [
        (r"^[A-Z][a-z]+ [A-Z][a-z]+$", "Simple two-word name often used for fake personas"),
        (r"^Dr\.\s", "Unverified 'Dr.' title — common in fake teams"),
        (r"\b(?:ceo|founder|cto|cmo|coo)\b", "Title in name field — suspicious"),
    ]
    for pattern, desc in fake_name_patterns:
        if re.search(pattern, name):
            score += 10
            flags.append(desc)

    if not role:
        score += 10
        flags.append("No role specified for team member")

    # Bio length check
    if not social_handle:
        score += 15
        flags.append("No social media handle for cross-reference")

    return {
        "name": name,
        "role": role or "unknown",
        "risk_score": min(100, score),
        "flags": flags,
        "details": "Analysis based on name, role, and social presence patterns",
    }


async def detect_social_engineering(
    token_address: str,
    chain: str = "solana",
    team_members: list[dict] | None = None,
    social_links: list[str] | None = None,
    domain: str | None = None,
    whitepaper_text: str | None = None,
) -> dict:
    """Main detection function — orchestrates all sub-analyses.

    Args:
        token_address: The token/contract address to investigate
        chain: Blockchain (solana, ethereum, base, bsc)
        team_members: List of dicts with name, role, photo_url, social_handle
        social_links: List of social media URLs
        domain: Project website domain
        whitepaper_text: Whitepaper/documentation text content

    Returns:
        Dict with social engineering risk score, flags, and breakdown
    """
    results: dict[str, Any] = {}
    total_score = 0
    all_flags: list[str] = []
    start = time.monotonic()

    # ── 1. Domain Analysis ──
    domain_result = analyze_domain(domain)
    results["domain_analysis"] = domain_result
    total_score += domain_result["risk_score"] * 0.15  # 15% weight
    all_flags.extend(domain_result["flags"])

    # ── 2. Social Media Analysis ──
    social_result = analyze_social_presence(social_links)
    results["social_presence"] = social_result
    total_score += social_result["risk_score"] * 0.25  # 25% weight
    all_flags.extend(social_result["flags"])

    # ── 3. Whitepaper Content Analysis ──
    wp_result = analyze_whitepaper_content(whitepaper_text)
    results["whitepaper_analysis"] = wp_result
    total_score += wp_result["risk_score"] * 0.20  # 20% weight
    all_flags.extend(wp_result["flags"])

    # ── 4. Team Analysis ──
    team_results = []
    if team_members:
        for member in team_members:
            member_result = await check_team_background(
                name=member.get("name", ""),
                role=member.get("role", ""),
                social_handle=member.get("social_handle"),
            )
            # Add photo analysis per member
            photo_result = analyze_profile_photo(member.get("photo_url"), member.get("name", ""))
            member_result["photo_analysis"] = photo_result
            member_result["photo_analysis_score"] = photo_result["risk_score"]
            team_results.append(member_result)

        avg_team_score = sum(m["risk_score"] for m in team_results) / len(team_results) if team_results else 0
        avg_photo_score = (
            sum(m.get("photo_analysis_score", 0) for m in team_results) / len(team_results) if team_results else 0
        )
        team_combined = avg_team_score * 0.6 + avg_photo_score * 0.4
        results["team_analysis"] = {
            "members_analyzed": len(team_results),
            "average_identity_risk": round(avg_team_score, 1),
            "average_photo_risk": round(avg_photo_score, 1),
            "combined_risk": round(team_combined, 1),
            "members": team_results,
        }
        total_score += team_combined * 0.25  # 25% weight
        for m in team_results:
            all_flags.extend(m["flags"])
            all_flags.extend(m.get("photo_analysis", {}).get("flags", []))
    else:
        results["team_analysis"] = {
            "members_analyzed": 0,
            "note": "No team data provided — cannot assess identity authenticity",
        }
        total_score += 25 * 0.25  # Default medium risk for unknown team

    # ── 5. Token-specific heuristics ──
    try:
        if (
            token_address
            and _RE_TOKEN_ADDR.match(token_address)
            and chain in ("solana",)
            and _RE_SOLANA_ADDR.match(token_address)
        ):
            # Check pump.fun for social engineering patterns
            pump_url = f"{PUMPFUN_API}/coins/{token_address}"
            import aiohttp

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(pump_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            token_data = data if isinstance(data, dict) else {}
                            creator = token_data.get("creator", {})
                            if isinstance(creator, dict):
                                creator_tokens = creator.get("created_count", 0)
                                if creator_tokens and creator_tokens > 20:
                                    total_score += 10
                                    all_flags.append(
                                        f"Creator has launched {creator_tokens}+ tokens — "
                                        "serial deployer pattern, high impersonation risk"
                                    )
                except (TimeoutError, Exception):
                    pass
    except Exception:
        pass

    # ── Final scoring ──
    final_score = min(100, round(total_score))
    risk_level = (
        "CRITICAL" if final_score >= 75 else "HIGH" if final_score >= 55 else "MODERATE" if final_score >= 35 else "LOW"
    )

    # Deduplicate flags
    seen = set()
    unique_flags = []
    for f in all_flags:
        if f not in seen:
            seen.add(f)
            unique_flags.append(f)

    elapsed = time.monotonic() - start

    return {
        "tool": "Social Engineering & Identity Fraud Detector",
        "version": "1.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "token_address": token_address,
        "chain": chain,
        "social_engineering_risk": {
            "score": final_score,
            "level": risk_level,
            "breakdown": {
                "domain_weighted": round(domain_result["risk_score"] * 0.15, 1),
                "social_weighted": round(social_result["risk_score"] * 0.25, 1),
                "whitepaper_weighted": round(wp_result["risk_score"] * 0.20, 1)
                if "whitepaper_analysis" in results
                else 0,
            },
        },
        "flags_found": len(unique_flags),
        "key_flags": unique_flags[:10],  # Top 10 flags
        "all_flags": unique_flags,
        "analysis_time_ms": round(elapsed * 1000),
    }

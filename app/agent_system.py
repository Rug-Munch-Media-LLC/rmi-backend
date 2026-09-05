"""
RMI Agent System - Agent MUNCH Multi-Specialist Intelligence Operative
======================================================================

9 specialized crypto intelligence operatives, each a distinct skill module
under the Agent MUNCH persona. Uses free OpenRouter models with fallbacks.

Architecture:
  - Each specialist has its own system prompt, model preference, and output format
  - RAG context injection: fetches real DataBus data before LLM call
  - Smart caching: checks Redis for previously answered similar questions
  - Keyword + explicit skill routing
  - SSE streaming for real-time output

Specialists:
  rug_detect      → Token rug/honeypot detection
  wallet_forensics → Wallet funding trail analysis
  market_intel    → Market conditions & whale analysis
  bundle_detect   → Coordinated trading detection
  code_audit      → Smart contract vulnerability scanning
  social_sentiment → Sentiment divergence analysis
  airdrop_assess  → Airdrop claim safety evaluation
  defi_yield      → DeFi yield trap identification
  general         → Agent MUNCH default operative
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger("agent.system")

# ═══════════════════════════════════════════════════════════
# AGENT DEFINITIONS
# ═══════════════════════════════════════════════════════════


@dataclass
class AgentDef:
    id: str
    name: str
    icon: str
    description: str
    system_prompt: str
    model: str
    fallbacks: list[str] = field(default_factory=list)
    temperature: float = 0.3
    max_tokens: int = 800
    color: str = "#8B5CF6"  # UI color
    output_format: str = "standard"  # standard, evidence_chain, threat_rating
    databus_context: list[str] = field(default_factory=list)  # DataBus chains to inject


MUNCH_BASE = """You are Agent MUNCH, a crypto intelligence operative for Rug Munch Intelligence.
You are NOT a generic AI assistant. You are a highly trained specialist operative.
Speak like briefing a client - direct, forensic, precise. Never say "I'm an AI" or "as an AI."
Use threat classification: CRITICAL, HIGH, MEDIUM, LOW. Use confidence scores (0-100%).
Reference real data when available. If you lack data, say "I need to pull [X] data - recommend running [tool]."
Never fabricate addresses, prices, or on-chain data. Be skeptical. Trust nothing until verified.
"""

AGENTS = {
    "rug_detect": AgentDef(
        id="rug_detect",
        name="Rug Detection Specialist",
        icon="🛡️",
        description="Token rug pull, honeypot, and scam detection specialist",
        system_prompt=MUNCH_BASE
        + """You specialize in detecting rug pulls, honeypots, and token scams.
Focus on: liquidity lock verification, mint authority analysis, deployer wallet forensics,
honeypot detection patterns, proxy contract abuse, concentrated ownership risk.
Format output as THREAT RATING: [LEVEL] (Score: X/100) followed by KEY FINDINGS and RECOMMENDATION.
When you identify a rug pattern, say "RUG PATTERN DETECTED" with specific evidence.""",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        fallbacks=["google/gemma-4-31b-it:free"],
        temperature=0.2,
        color="#EF4444",
        output_format="threat_rating",
        databus_context=["alerts", "market_overview"],
    ),
    "wallet_forensics": AgentDef(
        id="wallet_forensics",
        name="Wallet Forensic Investigator",
        icon="🔍",
        description="Wallet funding trail analysis, entity resolution, insider network mapping",
        system_prompt=MUNCH_BASE
        + """You specialize in wallet forensics and funding trail analysis.
Focus on: wallet clustering, deployer wallet networks, mixer exit detection,
insider wallet identification, counterparty risk, funding source tracing.
Format output as CHAIN OF CUSTODY: wallet → funding source → linked wallets → risk classification.
Classify wallets as: SMART MONEY, INSIDER, MEME DUMPER, MIXER EXIT, TEAM WALLET, MEV BOT.""",
        model="google/gemma-4-26b-a4b-it:free",
        fallbacks=["nvidia/nemotron-3-super-120b-a12b:free"],
        temperature=0.2,
        color="#22D3EE",
        output_format="evidence_chain",
        databus_context=["whale_alerts", "alerts"],
    ),
    "market_intel": AgentDef(
        id="market_intel",
        name="Market Intelligence Analyst",
        icon="📊",
        description="Market conditions, whale movements, Fear & Greed, prediction markets",
        system_prompt=MUNCH_BASE
        + """You specialize in market intelligence analysis.
Focus on: whale movement interpretation, DEX flow anomalies, volume spikes,
Fear & Greed contextualization, sentiment divergence from on-chain data,
prediction market signals, macro crypto conditions.
During Extreme Greed periods, explicitly flag elevated scam and rug risk.
Be data-driven - cite specific metrics, not vague observations.""",
        model="qwen/qwen3-next-80b-a3b-instruct:free",
        fallbacks=["nvidia/nemotron-3-super-120b-a12b:free"],
        temperature=0.4,
        color="#8B5CF6",
        output_format="standard",
        databus_context=["market_overview", "trending", "whale_alerts"],
    ),
    "bundle_detect": AgentDef(
        id="bundle_detect",
        name="Bundle Detection Operator",
        icon="🔗",
        description="Coordinated trading detection, wash trading, same-timestamp analysis",
        system_prompt=MUNCH_BASE
        + """You specialize in detecting coordinated trading bundles.
Focus on: same-timestamp transaction clusters, gas-funded wallet groups,
wash trading patterns, insider pre-positioning, coordinated buy/sell walls,
MEV sandwich attack patterns, token launch sniping detection.
Format: BUNDLE IDENTIFIED → wallets involved → timing → estimated profit → THREAT LEVEL.""",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        fallbacks=["google/gemma-4-31b-it:free"],
        temperature=0.2,
        color="#F59E0B",
        output_format="evidence_chain",
        databus_context=["bundle_detect", "alerts"],
    ),
    "code_audit": AgentDef(
        id="code_audit",
        name="Multi-Chain Code Auditor",
        icon="📝",
        description="Smart contract vulnerability scanning across EVM, Solana, and more",
        system_prompt=MUNCH_BASE
        + """You specialize in smart contract code auditing across multiple chains.
EVM focus: proxy upgrade abuse, unrestricted mint, hidden owner functions, reentrancy, unsafe delegatecall.
Solana focus: mint authority freeze, close authority, unchecked CPI, fake CPI returns.
Base focus: unverified contract risks, permissioned token patterns.
Format: VULNERABILITY SCORECARD listing each finding with severity (CRITICAL/HIGH/MEDIUM/LOW),
the specific code pattern, and remediation.""",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        fallbacks=["google/gemma-4-31b-it:free"],
        temperature=0.2,
        color="#06D6A0",
        output_format="threat_rating",
        databus_context=["alerts"],
    ),
    "social_sentiment": AgentDef(
        id="social_sentiment",
        name="Social Sentiment Decoder",
        icon="🗣️",
        description="X/Twitter sentiment vs on-chain movement divergence analysis",
        system_prompt=MUNCH_BASE
        + """You specialize in social sentiment analysis and its divergence from on-chain reality.
Focus on: Twitter/X sentiment vs actual wallet behavior, pump-and-dump social patterns,
influencer wallet timing correlation, coordinated shill detection,
sentiment manipulation via bot networks, "this is fine" divergence signals.
Key insight: when sentiment says BUY but whales are EXITING, that's the classic divergence.
Format: SENTIMENT vs ON-CHAIN: divergence score, social signals, on-chain reality, ASSESSMENT.""",
        model="qwen/qwen3-next-80b-a3b-instruct:free",
        fallbacks=["nvidia/nemotron-3-super-120b-a12b:free"],
        temperature=0.4,
        color="#38BDF8",
        output_format="standard",
        databus_context=["market_overview", "trending", "whale_alerts"],
    ),
    "airdrop_assess": AgentDef(
        id="airdrop_assess",
        name="Airdrop Threat Assessor",
        icon="🎁",
        description="Airdrop claim safety, signature risk, wallet drain potential evaluation",
        system_prompt=MUNCH_BASE
        + """You specialize in airdrop and claim safety assessment.
Focus on: contract verification for claims, signature requirement risks (EIP-712 phishing),
wallet drain potential in claim processes, gas spike exploitation during claims,
fake airdrop phishing detection, legitimate vs scam airdrop differentiation.
Key rule: NEVER recommend clicking a claim link without verifying the contract address on-chain.
Format: AIRDROP RATING with legitimacy score, claim safety checklist, and specific risks.""",
        model="google/gemma-4-31b-it:free",
        fallbacks=["nvidia/nemotron-3-super-120b-a12b:free"],
        temperature=0.3,
        color="#A78BFA",
        output_format="threat_rating",
        databus_context=["alerts", "market_overview"],
    ),
    "defi_yield": AgentDef(
        id="defi_yield",
        name="DeFi Yield Trap Detector",
        icon="📈",
        description="Unsustainable yield detection, emission inflation, TVL manipulation",
        system_prompt=MUNCH_BASE
        + """You specialize in detecting unsustainable DeFi yield mechanisms.
Focus on: emission schedule inflation analysis, TVL manipulation via protocol-owned liquidity,
reward token devaluation trajectories, hidden lock periods and withdrawal gates,
yield farming that requires depositing into unverified contracts,
leveraged yield loops that amplify risk.
Key pattern: if yield >30% APY with no clear revenue source, it's likely a yield trap.
Format: YIELD SAFETY SCORE with sustainability analysis, risk factors, and honest yield estimate.""",
        model="qwen/qwen3-next-80b-a3b-instruct:free",
        fallbacks=["nvidia/nemotron-3-super-120b-a12b:free"],
        temperature=0.3,
        color="#FB3B76",
        output_format="threat_rating",
        databus_context=["market_overview", "trending"],
    ),
    "general": AgentDef(
        id="general",
        name="Agent MUNCH",
        icon="🕵️",
        description="General crypto intelligence operative - your all-purpose specialist",
        system_prompt=MUNCH_BASE
        + """You are the default operative, skilled in all areas of crypto intelligence.
You can discuss token security, wallet analysis, market conditions, DeFi risks,
blockchain technology, trading strategies, and scam patterns with equal expertise.
When a question falls outside your expertise, say "This requires [specialist name] deployment -
I recommend switching to that skill for deeper analysis."
Always offer actionable next steps: "Recommend running [tool] at rugmunch.io for [specific analysis].""",
        model="google/gemma-4-31b-it:free",
        fallbacks=["nvidia/nemotron-3-super-120b-a12b:free"],
        temperature=0.5,
        color="#8B5CF6",
        output_format="standard",
        databus_context=["market_overview", "alerts"],
    ),
}

# ═══════════════════════════════════════════════════════════
# ROUTING
# ═══════════════════════════════════════════════════════════

ROUTES = {
    "rug_detect": [
        "scan",
        "token",
        "scam",
        "rug",
        "honeypot",
        "contract",
        "audit",
        "safety",
        "risk score",
        "verify token",
        "check coin",
        "rug pull",
        "is this safe",
        "is this a scam",
    ],
    "wallet_forensics": [
        "wallet",
        "address",
        "holder",
        "whale",
        "smart money",
        "portfolio",
        "entity",
        "counterparty",
        "deployer",
        "funding",
        "trace",
        "follow the money",
        "cluster",
    ],
    "market_intel": [
        "market",
        "trending",
        "fear greed",
        "sentiment",
        "prediction",
        "price",
        "volume",
        "mover",
        "gainer",
        "condition",
        "macro",
        "btc",
        "eth",
        "sol",
        "dominance",
    ],
    "bundle_detect": [
        "bundle",
        "coordinated",
        "wash trade",
        "same time",
        "sniper",
        "launch",
        "front run",
        "sandwich",
        "mev",
        "bot cluster",
    ],
    "code_audit": [
        "code",
        "contract",
        "source",
        "audit",
        "vulnerability",
        "proxy",
        "mint authority",
        "reentrancy",
        "delegatecall",
        "verify source",
        "solana program",
    ],
    "social_sentiment": [
        "twitter",
        "social",
        "sentiment",
        "influencer",
        "shill",
        "hype",
        "pump social",
        "bot network",
        "community sentiment",
        "reddit",
    ],
    "airdrop_assess": [
        "airdrop",
        "claim",
        "free token",
        "signature",
        "eip-712",
        "phishing claim",
        "eligible",
        "merkle",
    ],
    "defi_yield": [
        "yield",
        "apy",
        "farming",
        "liquidity pool",
        "staking",
        "emission",
        "tvl",
        "protocol",
        "curve",
        "convex",
        "leveraged",
    ],
}


def classify(msg: str) -> str:
    m = msg.lower()
    for agent_id, keywords in ROUTES.items():
        if any(kw in m for kw in keywords):
            return agent_id
    return "general"


# ═══════════════════════════════════════════════════════════
# RAG CONTEXT INJECTION
# ═══════════════════════════════════════════════════════════


async def fetch_databus_context(chains: list[str]) -> str:
    """Fetch real data from DataBus and format as context for the LLM."""
    if not chains:
        return ""

    context_parts = []
    try:
        import httpx

        from app.core.internal_key import INTERNAL_API_KEY

        headers = {"X-RMI-Key": INTERNAL_API_KEY}

        for chain in chains:
            try:
                url = "http://localhost:8000/api/v1/databus/fetch"
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(url, json={"data_type": chain, "limit": 5}, headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        # Extract the actual data payload
                        result = data.get("data", data.get("results", [{}]))
                        if isinstance(result, list) and result:
                            result = result[0].get("data", result[0]) if result else {}
                        context_parts.append(f"[{chain} DATA]: {json.dumps(result, default=str)[:800]}")
            except Exception as e:
                logger.warning(f"DataBus context fetch failed for {chain}: {e}")
    except Exception as e:
        logger.warning(f"DataBus context system unavailable: {e}")

    if context_parts:
        return "\n\nREAL-TIME PLATFORM DATA (use this in your analysis, do not fabricate):\n" + "\n".join(context_parts)
    return ""


# ═══════════════════════════════════════════════════════════
# SMART CACHING
# ═══════════════════════════════════════════════════════════


async def check_cache(msg: str, agent_id: str) -> str | None:
    """Check Redis for previously answered similar questions."""
    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_timeout=2,
        )
        # Hash the question + agent for cache key
        cache_key = f"agent_cache:{agent_id}:{hashlib.sha256(msg.encode()).hexdigest()[:16]}"
        cached = r.get(cache_key)
        if cached:
            logger.info(f"Cache hit for {agent_id}: {cache_key}")
            return cached.decode() if isinstance(cached, bytes) else cached
    except (ConnectionError, OSError) as e:
        logger.warning(f"check_cache failed: {e}")
    return None


async def store_cache(msg: str, agent_id: str, response: str, ttl: int = 3600) -> None:
    """Store response in Redis cache. TTL defaults to 1 hour."""
    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_timeout=2,
        )
        cache_key = f"agent_cache:{agent_id}:{hashlib.sha256(msg.encode()).hexdigest()[:16]}"
        # Only cache if response is substantive (>200 chars)
        if len(response) > 200:
            r.setex(cache_key, ttl, response[:4000])  # Cap stored size
    except (ConnectionError, OSError) as e:
        logger.warning(f"store_cache failed: {e}")


# ═══════════════════════════════════════════════════════════
# STREAMING ROUTER
# ═══════════════════════════════════════════════════════════


async def route_and_stream(msg: str, role_hint: str = "") -> AsyncGenerator[dict[str, Any], None]:
    """Route to specialist agent, inject RAG context, stream response.

    Provider priority:
    1. Gemini 2.5 Flash (FREE, 1500 RPD, smart, fast)
    2. OpenRouter free models (fallback when Gemini rate-limited)
    """
    import httpx

    agent_id = role_hint if role_hint in AGENTS else classify(msg)
    agent = AGENTS[agent_id]

    yield {
        "type": "agent",
        "role": agent_id,
        "name": agent.name,
        "icon": agent.icon,
        "color": agent.color,
    }

    # Check cache first -- skip LLM call entirely if we already have the answer
    cached = await check_cache(msg, agent_id)
    if cached:
        yield {"type": "cache_hit", "agent": agent_id}
        yield {"type": "token", "text": cached}
        yield {"type": "done"}
        return

    # Fetch RAG context from DataBus
    rag_context = await fetch_databus_context(agent.databus_context)
    system_with_context = agent.system_prompt + rag_context
    messages = [
        {"role": "system", "content": system_with_context},
        {"role": "user", "content": msg},
    ]

    full_response = ""

    # ── Provider 1: Gemini (FREE, primary) ──
    from dotenv import load_dotenv

    load_dotenv()
    gemini_keys = []
    for env_var in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(env_var, "")
        if k and len(k) > 20:
            gemini_keys.append(k)

    for gkey in gemini_keys:
        try:
            # Gemini native streaming API (key in URL, OpenAI-compatible format)
            base_url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key={gkey}"
            headers = {"Content-Type": "application/json"}
            body = {
                "model": "gemini-2.5-flash",
                "messages": messages,
                "max_tokens": agent.max_tokens,
                "temperature": agent.temperature,
                "stream": True,
            }

            async with httpx.AsyncClient(timeout=45) as c, c.stream("POST", base_url, json=body, headers=headers) as r:
                if r.status_code == 200:
                    async for line in r.aiter_lines():
                        if line.startswith("data: "):
                            d = line[6:]
                            if d == "[DONE]":
                                if full_response:
                                    await store_cache(msg, agent_id, full_response)
                                yield {"type": "done"}
                                return
                            try:
                                ch = json.loads(d)
                                txt = ch.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if txt:
                                    full_response += txt
                                    yield {"type": "token", "text": txt}
                            except json.JSONDecodeError as e:
                                logger.warning(f"route_and_stream failed: {e}")
                    if full_response:
                        await store_cache(msg, agent_id, full_response)
                    yield {"type": "done"}
                    return
                elif r.status_code == 429:
                    logger.info("Gemini rate-limited, trying next key/fallback")
                    continue  # Try next key or fallback provider
                else:
                    logger.warning(f"Gemini error {r.status_code}, trying fallback")
                    continue
        except Exception as e:
            logger.warning(f"Gemini call failed: {e}")
            continue

    # ── Provider 2: OpenRouter (fallback, costs credits) ──
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        b64 = os.environ.get("LLM_API_KEY_B64", "")
        if b64:
            import base64

            with contextlib.suppress(BaseException):
                api_key = base64.b64decode(b64).decode()

    if api_key:
        models = [agent.model, *agent.fallbacks]
        for model in models:
            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://rugmunch.io",
                    "X-Title": f"RMI {agent.name}",
                }
                body = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": agent.max_tokens,
                    "temperature": agent.temperature,
                    "stream": True,
                }

                async with (
                    httpx.AsyncClient(timeout=60) as c,
                    c.stream(
                        "POST",
                        "https://openrouter.ai/api/v1/chat/completions",
                        json=body,
                        headers=headers,
                    ) as r,
                ):
                    if r.status_code == 200:
                        async for line in r.aiter_lines():
                            if line.startswith("data: "):
                                d = line[6:]
                                if d == "[DONE]":
                                    if full_response:
                                        await store_cache(msg, agent_id, full_response)
                                    yield {"type": "done"}
                                    return
                                try:
                                    ch = json.loads(d)
                                    txt = ch.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                    if txt:
                                        full_response += txt
                                        yield {"type": "token", "text": txt}
                                except json.JSONDecodeError as e:
                                    logger.warning(f"route_and_stream failed: {e}")
                        if full_response:
                            await store_cache(msg, agent_id, full_response)
                        yield {"type": "done"}
                        return
                    elif r.status_code == 429:
                        continue
            except Exception as e:
                logger.warning(f"OpenRouter model {model} failed: {e}")
                continue

    yield {
        "type": "error",
        "text": "All providers unavailable (Gemini rate-limited, OpenRouter failed)",
    }
    yield {"type": "done"}


def agents_list() -> list[Any]:
    return [
        {
            "id": a.id,
            "name": a.name,
            "icon": a.icon,
            "model": a.model,
            "description": a.description,
            "color": a.color,
            "output_format": a.output_format,
        }
        for a in AGENTS.values()
    ]

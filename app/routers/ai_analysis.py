"""
Qwen-Powered AI Contract Analysis for RugMaps.
Advanced anomaly detection with AI deep analysis.

Endpoints:
  POST /api/v1/ai/contract-analysis
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai-analysis"])


# ── Request/Response Models ──────────────────────────────────────


class ContractAnalysisRequest(BaseModel):
    chain: str = Field(..., description="Blockchain (solana, ethereum, bsc, base, etc.)")
    address: str = Field(..., description="Token contract address")
    holder_data: dict[str, Any] | None = Field(default=None, description="Holder distribution data")
    scanner_data: dict[str, Any] | None = Field(default=None, description="Scanner results (risk scores, LP analysis)")


class AnomalyReport(BaseModel):
    pattern: str
    severity: str  # critical, high, medium, low
    description: str
    affected_wallets: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ContractAnalysisResponse(BaseModel):
    token_address: str
    chain: str
    risk_score: int
    risk_level: str
    ai_summary: str
    anomalies: list[AnomalyReport] = Field(default_factory=list)
    identified_clusters: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    analysis_source: str = "rule-based"  # "ai" or "rule-based"
    ai_model: str = ""


# ── AI Provider Configuration ────────────────────────────────────

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")

OPENROUTER_KEY = (
    os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("LLM_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen/qwen-2.5-72b-instruct:free")

# ── Direct llama.cpp config (NO litellm proxy, NO paid APIs) ──
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")
DEEPSEEK_PORT = 8086  # deepseek-analysis (Qwen3.5-9B-Abliterated, non-reasoning; legacy alias)
QWEN_PORT = 8085  # qwen-chat (Qwen3.5-4B-Abliterated, fast)

# Cache
_analysis_cache: dict[str, dict[str, Any]] = {}
CACHE_TTL = 3600


# ── Prompt Builder ───────────────────────────────────────────────


def _build_analysis_prompt(
    chain: str,
    address: str,
    holder_data: dict[str, Any] | None,
    scanner_data: dict[str, Any] | None,
) -> str:
    """Build a comprehensive analysis prompt for the AI."""

    holder_section = ""
    if holder_data:
        holders = holder_data.get("holders", holder_data.get("nodes", []))
        total_holders = holder_data.get("total_holders", len(holders))
        top_holders = holders[:15] if isinstance(holders, list) else []

        holder_lines = []
        for h in top_holders:
            if isinstance(h, dict):
                addr = str(h.get("address") or h.get("id") or "?")[:16]
                pct = h.get("pct", h.get("supply_pct", h.get("percentage", "?")))
                label = h.get("label", h.get("entity", "unknown"))
                risk = h.get("risk", h.get("risk_score", "?"))
                holder_lines.append(f"  - {addr}... | {pct}% | label: {label} | risk: {risk}/100")

        concentration = holder_data.get("top_10_concentration", 0)
        fresh_pct = holder_data.get("fresh_wallets_pct", 0)
        bundled_pct = holder_data.get("bundled_wallets_pct", 0)

        holder_section = f"""
HOLDER DISTRIBUTION:
- Total holders: {total_holders}
- Top 10 concentration: {concentration}%
- Fresh wallets (<24h): {fresh_pct}%
- Bundled wallets: {bundled_pct}%
- Top Holders:
{chr(10).join(holder_lines) if holder_lines else "  No holder data"}
"""

    scanner_section = ""
    if scanner_data:
        safety_score = scanner_data.get("safety_score", scanner_data.get("risk_score", "?"))
        risk_level = scanner_data.get("risk_level", "?")
        risk_flags = scanner_data.get("risk_flags", [])
        green_flags = scanner_data.get("green_flags", [])
        lp = scanner_data.get("liquidity_usd", scanner_data.get("liquidity", "?"))
        mcap = scanner_data.get("market_cap_usd", scanner_data.get("market_cap", "?"))
        verified = scanner_data.get("contract_verified", scanner_data.get("verified", "?"))
        modules = scanner_data.get("modules_run", [])

        scanner_section = f"""
SCANNER RESULTS:
- Safety score: {safety_score}/100
- Risk level: {risk_level}
- Liquidity: ${lp}
- Market cap: ${mcap}
- Contract verified: {verified}
- Risk flags: {json.dumps(risk_flags) if risk_flags else "None"}
- Green flags: {json.dumps(green_flags) if green_flags else "None"}
- Modules run: {json.dumps(modules) if modules else "Unknown"}
"""

    prompt = f"""You are RugMunch Media's elite blockchain forensic analyst. Your job is to detect scams, manipulation patterns, and security risks in token contracts using on-chain data.

## TASK
Analyze token {address} on {chain} and produce a comprehensive security assessment.

## DATA PROVIDED
{holder_section}
{scanner_section}

## SUSPICIOUS PATTERNS TO CHECK
1. **Bundled launches**: 50+ wallets funded from same source address
2. **Sniper clusters**: Wallets that bought in the same block with high gas
3. **Wash trading circles**: Wallets trading tokens between each other to fake volume
4. **Dev wallet connections**: Funds tracing back to deployer address
5. **Concentration risk**: Top holders controlling excessive supply
6. **Liquidity manipulation**: Unusual LP patterns, low liquidity
7. **Contract backdoors**: Mint functions, pause, blacklist, modify tax
8. **Insider clusters**: Coordinated buying/selling patterns

## OUTPUT FORMAT
Return ONLY valid JSON (no markdown, no code blocks) with these fields:
{{
  "risk_score": <0-100>,
  "risk_level": "<SAFE|LOW|MEDIUM|HIGH|CRITICAL>",
  "summary": "<2-4 sentence comprehensive analysis>",
  "anomalies": [
    {{
      "pattern": "<pattern name>",
      "severity": "<critical|high|medium|low>",
      "description": "<detailed explanation>",
      "affected_wallets": ["<address1>", "<address2>"],
      "confidence": <0-100>
    }}
  ],
  "clusters": [
    {{
      "name": "<cluster name>",
      "type": "<bundled|sniper|dev|insider|wash_trading>",
      "wallet_count": <number>,
      "supply_percentage": <number>,
      "risk": "<HIGH|MEDIUM|LOW>"
    }}
  ],
  "recommendations": [
    "<actionable recommendation 1>",
    "<actionable recommendation 2>"
  ]
}}

If you find NO anomalies, set anomalies to empty array and recommendations to ["No suspicious patterns detected. Token appears normal based on available data."]."""

    return prompt


# ── Rule-Based Anomaly Detection (Fallback) ──────────────────────


def _detect_anomalies_rule_based(
    holder_data: dict[str, Any] | None,
    scanner_data: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, str]:
    """Run rule-based anomaly detection on the data."""
    anomalies: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []
    risk_score = 0

    # ── Scanner-based checks ──
    if scanner_data and isinstance(scanner_data, dict):
        # Risk flags
        risk_flags = scanner_data.get("risk_flags", [])
        if risk_flags and isinstance(risk_flags, list) and len(risk_flags) > 0:
            risk_score += min(len(risk_flags) * 10, 40)
            for flag in risk_flags[:8]:
                anomalies.append(
                    {
                        "pattern": "Scanner Risk Flag",
                        "severity": "high",
                        "description": str(flag),
                        "affected_wallets": [],
                        "confidence": 80.0,
                    }
                )

        # Safety score
        safety = scanner_data.get("safety_score", 50)
        if isinstance(safety, (int, float)) and safety < 30:
            risk_score += 30
            anomalies.append(
                {
                    "pattern": "Low Safety Score",
                    "severity": "critical" if safety < 15 else "high",
                    "description": f"Token safety score is {safety}/100 - indicates serious risks",
                    "affected_wallets": [],
                    "confidence": 90.0,
                }
            )

        # Liquidity
        lp = scanner_data.get("liquidity_usd", scanner_data.get("liquidity", 0))
        if isinstance(lp, (int, float)) and lp < 5000 and lp > 0:
            risk_score += 15
            anomalies.append(
                {
                    "pattern": "Low Liquidity",
                    "severity": "high",
                    "description": f"Liquidity is only ${lp:,.0f} - easy to manipulate price",
                    "affected_wallets": [],
                    "confidence": 85.0,
                }
            )

        # Contract verification
        verified = scanner_data.get("contract_verified", scanner_data.get("verified", None))
        if verified is False:
            risk_score += 20
            anomalies.append(
                {
                    "pattern": "Unverified Contract",
                    "severity": "high",
                    "description": "Contract source code is not verified - cannot audit functionality",
                    "affected_wallets": [],
                    "confidence": 95.0,
                }
            )

        # Honeypot indicators
        honeypot = scanner_data.get("honeypot_indicators", scanner_data.get("honeypot", []))
        if honeypot and isinstance(honeypot, list) and len(honeypot) > 0:
            risk_score += 25
            anomalies.append(
                {
                    "pattern": "Honeypot Indicators",
                    "severity": "critical",
                    "description": f"Honeypot signs detected: {', '.join(str(h) for h in honeypot)}",
                    "affected_wallets": [],
                    "confidence": 90.0,
                }
            )

        # Tax analysis
        sell_tax = scanner_data.get("tax_sell", scanner_data.get("sell_tax", 0))
        if isinstance(sell_tax, (int, float)) and sell_tax > 10:
            risk_score += min(int(sell_tax), 30)
            anomalies.append(
                {
                    "pattern": "High Sell Tax",
                    "severity": "critical" if sell_tax > 50 else "high",
                    "description": f"Sell tax is {sell_tax}% - may prevent selling",
                    "affected_wallets": [],
                    "confidence": 90.0,
                }
            )

    # ── Holder-based checks ──
    if holder_data and isinstance(holder_data, dict):
        holders_list = holder_data.get("holders", holder_data.get("nodes", []))
        if isinstance(holders_list, list):
            # Bundled wallets detection
            bundled_pct = holder_data.get("bundled_wallets_pct", 0)
            if isinstance(bundled_pct, (int, float)) and bundled_pct > 10:
                risk_score += min(int(bundled_pct * 1.5), 35)
                anomalies.append(
                    {
                        "pattern": "Bundled Launch",
                        "severity": "critical" if bundled_pct > 25 else "high",
                        "description": f"{bundled_pct:.1f}% of wallets appear bundled (shared funding source)",
                        "affected_wallets": [],
                        "confidence": min(bundled_pct * 2, 95),
                    }
                )
                clusters.append(
                    {
                        "name": "Bundled Wallet Cluster",
                        "type": "bundled",
                        "wallet_count": int(len(holders_list) * bundled_pct / 100),
                        "supply_percentage": round(bundled_pct, 1),
                        "risk": "HIGH",
                    }
                )

            # Fresh wallets
            fresh_pct = holder_data.get("fresh_wallets_pct", 0)
            if isinstance(fresh_pct, (int, float)) and fresh_pct > 20:
                risk_score += min(int(fresh_pct * 0.8), 25)
                anomalies.append(
                    {
                        "pattern": "Fresh Wallet Cluster",
                        "severity": "high" if fresh_pct > 35 else "medium",
                        "description": f"{fresh_pct:.1f}% of wallets created within 24h of token launch - likely artificial",
                        "affected_wallets": [],
                        "confidence": min(fresh_pct * 2, 90),
                    }
                )

            # Top 10 concentration
            concentration = holder_data.get("top_10_concentration", 0)
            if isinstance(concentration, (int, float)) and concentration > 50:
                risk_score += min(int(concentration * 0.4), 30)
                anomalies.append(
                    {
                        "pattern": "Concentration Risk",
                        "severity": "critical" if concentration > 80 else "high",
                        "description": f"Top 10 holders control {concentration:.1f}% of supply",
                        "affected_wallets": [],
                        "confidence": 95.0,
                    }
                )

            # Dev wallet probability
            dev_prob = holder_data.get("dev_wallet_probability", 0)
            if isinstance(dev_prob, (int, float)) and dev_prob > 50:
                risk_score += 15
                anomalies.append(
                    {
                        "pattern": "Dev-Connected Wallets",
                        "severity": "high",
                        "description": f"{dev_prob:.0f}% probability top holders are connected to developer",
                        "affected_wallets": [],
                        "confidence": dev_prob,
                    }
                )

            # Sniper detection
            sniper_count = holder_data.get("sniper_count", holder_data.get("snipers", 0))
            if isinstance(sniper_count, (int, float)) and sniper_count > 3:
                risk_score += min(int(sniper_count * 3), 30)
                anomalies.append(
                    {
                        "pattern": "Sniper Cluster",
                        "severity": "high",
                        "description": f"{int(sniper_count)} sniper bots detected buying in first block",
                        "affected_wallets": [],
                        "confidence": 85.0,
                    }
                )

            # Exchange funded
            exchange_pct = holder_data.get("exchange_funded_pct", 0)
            if isinstance(exchange_pct, (int, float)) and exchange_pct > 20:
                anomalies.append(
                    {
                        "pattern": "Exchange-Funded Pattern",
                        "severity": "medium",
                        "description": f"{exchange_pct:.1f}% of wallets funded from CEX - possible bot farm",
                        "affected_wallets": [],
                        "confidence": 70.0,
                    }
                )

    # ── Cap risk score ──
    risk_score = min(100, max(0, risk_score))

    # ── Determine risk level ──
    if risk_score >= 80:
        risk_level = "CRITICAL"
    elif risk_score >= 60:
        risk_level = "HIGH"
    elif risk_score >= 40:
        risk_level = "MEDIUM"
    elif risk_score >= 20:
        risk_level = "LOW"
    else:
        risk_level = "SAFE"

    return anomalies, clusters, risk_score, risk_level


# ── AI Analysis (Qwen via Ollama or OpenRouter) ──────────────────


async def _call_qwen_ollama(prompt: str) -> dict[str, Any] | None:
    """Call Qwen via local Ollama instance."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a blockchain security expert. Return ONLY valid JSON, no markdown, no explanations outside the JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 1500},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                return _parse_ai_response(content)
            logger.warning(f"Ollama returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"Ollama call failed: {e}")
    return None


async def _call_qwen_openrouter(prompt: str) -> dict[str, Any] | None:
    """Call Qwen via OpenRouter."""
    if not OPENROUTER_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://rugmunch.io",
                    "X-Title": "RugMaps AI Analysis",
                },
                json={
                    "model": QWEN_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a blockchain forensic expert. Return ONLY valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1500,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return _parse_ai_response(content)
            logger.warning(f"OpenRouter returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"OpenRouter call failed: {e}")
    return None


def _parse_ai_response(content: str) -> dict[str, Any] | None:
    """Parse the AI's JSON response."""
    if not content:
        return None
    try:
        # Strip markdown code blocks
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove opening and closing fences
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse AI response: {e}")
        # Try to find JSON block
        import re

        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return None


async def _run_ai_analysis(
    chain: str,
    address: str,
    holder_data: dict[str, Any] | None,
    scanner_data: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    """Try AI analysis, falling through providers: LiteLLM → Ollama → OpenRouter."""
    prompt = _build_analysis_prompt(chain, address, holder_data, scanner_data)

    # ── Primary: llama.cpp deepseek-analysis (local, reasoning) ──
    result = await _call_llama_analysis(prompt, DEEPSEEK_PORT, "deepseek-analysis")
    if result:
        return result, "llama/deepseek-analysis"

    # ── Fallback: llama.cpp qwen-chat (local, fast) ──
    result = await _call_llama_analysis(prompt, QWEN_PORT, "qwen-chat")
    if result:
        return result, "llama/qwen-chat"

    return None, ""


async def _call_llama_analysis(prompt: str, port: int, model: str) -> dict[str, Any] | None:
    """Call a local llama.cpp model directly. NO litellm, NO paid APIs."""
    import re

    def _strip_think(text: str) -> str:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"http://{LLAMA_HOST}:{port}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a blockchain forensic expert. Return ONLY valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1500,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return _parse_ai_response(_strip_think(content))
            logger.warning("llama_non_200", status=resp.status_code, body=resp.text[:200], model=model)
    except Exception as e:
        logger.warning("llama_analysis_failed", error=str(e), model=model)
    return None


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/contract-analysis")
async def contract_analysis(request: ContractAnalysisRequest) -> ContractAnalysisResponse:
    """AI-powered contract analysis with anomaly detection.

    Accepts holder/distribution data and scanner results, runs AI analysis
    via Qwen (Ollama or OpenRouter), and falls back to rule-based detection
    if AI is unavailable.

    Returns a comprehensive risk report with identified anomalies and clusters.
    """
    chain = request.chain.lower()
    address = request.address
    holder_data = request.holder_data
    scanner_data = request.scanner_data

    # ── Run rule-based detection first (fast, always works) ──
    rule_anomalies, rule_clusters, rule_score, rule_level = _detect_anomalies_rule_based(holder_data, scanner_data)

    # ── Try AI analysis for deeper insights ──
    ai_result, ai_model = await _run_ai_analysis(chain, address, holder_data, scanner_data)

    if ai_result:
        # Merge AI insights with rule-based detection
        ai_anomalies_raw = ai_result.get("anomalies", [])
        ai_clusters_raw = ai_result.get("clusters", [])
        ai_score = ai_result.get("risk_score", rule_score)
        ai_level = ai_result.get("risk_level", rule_level)
        ai_summary = ai_result.get("summary", "")
        ai_recommendations = ai_result.get("recommendations", [])

        # Convert AI anomalies to standard format
        anomalies = [
            AnomalyReport(
                pattern=a.get("pattern", "Unknown"),
                severity=a.get("severity", "medium"),
                description=a.get("description", ""),
                affected_wallets=a.get("affected_wallets", []),
                confidence=float(a.get("confidence", 50)),
            )
            for a in ai_anomalies_raw
        ]

        # Add rule-based anomalies that AI didn't catch
        ai_patterns = {a.get("pattern", "") for a in ai_anomalies_raw}
        for ra in rule_anomalies:
            if ra["pattern"] not in ai_patterns:
                anomalies.append(
                    AnomalyReport(
                        pattern=ra["pattern"],
                        severity=ra["severity"],
                        description=ra["description"],
                        affected_wallets=ra.get("affected_wallets", []),
                        confidence=ra.get("confidence", 70),
                    )
                )

        clusters = list(ai_clusters_raw)
        if not clusters:
            clusters = list(rule_clusters)

        recommendations = list(ai_recommendations)
        if not recommendations:
            recommendations = _generate_recommendations(rule_score)

        risk_score = max(ai_score, rule_score)
        risk_level = ai_level

        return ContractAnalysisResponse(
            token_address=address,
            chain=chain,
            risk_score=risk_score,
            risk_level=risk_level,
            ai_summary=ai_summary,
            anomalies=anomalies,
            identified_clusters=clusters,
            recommendations=recommendations,
            analysis_source="ai",
            ai_model=ai_model,
        )

    # ── Fallback: rule-based only ──
    return ContractAnalysisResponse(
        token_address=address,
        chain=chain,
        risk_score=rule_score,
        risk_level=rule_level,
        ai_summary=_build_rule_summary(rule_score, rule_level, rule_anomalies),
        anomalies=[AnomalyReport(**a) for a in rule_anomalies],
        identified_clusters=rule_clusters,
        recommendations=_generate_recommendations(rule_score),
        analysis_source="rule-based",
        ai_model="",
    )


def _generate_recommendations(risk_score: int) -> list[str]:
    """Generate recommendations based on risk level."""
    if risk_score >= 80:
        return [
            "⚠️ DO NOT INVEST - multiple critical risk indicators detected",
            "This token shows strong scam characteristics - avoid any interaction",
            "Report this token to community scam databases",
        ]
    elif risk_score >= 60:
        return [
            "Exercise extreme caution - high risk detected",
            "Wait for contract audit and verification before considering",
            "Monitor liquidity and holder distribution for 48h before any action",
        ]
    elif risk_score >= 40:
        return [
            "Proceed with caution - some suspicious patterns present",
            "Use a small test transaction before committing significant funds",
            "Verify contract is verified and liquidity is locked",
        ]
    elif risk_score >= 20:
        return [
            "Token appears relatively normal with minor concerns",
            "Standard due diligence recommended",
            "Check community sentiment and social presence",
        ]
    else:
        return [
            "No significant risks detected from available data",
            "Standard trading precautions apply",
        ]


def _build_rule_summary(risk_score: int, risk_level: str, anomalies: list[dict[str, Any]]) -> str:
    """Build a human-readable summary from rule-based detection."""
    count = len(anomalies)
    if count == 0:
        return "No suspicious patterns detected from rule-based analysis."
    return (
        f"Rule-based analysis found {count} suspicious pattern(s). "
        f"Risk score: {risk_score}/100 ({risk_level}). "
        f"Key concerns: {', '.join(a['pattern'] for a in anomalies[:3])}."
    )


# ── Health check ─────────────────────────────────────────────────


@router.get("/ai-health")
async def ai_analysis_health() -> dict[str, Any]:
    """Check AI analysis availability across all providers."""
    ollama_ok = False
    openrouter_ok = bool(OPENROUTER_KEY)
    litellm_ok = False

    # Check LiteLLM
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{LITELLM_BASE_URL.replace('/v1', '')}/health")
            litellm_ok = resp.status_code == 200
    except Exception as e:
        logger.warning("ai_health_litellm_check_failed", error=str(e))

    # Check Ollama
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                OLLAMA_URL.replace("/api/chat", "/api/tags")
                if "/api/chat" in OLLAMA_URL
                else "http://localhost:11434/api/tags"
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                ollama_ok = any("qwen" in m.lower() for m in models)
    except Exception as e:
        logger.warning("ai_health_ollama_check_failed", error=str(e))

    overall = "ok" if (litellm_ok or ollama_ok or openrouter_ok) else "degraded"

    return {
        "status": overall,
        "litellm_available": litellm_ok,
        "litellm_url": LITELLM_BASE_URL,
        "ollama_available": ollama_ok,
        "ollama_model": OLLAMA_MODEL,
        "openrouter_available": openrouter_ok,
        "openrouter_model": QWEN_MODEL,
        "fallback": "rule-based",
    }

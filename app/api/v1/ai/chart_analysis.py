"""POST /api/v1/ai/chart-analysis — AI-powered technical analysis via Qwen.

Accepts OHLCV candle data + chain/pair info, computes technical indicators
(RSI, MACD, EMAs, Bollinger Bands, support/resistance, pattern detection),
then calls Qwen (Ollama local or OpenRouter fallback) for human-readable AI
analysis: trend direction, key levels, pattern detection, risk assessment,
and trade signals.

Architecture:
    - Indicators computed in pure Python (no external TA lib needed)
    - Prompt assembled with structured data + indicators
    - Calls model_router.smart_route(task_type="complex") for reasoning
    - Returns structured JSON response with formatted analysis
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.model_router import smart_route
from app.core.rate_limiter import UnifiedRateLimiter
from app.routers.rugcharts.ta import (
    bollinger as _ta_bollinger,
    ema_series as _ta_ema_series,
    rsi as _ta_rsi,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai-chart"])

# ── Cost guards (audit WIN-5) ────────────────────────────────────────────
# Each call triggers paid/local LLM inference: strict per-client bucket on
# top of the global middleware (10s sustained, burst 5).
_llm_limiter = UnifiedRateLimiter()
_degraded_count = 0


# ── Request / Response models ──────────────────────────────────────────


class OHLCVCandle(BaseModel):
    time: int = Field(..., description="Unix timestamp in seconds")
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class ChartAnalysisRequest(BaseModel):
    chain: str = Field(default="solana", max_length=32, description="Blockchain (solana, ethereum, base, bsc, etc.)")
    pair: str = Field(default="", max_length=128, description="Token pair address or contract address")
    symbol: str = Field(default="", max_length=64, description="Token symbol (e.g. BONK)")
    name: str = Field(default="", max_length=256, description="Token name")
    timeframe: str = Field(default="1d", max_length=8, description="Candle timeframe (1h, 4h, 1d, 7d)")
    current_price: float = Field(default=0.0, description="Current/last price")
    price_change_24h: float = Field(default=0.0, description="24h price change percentage")
    volume_24h: float = Field(default=0.0, description="24h volume USD")
    liquidity_usd: float = Field(default=0.0, description="Liquidity in USD")
    fdv: float = Field(default=0.0, description="Fully diluted valuation")
    buys_24h: int = Field(default=0, description="24h buy transactions")
    sells_24h: int = Field(default=0, description="24h sell transactions")
    candles: list[OHLCVCandle] = Field(
        default_factory=list,
        max_length=1500,  # payload cap — LLM cost surface (audit WIN-5)
        description="OHLCV candle data for the timeframe",
    )


class KeyLevel(BaseModel):
    price: float
    label: str
    strength: str = "medium"  # strong, medium, weak


class ChartAnalysisResponse(BaseModel):
    success: bool = True
    trend: str = ""  # BULLISH, BEARISH, NEUTRAL, SIDEWAYS
    trend_strength: int = 0  # 0-100
    confidence: int = 0  # 0-100
    summary: str = ""  # 2-3 sentence summary
    key_levels: list[KeyLevel] = Field(default_factory=list)
    patterns_detected: list[str] = Field(default_factory=list)
    risk_assessment: str = ""  # LOW, MEDIUM, HIGH, EXTREME
    trade_signal: str = ""  # STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
    detailed_analysis: str = ""  # Full AI-generated analysis text
    indicators: dict[str, Any] = Field(default_factory=dict)
    provider: str = ""  # Which AI provider responded


# ── Technical Indicators — delegated to the shared RugCharts TA module ──
# Audit WIN-6: RSI/MACD/Bollinger/EMA were implemented independently HERE and
# in /rugcharts{,/intel,/ta} with guaranteed drift. One implementation now
# serves every surface (see app/routers/rugcharts/ta.py) — imported at top.


def compute_rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index (Wilder smoothing) — shared implementation."""
    value = _ta_rsi(closes, period)
    return 50.0 if value is None else float(value)


def compute_macd(closes: list[float]) -> dict[str, Any]:
    """MACD (12, 26, 9) — returns {macd, signal, histogram, crossover}."""
    neutral = {"macd": 0, "signal": 0, "histogram": 0, "crossover": "neutral"}
    if len(closes) < 35:
        return neutral

    ema_fast = _ta_ema_series(closes, 12)
    ema_slow = _ta_ema_series(closes, 26)
    difs = [(f - s) if (f is not None and s is not None) else None for f, s in zip(ema_fast, ema_slow, strict=False)]
    defined = [d for d in difs if d is not None]
    if len(defined) < 2:
        return neutral
    deas = _ta_ema_series(defined, 9)
    cur_dif, prev_dif = defined[-1], defined[-2]
    cur_dea = deas[-1]
    prev_dea = deas[-2] if len(deas) > 1 and deas[-2] is not None else cur_dea
    if cur_dea is None or prev_dea is None:
        return neutral

    histogram = cur_dif - cur_dea
    if prev_dif <= prev_dea and cur_dif > cur_dea:
        crossover = "bullish"
    elif prev_dif >= prev_dea and cur_dif < cur_dea:
        crossover = "bearish"
    else:
        crossover = "neutral"

    return {
        "macd": round(cur_dif, 8),
        "signal": round(cur_dea, 8),
        "histogram": round(histogram, 8),
        "crossover": crossover,
    }


def compute_bollinger_bands(closes: list[float], period: int = 20) -> dict[str, float | str]:
    """Bollinger Bands (20, 2) — shared implementation + position metadata."""
    if len(closes) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "width_pct": 0, "position": "unknown"}
    bands = _ta_bollinger(closes, period)
    upper, middle, lower = bands["upper"], bands["middle"], bands["lower"]
    if upper is None or middle is None or lower is None:
        return {"upper": 0, "middle": 0, "lower": 0, "width_pct": 0, "position": "unknown"}
    current = closes[-1]
    width_pct = ((upper - lower) / middle) * 100 if middle > 0 else 0
    if current > upper:
        position = "above_upper"
    elif current < lower:
        position = "below_lower"
    elif current > middle:
        position = "above_middle"
    else:
        position = "below_middle"
    return {
        "upper": round(upper, 8),
        "middle": round(middle, 8),
        "lower": round(lower, 8),
        "width_pct": round(width_pct, 2),
        "position": position,
    }


def compute_ema(closes: list[float], period: int) -> float:
    """Exponential Moving Average (latest bar) — shared implementation."""
    if not closes:
        return 0
    if len(closes) < period:
        return closes[-1]
    series = _ta_ema_series(closes, period)
    last = series[-1]
    return float(last) if last is not None else closes[-1]


def find_sr_levels(candles: list[dict[str, float]], lookback: int = 50) -> list[KeyLevel]:
    """Find support/resistance levels from swing highs/lows."""
    if len(candles) < 5:
        return []
    recent = candles[-min(lookback, len(candles)) :]
    highs = sorted({c["high"] for c in recent}, reverse=True)
    lows = sorted({c["low"] for c in recent})
    levels: list[KeyLevel] = []

    if highs:
        levels.append(KeyLevel(price=highs[0], label="Resistance", strength="strong"))
        if len(highs) > 3:
            levels.append(KeyLevel(price=highs[len(highs) // 2], label="Resistance 2", strength="medium"))
    if lows:
        levels.append(KeyLevel(price=lows[0], label="Support", strength="strong"))
        if len(lows) > 3:
            levels.append(KeyLevel(price=lows[len(lows) // 2], label="Support 2", strength="medium"))

    return levels


def detect_patterns(candles: list[dict[str, float]]) -> list[str]:
    """Detect common candlestick/price patterns."""
    patterns: list[str] = []
    if len(candles) < 10:
        return patterns

    [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    vols = [c.get("volume", 0) for c in candles]

    # Check for higher highs / higher lows (uptrend structure)
    mid = len(candles) // 2
    first_half_high = max(highs[:mid])
    second_half_high = max(highs[mid:])
    first_half_low = min(lows[:mid])
    second_half_low = min(lows[mid:])

    if second_half_high > first_half_high and second_half_low > first_half_low:
        patterns.append("Uptrend (higher highs & higher lows)")

    if second_half_high < first_half_high and second_half_low < first_half_low:
        patterns.append("Downtrend (lower highs & lower lows)")

    # Doji detection (open ≈ close, small body relative to range)
    last_5 = candles[-5:]
    doji_count = 0
    for c in last_5:
        body = abs(c["close"] - c["open"])
        range_ = c["high"] - c["low"]
        if range_ > 0 and body / range_ < 0.1:
            doji_count += 1
    if doji_count >= 2:
        patterns.append("Multiple Doji — indecision/consolidation")

    # Volume spike
    if len(vols) > 5:
        avg_vol = sum(vols[-6:-1]) / 5
        if vols[-1] > avg_vol * 2:
            patterns.append("Volume spike on latest candle")

    # Bullish/Bearish engulfing (simplified)
    if len(candles) >= 2:
        prev, curr = candles[-2], candles[-1]
        prev_body = prev["close"] - prev["open"]
        curr_body = curr["close"] - curr["open"]
        if prev_body < 0 and curr_body > abs(prev_body) and curr["close"] > prev["open"]:
            patterns.append("Bullish Engulfing")
        if prev_body > 0 and abs(curr_body) > prev_body and curr["close"] < prev["open"]:
            patterns.append("Bearish Engulfing")

    return patterns


def compute_all_indicators(candles: list[dict[str, float]], current_price: float) -> dict[str, Any]:
    """Compute all technical indicators from OHLCV data."""
    closes = [float(c["close"]) for c in candles] if candles else []

    rsi = compute_rsi(closes) if len(closes) >= 15 else 50.0
    macd = compute_macd(closes)
    bb = compute_bollinger_bands(closes) if len(closes) >= 20 else {}
    ema9 = compute_ema(closes, 9) if len(closes) >= 9 else current_price
    ema21 = compute_ema(closes, 21) if len(closes) >= 21 else current_price
    ema50 = compute_ema(closes, 50) if len(closes) >= 50 else current_price
    ema200 = compute_ema(closes, 200) if len(closes) >= 200 else current_price

    # Price position relative to EMAs
    above_9 = current_price > ema9
    above_21 = current_price > ema21
    above_50 = current_price > ema50

    # Trend determination
    if above_9 and above_21 and above_50:
        ema_trend = "bullish"
    elif not above_9 and not above_21 and not above_50:
        ema_trend = "bearish"
    else:
        ema_trend = "mixed"

    # Price change calculations
    change_1 = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 and closes[-2] > 0 else 0
    change_7 = ((closes[-1] - closes[-8]) / closes[-8]) * 100 if len(closes) >= 8 and closes[-8] > 0 else 0
    change_30 = ((closes[-1] - closes[-31]) / closes[-31]) * 100 if len(closes) >= 31 and closes[-31] > 0 else 0

    return {
        "rsi": round(rsi, 1),
        "rsi_zone": "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral",
        "macd": macd,
        "bollinger_bands": bb,
        "ema": {
            "ema9": round(ema9, 8),
            "ema21": round(ema21, 8),
            "ema50": round(ema50, 8),
            "ema200": round(ema200, 8),
        },
        "ema_trend": ema_trend,
        "price_vs_ema9": "above" if above_9 else "below",
        "price_vs_ema21": "above" if above_21 else "below",
        "price_vs_ema50": "above" if above_50 else "below",
        "price_change": {
            "1_candle_pct": round(change_1, 2),
            "7_candle_pct": round(change_7, 2),
            "30_candle_pct": round(change_30, 2),
        },
    }


# ── AI Prompt Builder ────────────────────────────────────────────────────


def build_analysis_prompt(
    req: ChartAnalysisRequest, indicators: dict[str, Any], sr_levels: list[KeyLevel], patterns: list[str]
) -> str:
    """Build a detailed prompt for the AI to analyze the chart."""

    token_id = f"{req.symbol} ({req.name})" if req.symbol else req.pair or "Unknown Token"

    candle_summary = ""
    if req.candles:
        last_c = req.candles[-1]
        first_c = req.candles[0]
        candle_summary = (
            f"OHLCV Data Summary: {len(req.candles)} candles on {req.timeframe} timeframe.\n"
            f"  First candle: O={first_c.open} H={first_c.high} L={first_c.low} C={first_c.close}\n"
            f"  Last candle:  O={last_c.open} H={last_c.high} L={last_c.low} C={last_c.close}\n"
            f"  Price range: {min(c.low for c in req.candles)} - {max(c.high for c in req.candles)}\n"
        )
        # Last 5 candles detail
        candle_summary += "  Last 5 candles (close prices): "
        candle_summary += ", ".join(f"${c.close}" for c in req.candles[-5:]) + "\n"

    prompt = f"""You are an expert crypto technical analyst. Analyze the following token's chart data and provide a professional, actionable technical analysis.

=== TOKEN INFO ===
Token: {token_id}
Chain: {req.chain}
Timeframe: {req.timeframe}
Current Price: ${req.current_price}
24h Change: {req.price_change_24h}%
24h Volume: ${req.volume_24h:,.0f}
Liquidity: ${req.liquidity_usd:,.0f}
FDV: ${req.fdv:,.0f}
Buy/Sell Ratio: {req.buys_24h}/{req.sells_24h}

=== TECHNICAL INDICATORS ===
RSI (14): {indicators.get("rsi", "N/A")} — {indicators.get("rsi_zone", "N/A")}
MACD: {indicators.get("macd", {}).get("macd", "N/A")} (Signal: {indicators.get("macd", {}).get("signal", "N/A")})
MACD Histogram: {indicators.get("macd", {}).get("histogram", "N/A")}
MACD Crossover: {indicators.get("macd", {}).get("crossover", "N/A")}
Bollinger Bands: Upper={indicators.get("bollinger_bands", {}).get("upper", "N/A")} Middle={indicators.get("bollinger_bands", {}).get("middle", "N/A")} Lower={indicators.get("bollinger_bands", {}).get("lower", "N/A")}
BB Position: {indicators.get("bollinger_bands", {}).get("position", "N/A")}
EMA 9: ${indicators.get("ema", {}).get("ema9", "N/A")} — Price is {indicators.get("price_vs_ema9", "N/A")}
EMA 21: ${indicators.get("ema", {}).get("ema21", "N/A")} — Price is {indicators.get("price_vs_ema21", "N/A")}
EMA 50: ${indicators.get("ema", {}).get("ema50", "N/A")} — Price is {indicators.get("price_vs_ema50", "N/A")}
EMA 200: ${indicators.get("ema", {}).get("ema200", "N/A")} — Price is {indicators.get("price_vs_ema200", "N/A")}
EMA Trend: {indicators.get("ema_trend", "N/A")}
Price Change (1 candle): {indicators.get("price_change", {}).get("1_candle_pct", 0)}%
Price Change (7 candles): {indicators.get("price_change", {}).get("7_candle_pct", 0)}%
Price Change (30 candles): {indicators.get("price_change", {}).get("30_candle_pct", 0)}%

=== KEY LEVELS ===
{chr(10).join(f"  {lvl.label}: ${lvl.price} ({lvl.strength})" for lvl in sr_levels) if sr_levels else "  No key levels detected"}

=== PATTERNS DETECTED ===
{chr(10).join(f"  ✓ {p}" for p in patterns) if patterns else "  No clear patterns detected"}

{candle_summary}

=== TASK ===
Based on the above data, provide a professional technical analysis with these sections in JSON format:

1. "trend": One of [BULLISH, BEARISH, NEUTRAL, SIDEWAYS]
2. "trend_strength": 0-100 indicating conviction
3. "confidence": 0-100 indicating how confident you are in this analysis
4. "summary": A 2-3 sentence market summary
5. "key_levels_additional": Any additional support/resistance levels you identify (as array of {{"price": float, "label": string, "strength": string}})
6. "patterns_additional": Additional patterns you identify
7. "risk_assessment": One of [LOW, MEDIUM, HIGH, EXTREME]
8. "trade_signal": One of [STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL]
9. "detailed_analysis": A paragraph explaining your reasoning — mention specific indicators, patterns, volume analysis, and market structure. Be specific about price levels. Include actionable insights.

IMPORTANT: Return ONLY valid JSON. No markdown, no code fences, no extra text. Just the JSON object.
"""
    return prompt


# ── Endpoint ─────────────────────────────────────────────────────────────


@router.post("/chart-analysis", response_model=ChartAnalysisResponse)
async def chart_analysis(request: Request, req: ChartAnalysisRequest) -> ChartAnalysisResponse:
    """Run AI-powered technical analysis on OHLCV chart data.

    Computes technical indicators (RSI, MACD, Bollinger Bands, EMAs,
    support/resistance, pattern detection), then calls Qwen (via Ollama
    or OpenRouter) for human-readable analysis with trade signals.

    Args:
        req: Chart data including OHLCV candles, current price, volume, etc.

    Returns:
        ChartAnalysisResponse with trend, signals, risk, and detailed AI analysis.
    """
    # 0. Per-client cost guard — this endpoint spends money per call.
    client_id = request.client.host if request.client else "anonymous"
    allowed, headers = await _llm_limiter.acquire(f"chart-analysis:{client_id}", rate=0.1, burst=5.0)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded for AI analysis", headers=headers)

    # 1. Convert candles to dicts for indicator computation
    candle_dicts = [c.model_dump() for c in req.candles] if req.candles else []

    # 2. Compute all technical indicators
    indicators = compute_all_indicators(candle_dicts, req.current_price)

    # 3. Find support/resistance levels
    sr_levels = find_sr_levels(candle_dicts)

    # 4. Detect candlestick patterns
    patterns = detect_patterns(candle_dicts)

    # 5. Build the AI prompt
    prompt = build_analysis_prompt(req, indicators, sr_levels, patterns)

    logger.info("chart_analysis_start", chain=req.chain, pair=req.pair, candles=len(candle_dicts))

    # 6. Call Qwen via model router (uses Ollama local, falls back to OpenRouter)
    try:
        result = await smart_route(
            prompt=prompt,
            task_type="complex",
            prefer="fast",
        )
    except Exception as e:
        global _degraded_count
        _degraded_count += 1
        logger.error("chart_analysis_degraded", error=str(e), degraded_total=_degraded_count)
        # Fallback: return indicator-based analysis without AI
        return _build_fallback_response(req, indicators, sr_levels, patterns)

    if not result or "error" in result:
        _degraded_count += 1
        logger.warning("chart_analysis_degraded", result=result, degraded_total=_degraded_count)
        return _build_fallback_response(req, indicators, sr_levels, patterns)

    # 7. Parse the AI response
    result_dict = cast("dict[str, Any]", result)  # smart_route always returns a dict
    raw_response: str = str(result_dict.get("response", ""))
    provider: str = str(result_dict.get("routing", {}).get("provider", result_dict.get("provider", "unknown")))
    logger.info("chart_analysis_complete", provider=provider, response_len=len(raw_response))

    try:
        # Try to extract JSON from the response (may have markdown fences)
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            # Extract from code fences
            lines = cleaned.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                if line.startswith("```") and in_json:
                    break
                if in_json:
                    json_lines.append(line)
            cleaned = "\n".join(json_lines) if json_lines else cleaned

        ai_result = json.loads(cleaned)

        # Merge AI-detected patterns and levels with our computed ones
        all_patterns = list(set(patterns + ai_result.get("patterns_additional", [])))
        all_key_levels = list(sr_levels)
        for lvl in ai_result.get("key_levels_additional", []):
            all_key_levels.append(
                KeyLevel(
                    price=lvl.get("price", 0),
                    label=lvl.get("label", ""),
                    strength=lvl.get("strength", "medium"),
                )
            )

        return ChartAnalysisResponse(
            success=True,
            trend=ai_result.get("trend", "NEUTRAL"),
            trend_strength=ai_result.get("trend_strength", 50),
            confidence=ai_result.get("confidence", 50),
            summary=ai_result.get("summary", ""),
            key_levels=all_key_levels,
            patterns_detected=all_patterns,
            risk_assessment=ai_result.get("risk_assessment", "MEDIUM"),
            trade_signal=ai_result.get("trade_signal", "NEUTRAL"),
            detailed_analysis=ai_result.get("detailed_analysis", raw_response),
            indicators=indicators,
            provider=provider,
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("chart_analysis_parse_failed", error=str(e), raw=raw_response[:500])
        # Return what we can parse — use raw text as detailed analysis
        return ChartAnalysisResponse(
            success=True,
            trend=_infer_trend(indicators),
            trend_strength=60,
            confidence=50,
            summary="AI analysis completed but response format was unexpected.",
            key_levels=sr_levels,
            patterns_detected=patterns,
            risk_assessment=_infer_risk(req, indicators),
            trade_signal=_infer_signal(indicators),
            detailed_analysis=raw_response,
            indicators=indicators,
            provider=provider,
        )


# ── Fallback helpers ─────────────────────────────────────────────────


def _infer_trend(indicators: dict[str, Any]) -> str:
    ema_trend = indicators.get("ema_trend", "mixed")
    rsi = indicators.get("rsi", 50)
    if ema_trend == "bullish" and rsi > 50:
        return "BULLISH"
    if ema_trend == "bearish" and rsi < 50:
        return "BEARISH"
    if ema_trend == "mixed":
        return "NEUTRAL"
    return "SIDEWAYS"


def _infer_signal(indicators: dict[str, Any]) -> str:
    ema_trend = indicators.get("ema_trend", "mixed")
    rsi = indicators.get("rsi", 50)
    macd = indicators.get("macd", {})
    crossover = macd.get("crossover", "neutral")

    if ema_trend == "bullish" and rsi < 40 and crossover == "bullish":
        return "BUY"
    if ema_trend == "bullish" and rsi < 30:
        return "STRONG_BUY"
    if ema_trend == "bearish" and rsi > 60 and crossover == "bearish":
        return "SELL"
    if ema_trend == "bearish" and rsi > 70:
        return "STRONG_SELL"
    return "NEUTRAL"


def _infer_risk(req: ChartAnalysisRequest, indicators: dict[str, Any]) -> str:
    rsi = indicators.get("rsi", 50)
    vol_to_liq = req.volume_24h / req.liquidity_usd if req.liquidity_usd > 0 else 0
    if rsi > 75 or rsi < 25:
        return "HIGH"
    if vol_to_liq > 100:
        return "HIGH"
    if vol_to_liq > 50:
        return "MEDIUM"
    return "LOW"


def _build_fallback_response(
    req: ChartAnalysisRequest,
    indicators: dict[str, Any],
    sr_levels: list[KeyLevel],
    patterns: list[str],
) -> ChartAnalysisResponse:
    """Build analysis response from indicators only when AI is unavailable."""
    trend = _infer_trend(indicators)
    signal = _infer_signal(indicators)
    risk = _infer_risk(req, indicators)

    return ChartAnalysisResponse(
        success=True,
        trend=trend,
        trend_strength=65,
        confidence=40,
        summary=f"Indicator-based analysis (AI unavailable). RSI: {indicators.get('rsi')} ({indicators.get('rsi_zone')}). MACD crossover: {indicators.get('macd', {}).get('crossover')}. Trend: {trend}.",
        key_levels=sr_levels,
        patterns_detected=patterns,
        risk_assessment=risk,
        trade_signal=signal,
        detailed_analysis="AI analysis engine is currently unavailable. The signals above are computed from technical indicators only (RSI, MACD, EMAs, Bollinger Bands, support/resistance).",
        indicators=indicators,
        provider="fallback (indicators only)",
    )

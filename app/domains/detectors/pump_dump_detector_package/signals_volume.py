"""Volume anomaly and price pump detection signals."""
from __future__ import annotations
from app.domains.detectors.pump_dump_detector_package.types import (
    PUMP_DUMP_THRESHOLDS,
    FindingSeverity,
    ManipulationType,
    PricePumpSignal,
    VolumeAnomaly,
)

async def detect_volume_anomalies(pairs_data: dict) -> dict:
    """Detect abnormal volume patterns indicating manipulation."""
    findings: list[dict] = []
    anomalies: list[VolumeAnomaly] = []
    if not pairs_data.get("pairs"):
        return {"findings": findings, "anomalies": anomalies}
    for pair in pairs_data["pairs"]:
        volume = pair.get("volume", {})
        vol_24h = float(volume.get("h24", 0) or 0)
        vol_1h = float(volume.get("h1", 0) or 0)
        vol_5m = float(volume.get("m5", 0) or 0)
        expected_1h = vol_24h / 24 if vol_24h > 0 else 1
        if expected_1h > 1 and vol_1h > expected_1h * PUMP_DUMP_THRESHOLDS["volume_spike_min"]:
            spike_ratio = vol_1h / expected_1h
            anomalies.append(VolumeAnomaly(
                current_volume_usd=vol_1h, avg_24h_volume_usd=expected_1h,
                spike_ratio=spike_ratio, time_window="1h", confidence=min(spike_ratio / 20, 1.0),
            ))
            sev = FindingSeverity.HIGH if spike_ratio >= PUMP_DUMP_THRESHOLDS["volume_spike_high"] else FindingSeverity.MEDIUM
            findings.append({"finding_type": ManipulationType.VOLUME_SPIKE, "severity": sev,
                "description": f"{'Extreme ' if sev == FindingSeverity.HIGH else ''}1h volume spike: {spike_ratio:.0f}x above 24h average (${vol_1h:,.0f})",
                "detail": f"24h avg: ${expected_1h:,.0f} | Current: ${vol_1h:,.0f}",
                "evidence": {"pair": pair.get("pairAddress", ""), "spike_ratio": spike_ratio}})
        expected_5m = vol_24h / 288 if vol_24h > 0 else 1
        if expected_5m > 1 and vol_5m > expected_5m * PUMP_DUMP_THRESHOLDS["volume_spike_min"]:
            spike_ratio_5m = vol_5m / expected_5m
            anomalies.append(VolumeAnomaly(
                current_volume_usd=vol_5m, avg_24h_volume_usd=expected_5m,
                spike_ratio=spike_ratio_5m, time_window="5m", confidence=min(spike_ratio_5m / 15, 1.0),
            ))
            if spike_ratio_5m >= 15:
                findings.append({"finding_type": ManipulationType.VOLUME_SPIKE, "severity": FindingSeverity.CRITICAL,
                    "description": f"Extreme 5m volume burst: {spike_ratio_5m:.0f}x above 24h average",
                    "detail": f"5m volume: ${vol_5m:,.0f} vs expected ${expected_5m:,.0f}",
                    "evidence": {"pair": pair.get("pairAddress", ""), "spike_ratio": spike_ratio_5m}})
    return {"findings": findings, "anomalies": anomalies}

async def analyze_price_pump(pairs_data: dict) -> dict:
    """Analyze price movement for pump-and-dump patterns."""
    findings: list[dict] = []
    signal = None
    if not pairs_data.get("pairs"):
        return {"signal": signal, "findings": findings}
    pair = pairs_data["pairs"][0]
    try:
        current_price = float(pair.get("priceUsd", "0") or "0")
        if current_price <= 0:
            current_price = float(pair.get("priceNative", "0") or 0)
    except (ValueError, TypeError):
        current_price = 0.0
    pc = pair.get("priceChange", {})
    m5 = float(pc.get("m5", 0) or 0)
    h1 = float(pc.get("h1", 0) or 0)
    h24 = float(pc.get("h24", 0) or 0)
    pump_signals = []
    if m5 > PUMP_DUMP_THRESHOLDS["price_pump_threshold_pct"]:
        pump_signals.append(("5m", m5))
    if h1 > PUMP_DUMP_THRESHOLDS["price_pump_threshold_pct"]:
        pump_signals.append(("1h", h1))
    if h1 > 100 and h24 < h1 * 0.5:
        price_before = current_price / (1 + h1 / 100) if current_price > 0 else 0
        dump_pct = max(0, abs(h1 - h24))
        if h24 < 0:
            dump_pct = abs(h24)
        signal = PricePumpSignal(price_before_pump=price_before, price_peak=current_price,
            pump_pct=h1, current_price=current_price or price_before, duration_seconds=3600, dump_pct_from_peak=dump_pct)
        findings.append({"finding_type": ManipulationType.PRICE_PUMP, "severity": FindingSeverity.HIGH,
            "description": f"Price pump detected: +{h1:.0f}% in 1h, followed by {dump_pct:.0f}% dump from peak",
            "detail": f"Current price: ${current_price:.6f}",
            "evidence": {"pump_pct": h1, "dump_pct": dump_pct, "current_price": current_price}})
    elif pump_signals:
        for tf, ch in pump_signals:
            findings.append({"finding_type": ManipulationType.PRICE_PUMP, "severity": FindingSeverity.MEDIUM,
                "description": f"Rapid price pump: +{ch:.0f}% in {tf}",
                "detail": f"Current price: ${current_price:.6f}" if current_price > 0 else "",
                "evidence": {"timeframe": tf, "change": ch, "current_price": current_price}})
        signal = PricePumpSignal(
            price_before_pump=current_price / (1 + h1 / 100) if current_price > 0 else 0,
            price_peak=current_price, pump_pct=h1, current_price=current_price,
            duration_seconds=3600, dump_pct_from_peak=0)
    return {"signal": signal, "findings": findings}

"""
Degen Security Scanner — Convenience API and CLI
==================================================
Module-level functions for one-shot scans and report formatting.
"""

import asyncio
import sys

from .models import SecurityReport
from .scanner import DegenSecurityScanner


async def scan_token(token_address: str, quick: bool = False) -> SecurityReport:
    """One-shot token scan."""
    scanner = DegenSecurityScanner()
    return await scanner.full_scan(token_address, quick=quick)


def format_report(report: SecurityReport) -> str:
    """Format a security report for display."""
    lines = [
        "\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557",
        "\u2551           RUG MUNCH INTELLIGENCE - DEGEN SECURITY SCAN             \u2551",
        "\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255b",
        "",
        f"\U0001fa99 Token: {report.token_name} (${report.token_symbol})",
        f"\U0001f4cd Address: {report.token_address}",
        f"\U0001f4ca Supply: {report.total_supply:,.0f} | Decimals: {report.decimals}",
        f"\U0001f465 Holders: {report.total_holders:,}",
        "",
        "\u2501\u2501\u2501 AUTHORITY STATUS \u2501\u2501\u2501",
        f"   Mint Authority: {'\u2705 RENOUNCED' if report.mint_authority_renounced else '\u274c ACTIVE'}",
        f"   Freeze Authority: {'\u2705 RENOUNCED' if report.freeze_authority_renounced else '\u274c ACTIVE'}",
        f"   Risk: {report.authorities_risk}",
        "",
        "\u2501\u2501\u2501 HOLDER CONCENTRATION \u2501\u2501\u2501",
        f"   Top 10: {report.top_10_concentration}% | Top 20: {report.top_20_concentration}% | Top 50: {report.top_50_concentration}%",
        f"   Health Score: {report.holder_health_score}/100",
        f"   Smart Money: {report.smart_money_holders} wallets",
        "",
        "\u2501\u2501\u2501 FUNDING TRACE \u2501\u2501\u2501",
        f"   Exchange Funding: {report.exchange_funding_percentage}%",
        f"   Organic Funding: {report.organic_funding_percentage}%",
        f"   Mixer Used: {'YES \U0001f6a8' if report.mixer_funding_detected else 'No'}",
        f"   Risk: {report.funding_risk}",
        "",
        "\u2501\u2501\u2501 BUNDLER DETECTION \u2501\u2501\u2501",
        f"   Detected: {'YES \U0001f3af' if report.bundler_detected else 'No'}",
    ]
    if report.bundler_detected:
        lines.extend(
            [
                f"   Bundler Wallets: {len(report.bundler_wallets)}",
                f"   Pattern: {report.bundler_pattern}",
                f"   Risk: {report.bundler_risk}",
            ]
        )
    lines.extend(
        [
            "",
            "\u2501\u2501\u2501 SNIPER ANALYSIS \u2501\u2501\u2501",
            f"   Snipers Detected: {report.sniper_count}",
            f"   Avg Entry Time: {report.avg_sniper_entry_time_ms / 1000:.1f}s after launch",
            f"   Sniper Volume: {report.sniper_volume_sol:.2f} SOL",
            f"   Risk: {report.sniper_risk}",
            "",
            "\u2501\u2501\u2501 LIQUIDITY ANALYSIS \u2501\u2501\u2501",
            f"   Total TVL: ${report.total_liquidity_usd:,.0f}",
            f"   Pools: {len(report.liquidity_pools)}",
        ]
    )
    for lp in report.liquidity_pools:
        lines.append(
            f"   \u2022 {lp.dex_name}: ${lp.tvl_usd:,.0f} TVL | Locked: {lp.lp_locked_percentage}% | Depth: {lp.depth_score}/100"
        )
    lines.extend(
        [
            f"   Risk: {report.lp_risk}",
            "",
            "\u2501\u2501\u2501 ADVANCED DETECTION \u2501\u2501\u2501",
            f"   Insiders: {len(report.insiders_detected)} wallets | Risk: {report.insider_risk}",
            f"   Wash Clusters: {len(report.wash_trade_clusters)} | Risk: {report.wash_trading_risk}",
            f"   Dev Other Tokens: {len(report.dev_other_tokens)} | Risk: {report.dev_risk}",
            f"   Buy Tax: {report.buy_tax}% | Sell Tax: {report.sell_tax}%",
            f"   Honeypot: {'YES \U0001f36f' if report.honeypot_detected else 'No'}",
            "",
            "\u2501\u2501\u2501 MARKET DATA \u2501\u2501\u2501",
        ]
    )
    if report.price_usd < 0.001:
        lines.append(f"   Price: ${report.price_usd:.10f}")
    else:
        lines.append(f"   Price: ${report.price_usd:.6f}")
    lines.extend(
        [
            f"   Market Cap: ${report.market_cap:,.0f}",
            f"   24h Volume: ${report.volume_24h:,.0f}",
            f"   24h Change: {report.price_change_24h:+.2f}%",
            f"   Buy/Sell Ratio: {report.buy_sell_ratio:.2f}",
            "",
            "\u2501\u2501\u2501 FINAL VERDICT \u2501\u2501\u2501",
            f"   RUG PULL PROBABILITY: {report.rug_pull_probability}%",
            f"   OVERALL RISK: {report.overall_risk}",
            f"   Confidence: {report.confidence_level}",
            f"   Scan Time: {report.scan_duration_ms}ms",
            "",
        ]
    )

    if report.critical_warnings:
        lines.extend(
            [
                "\u2501\u2501\u2501 \u26a0\ufe0f CRITICAL WARNINGS \u26a0\ufe0f \u2501\u2501\u2501",
                *[f"   {w}" for w in report.critical_warnings[:10]],
                "",
            ]
        )

    if report.recommendations:
        lines.extend(
            [
                "\u2501\u2501\u2501 \U0001f4a1 RECOMMENDATIONS \u2501\u2501\u2501",
                *[f"   {r}" for r in report.recommendations[:8]],
                "",
            ]
        )

    lines.extend(
        [
            "\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550",
            "  Powered by Rug Munch Intelligence - Protecting Degens Since 2024",
            "\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550",
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    # Test scan on a known token
    test_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL
    if len(sys.argv) > 1:
        test_token = sys.argv[1]

    print(f"Testing Degen Security Scanner on: {test_token}")
    report = asyncio.run(scan_token(test_token, quick=True))
    print(format_report(report))


__all__ = [
    "format_report",
    "scan_token",
]

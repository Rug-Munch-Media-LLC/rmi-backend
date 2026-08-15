"""The main ``/scan`` report assembler.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: ``format_scan_report`` renders every section using the
per-section helpers in the sibling ``_scan_report_*`` modules."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.telegram.rugmunchbot.config import _risk_gauge, fmt_chain, fmt_pct, risk_bar, threat_indicator
from app.domains.telegram.rugmunchbot.formatters._base import footer_links, sep, thin_sep
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import _clip, _collapse_blank_lines
from app.domains.telegram.rugmunchbot.formatters._scan_report_risk import _format_risk_signals_section, _verdict_text
from app.domains.telegram.rugmunchbot.formatters._scan_report_sentinel import _format_sentinel_pro
from app.domains.telegram.rugmunchbot.formatters._scan_report_token import _token_description, _token_image_url
from app.domains.telegram.rugmunchbot.formatters._scan_report_wallet import (
    _format_deployer_section,
    _format_gmgn_section,
)
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import (
    _bundle_alert_line,
    _fmt_usd_amount,
    _wallet_freshness_line,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# The parallel formatter refactor may add a dedicated verdict-header helper to
# _base.py. Import it defensively so this module works with or without it.
format_verdict_header: Callable[[dict[str, Any]], str] | None
try:  # pragma: no cover - exercised only when the _base helper is absent
    from app.domains.telegram.rugmunchbot.formatters._base import format_verdict_header
except ImportError:
    format_verdict_header = None

__all__ = [
    "format_scan_report",
]


def format_scan_report(r: dict[str, Any]) -> str:
    s = sep()
    d = thin_sep()

    # ── Header identity bits ──
    age_str = ""
    if r.get("created_at"):
        try:
            created = datetime.fromtimestamp(r["created_at"] / 1000, tz=UTC)
            age = datetime.now(UTC) - created
            if age.days > 365:
                age_str = f" | Age: {age.days // 365}y {age.days % 365 // 30}mo"
            elif age.days > 0:
                age_str = f" | Age: {age.days}d"
            else:
                age_str = f" | Age: {age.seconds // 3600}h"
        except (ConnectionError, OSError, TimeoutError):
            pass

    chain_str = fmt_chain(r["chain"])
    try:
        risk = float(r.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        risk = 0.0

    # ── 1. Verdict header (brand + identity + big risk score + one-line verdict) ──
    # The brand banner and token identity always render here; the risk
    # headline + verdict sentence come from _base.format_verdict_header when
    # the parallel refactor provides it, otherwise from the inline equivalent.
    lines = [
        "\U0001f6e1\ufe0f <b>Rug Munch Token Scan</b>",
        s,
        f"<b>{r['name']}</b> ({r['symbol']}) {chain_str}{age_str}",
        f"<code>{r['address']}</code>",
    ]
    verdict_rendered = False
    if format_verdict_header is not None:
        try:
            vh = format_verdict_header(r)
        except Exception:
            vh = None
        if vh:
            vh_lines = vh.split("\n") if isinstance(vh, str) else [str(line) for line in vh]
            lines.extend(f"  {line}" for line in vh_lines)
            verdict_rendered = True
    if not verdict_rendered:
        lines.append(f"  {_risk_gauge(risk)} \u2014 <b>{risk:.0f}/100</b>")
        lines.append(f"  \U0001f3af {_verdict_text(risk)}")

    # Bundle alert stays prominent, right after the verdict.
    bundle_alert = _bundle_alert_line(r)
    if bundle_alert:
        lines.extend(["", bundle_alert])

    # ── 2. Security Assessment (risk bar + contract checks + taxes / LP lock) ──
    lines.extend(["", "\U0001f512 <b>Security Assessment</b>", d, risk_bar(risk)])
    checks = []
    if r.get("honeypot") is not None:
        checks.append(f"  {threat_indicator('Honeypot', r['honeypot'])}")
    if r.get("contract_verified") is not None:
        checks.append(f"  {threat_indicator('Verified', not r['contract_verified'])}")
    if r.get("mintable") is not None:
        checks.append(f"  {threat_indicator('Mintable', r['mintable'])}")
    if r.get("owner_renounced") is not None:
        checks.append(f"  {threat_indicator('Ownership Renounced', not r['owner_renounced'])}")
    if checks:
        lines.extend(checks)
    tax_lines = []
    if r.get("buy_tax") is not None and r["buy_tax"] > 0:
        tax_lines.append(f"  \U0001f4e5 Buy Tax: <b>{r['buy_tax'] * 100:.1f}%</b>")
    if r.get("sell_tax") is not None and r["sell_tax"] > 0:
        tax_lines.append(f"  \U0001f4e4 Sell Tax: <b>{r['sell_tax'] * 100:.1f}%</b>")
    if r.get("lp_locked") is not None:
        tax_lines.append(f"  \U0001f510 LP Locked: <b>{r['lp_locked'] * 100:.0f}%</b>")
    if tax_lines:
        lines.extend(["", *tax_lines])

    # ── 2b. Risk Signals (consolidated "why it's sus" enrichment) ──
    risk_lines = _format_risk_signals_section(r)
    if risk_lines:
        lines.extend(risk_lines)

    # ── 3. Market Data ──
    price = r.get("price", 0)
    mcap = r.get("mcap", 0)
    liquidity = r.get("liquidity_usd") or r.get("liquidity", 0)
    vol_24h = r.get("volume_24h", 0)
    lines.extend(
        [
            "",
            "\U0001f4ca <b>Market Data</b>",
            d,
            f"  \U0001f4b0 Price: <b>{_fmt_usd_amount(price)}</b>",
            f"  \U0001f4c8 Market Cap: <b>{_fmt_usd_amount(mcap)}</b>",
            f"  \U0001f4a7 Liquidity: <b>{_fmt_usd_amount(liquidity)}</b>",
            f"  \U0001f4ca 24h Volume: <b>{_fmt_usd_amount(vol_24h)}</b>",
            f"  \U0001f504 1h: {fmt_pct(r.get('price_change_1h'))} | 24h: {fmt_pct(r.get('price_change_24h'))} | 7d: {fmt_pct(r.get('price_change_7d'))}",
        ]
    )

    # ── 4. SENTINEL Forensics ──
    sentinel_flags = _format_sentinel_pro(r)
    if sentinel_flags:
        lines.extend(["", "\U0001f6e1\ufe0f <b>AEGIS Forensics</b>", d])
        lines.extend(sentinel_flags)

    # ── 5. Top Holders ──
    if r.get("top_holders"):
        lines.extend(["", "\U0001f465 <b>Top Holders</b>", d])
        for i, h in enumerate(r["top_holders"][:5], 1):
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            cf = " \U0001f4c4" if h.get("is_contract") else ""
            lines.append(f"  {i}. <code>{h['address']}</code> - {h['pct']:.1f}%{tag}{cf}")

    # ── 5b. Smart Money / Holders (GMGN enrichment) ──
    gmgn_lines = _format_gmgn_section(r)
    if gmgn_lines:
        lines.extend(gmgn_lines)

    # ── 6. Deployer (always shown; pulled out of Advanced Analysis) ──
    deployer_lines = _format_deployer_section(r)
    if deployer_lines:
        lines.extend(deployer_lines)

    # ── 7. Advanced Analysis (single consolidated section) ──
    adv: list[str] = []
    if r.get("bundle_detected") and not bundle_alert:
        adv.append("  \U0001f578\ufe0f Bundle activity detected")
    if r.get("volume_real_ratio") is not None:
        ratio = r["volume_real_ratio"]
        emoji = "\U0001f534" if ratio > 5 else "\U0001f7e1" if ratio > 2 else "\U0001f7e2"
        adv.append(f"  {emoji} Vol/MCap ratio: <b>{ratio:.2f}x</b>")
    dev_wallet = r.get("dev_wallet")
    deployer_addr = r.get("deployer_address") or r.get("dev_wallet") or ""
    if dev_wallet and dev_wallet != deployer_addr and not any("Dev:" in line for line in deployer_lines):
        adv.append(f"  \U0001f468\u200d\U0001f4bb Dev: <code>{dev_wallet}</code>")
    wallet_line = _wallet_freshness_line(r)
    if wallet_line:
        adv.append(f"  {wallet_line}")
    elif r.get("fresh_wallet_pct", 0) > 0:
        adv.append(f"  \U0001f195 Fresh wallets: <b>{r['fresh_wallet_pct']:.0f}%</b>")
    if adv:
        lines.extend(["", "\U0001f52c <b>Advanced Analysis</b>", d])
        lines.extend(adv)

    # ── 8. Socials / Description ──
    # ALWAYS renders a Socials block when the token has any socials or an
    # image URL (never hidden behind a "NO SOCIALS" placeholder). Lists
    # Twitter / Telegram / website links plus the token image URL, with a
    # one-line safety note — external links are attacker-controlled surface
    # and must be verified before clicking. "NO SOCIALS" only when genuinely
    # none (no links AND no image).
    socials = r.get("socials", {})
    if not isinstance(socials, dict):
        socials = {}
    image_url = _token_image_url(r)
    description = _token_description(r)

    soc_parts: list[str] = []
    for stype, surl in socials.items():
        if not isinstance(surl, str) or not surl.strip():
            continue
        surl = surl.strip()
        if stype == "twitter":
            soc_parts.append(f'<a href="{html.escape(surl, quote=True)}">\U0001d54f Twitter</a>')
        elif stype == "telegram":
            soc_parts.append(f'<a href="{html.escape(surl, quote=True)}">\U0001f4f1 Telegram</a>')
        elif stype == "website":
            soc_parts.append(f'<a href="{html.escape(surl, quote=True)}">\U0001f310 Website</a>')
        else:
            soc_parts.append(
                f'<a href="{html.escape(surl, quote=True)}">\U0001f517 {html.escape(str(stype).capitalize())}</a>'
            )
    if image_url:
        soc_parts.append(f'<a href="{html.escape(image_url, quote=True)}">\U0001f5bc\ufe0f Image</a>')

    lines.append("")
    lines.append("\U0001f517 <b>Socials</b>")
    lines.append(d)
    if soc_parts:
        lines.append("  " + " | ".join(soc_parts))
        lines.append("  \u26a0\ufe0f external links may be malicious \u2014 verify before clicking")
    else:
        lines.append("  \u26a0\ufe0f NO SOCIALS")
    if description:
        lines.append(f"  \U0001f4dd <i>{html.escape(_clip(description, 300))}</i>")

    # ── 9. Footer (trade links) ──
    lines.extend(["", s, "\U0001f4b1 <b>Trade:</b>"])
    _addr = r.get("address", r.get("token", ""))
    _chain = r.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return _collapse_blank_lines("\n".join(lines))

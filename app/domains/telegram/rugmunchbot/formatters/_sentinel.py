"""SENTINEL / scan-report / news formatting blocks for the RugMunch bot.

Extracted from ``formatters.py`` during the Batch 11 de-godding pass:
SENTINEL flag rendering, deep-forensic scan report, own-data intel helpers,
the main ``/scan`` report and the news cards. ``formatters.py`` re-exports
every public name.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from app.domains.telegram.rugmunchbot.config import (
    _risk_gauge,
    fmt_chain,
    fmt_pct,
    risk_bar,
    threat_indicator,
)
from app.domains.telegram.rugmunchbot.formatters._base import footer_links, sep, thin_sep
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import (
    _SENTINEL_CODE_MAP,  # noqa: F401  # re-exported for formatters/__init__
    SENTINEL_MODULE_EMOJI,
    _bluechip_addresses,  # noqa: F401  # re-exported for formatters/__init__
    _bundle_alert_line,
    _decode_sentinel_warning,
    _deployer_history_line,
    _fmt_usd_amount,
    _module_warnings,
    _risk_level_emoji,
    _sentinel_free,
    _sentinel_recommendations,
    _wallet_freshness_line,
    is_bluechip,
)

# --- SENTINEL deep forensic flags (v2 /scan + deep scan) ---


def format_sentinel_flags(r: dict[str, Any]) -> str:
    """Render SENTINEL free-tier flags for the /scan report.

    Reads the v2 scanner's ``sentinel`` dict (free/premium module buckets).
    Blue-chip tokens (WETH, WBTC, ...) legitimately trigger concentration /
    low-lock signals — those warnings are suppressed and replaced by a single
    green confirmation line. Returns "" when the v2 response carried no
    sentinel data.
    """
    sentinel = r.get("sentinel")
    if not isinstance(sentinel, dict):
        return ""
    d = thin_sep()
    if is_bluechip(r.get("address", "")):
        return (
            "\n\U0001f6e1\ufe0f <b>SENTINEL</b>\n"
            f"{d}\n"
            "  \u2705 <b>Known blue-chip</b> \u2014 concentration & lock signals suppressed"
        )
    free = _sentinel_free(sentinel)
    flags: list[str] = []
    if free:
        # Holder HHI concentration
        ha = free.get("holder_analysis")
        if isinstance(ha, dict):
            hhi = ha.get("hhi")
            level = str(ha.get("risk_level") or "").lower()
            if hhi is not None:
                try:
                    hhi_f = float(hhi)
                except (TypeError, ValueError):
                    hhi_f = 0.0
                if level in ("high", "critical"):
                    flags.append(f"\u26a0\ufe0f High holder concentration (HHI: {hhi_f:.0f})")
                else:
                    flags.append(f"\U0001f465 Holder concentration (HHI: {hhi_f:.0f}, {level})")
        # LP lock
        lv = free.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            try:
                locked_f = float(lv["total_locked_percentage"])
            except (TypeError, ValueError):
                locked_f = 0.0
            flags.append(f"\U0001f510 LP Lock: {locked_f:.0f}%")
        # Contract authority
        ca = free.get("contract_authority")
        if isinstance(ca, dict):
            ca_level = str(ca.get("risk_level") or "LOW").upper()
            if ca_level in ("HIGH", "CRITICAL"):
                flags.append(f"\U0001f512 Contract authority risk: {ca_level}")
        # Proxy
        px = free.get("proxy_detection")
        if isinstance(px, dict):
            if px.get("can_upgrade"):
                flags.append("\u26a0\ufe0f Upgradeable proxy (admin can change implementation)")
            elif px.get("is_proxy"):
                flags.append(f"\U0001f4e6 Proxy contract ({px.get('proxy_type') or 'unknown'})")
        # Deployer reputation
        dr = free.get("dev_reputation")
        if isinstance(dr, dict):
            rep = str(dr.get("reputation_level") or "").lower()
            if rep in ("suspicious", "dangerous"):
                score = dr.get("reputation_score")
                score_str = f"{int(score)}/100" if score is not None else "N/A"
                flags.append(f"\U0001f468\u200d\U0001f4bb Dev reputation: {rep} ({score_str})")
        # Remaining module warnings (deduped, capped)
        for w in _module_warnings(
            free,
            "holder_analysis",
            "liquidity_verification",
            "contract_authority",
            "proxy_detection",
            "metadata_fingerprint",
            "dev_reputation",
        ):
            if len(flags) >= 6:
                break
            flags.append(f"\u26a0\ufe0f {w[:100]}")
    if not flags:
        flags.append("\u2705 No SENTINEL red flags")
    lines = ["", "\U0001f6e1\ufe0f <b>SENTINEL</b>", d]
    lines.extend(f"  {f}" for f in flags)
    return "\n".join(lines)


# --- SENTINEL deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---


def format_sentinel_deepscan_section(data: dict[str, Any]) -> str:
    """Render the SENTINEL deep-forensic section.

    Consumes the raw response from ``POST /api/v1/x402-tools/sentinel_scan``
    (SentinelReport serialized): composite risk score, per-module risk
    breakdown, red flags, and per-module detail (holder HHI, LP lock,
    contract authority, deployer reputation). Used by /deepscan (appended to
    the base v2 scan report) and /sentinelscan (standalone).
    """
    s = sep()
    d = thin_sep()
    address = data.get("address") or data.get("token_address") or ""
    chain = data.get("chain") or "unknown"
    try:
        risk = float(data.get("composite_risk_score") or 0)
    except (TypeError, ValueError):
        risk = 0.0
    level = str(data.get("risk_level") or "unknown")
    red_flags = data.get("red_flags") or []
    module_scores = data.get("module_risk_scores") or {}
    holder = data.get("holder_analysis") or {}
    liq = data.get("liquidity_verification") or {}
    dev = data.get("dev_reputation") or {}
    authority = data.get("contract_authority") or {}

    lines: list[str] = [
        "\U0001f6e1\ufe0f <b>SENTINEL DEEP FORENSIC SCAN</b>",
        s,
    ]
    if address:
        lines.append(f"<code>{address}</code>")
    lines.append(f"{fmt_chain(chain)}\n")

    # ── Composite risk assessment ──
    lines.append("\U0001f3af <b>Risk Assessment</b>")
    lines.append(d)
    lines.append(f"{_risk_level_emoji(level)} Composite Risk: <b>{risk:.0f}/100</b> ({level.upper()})")
    lines.append("")

    # ── Key findings (red flags) ──
    if red_flags:
        lines.append("\U0001f6a8 <b>Key Findings</b>")
        lines.append(d)
        for flag in red_flags[:8]:
            lines.append(f"  \U0001f534 {str(flag)[:160]}")
        lines.append("")
    else:
        lines.append("\U0001f7e2 <b>No critical findings</b>")
        lines.append("")

    # ── Module risk breakdown ──
    if module_scores:
        lines.append("\U0001f4ca <b>Module Risk Breakdown</b>")
        lines.append(d)
        ranked = sorted(
            module_scores.items(),
            key=lambda kv: float(kv[1] or 0),
            reverse=True,
        )
        for mod, score in ranked[:10]:
            try:
                mscore = float(score or 0)
            except (TypeError, ValueError):
                mscore = 0.0
            memoji = SENTINEL_MODULE_EMOJI.get(mod, "\U0001f539")  # 🔹
            filled = max(0, min(10, int(mscore / 10)))
            mbar = "\u2b1b" * filled + "\u2b1c" * (10 - filled)
            lines.append(f"  {memoji} {mod.replace('_', ' ').title()}: {mscore:.0f}/100")
            lines.append(f"     <code>{mbar}</code>")
        lines.append("")

    # ── Holder analysis ──
    if holder:
        lines.append("\U0001f465 <b>Holder Analysis</b>")
        lines.append(d)
        hhi = holder.get("hhi")
        if hhi is not None:
            try:
                hhi_f = float(hhi)
            except (TypeError, ValueError):
                hhi_f = 0.0
            hhi_emoji = (
                "\U0001f534" if str(holder.get("risk_level") or "").lower() in ("high", "critical") else "\U0001f7e2"
            )
            lines.append(f"  {hhi_emoji} HHI Concentration: <b>{hhi_f:.0f}</b> ({holder.get('risk_level', 'unknown')})")
        if holder.get("total_holders") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f465 Total Holders: <b>{int(holder['total_holders']):,}</b>")
        if holder.get("top_10_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f40b Top-10 Supply: <b>{float(holder['top_10_percentage']):.0f}%</b>")
        for w in (holder.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── LP analysis ──
    if liq:
        lines.append("\U0001f510 <b>LP Analysis</b>")
        lines.append(d)
        locked = liq.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            lock_emoji = "\U0001f7e2" if locked_f >= 80 else "\U0001f7e1" if locked_f > 0 else "\U0001f534"
            lines.append(f"  {lock_emoji} LP Locked: <b>{locked_f:.0f}%</b>")
        if liq.get("lp_burned"):
            lines.append("  \U0001f525 LP tokens burned")
        if liq.get("fake_lock_detected"):
            lines.append("  \U0001f534 Fake lock detected")
        for w in (liq.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Contract authority ──
    if authority:
        lines.append("\U0001f512 <b>Contract Authority</b>")
        lines.append(d)
        ca_level = str(authority.get("risk_level") or "LOW").upper()
        lines.append(f"  {_risk_level_emoji(ca_level)} Authority Risk: <b>{ca_level}</b>")
        for w in (authority.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Contract Creation Analysis ──
    creation = data.get("contract_creation") or {}
    if creation:
        lines.append("\U0001f4be <b>Contract Creation Analysis</b>")
        lines.append(d)
        creator = creation.get("creator_address")
        if creator:
            lines.append(f"  \U0001f464 Creator: <code>{creator}</code>")
        block = creation.get("block_number")
        if block:
            lines.append(f"  \U0001f4c5 Block: <b>{block:,}</b>")
        tx_hash = creation.get("tx_hash")
        if tx_hash:
            lines.append(f"  \U0001f517 Tx: <code>{tx_hash}</code>")
        first_interactors = creation.get("first_interactors") or []
        if first_interactors:
            lines.append(f"  \U0001f9e0 First Interactors ({len(first_interactors)}):")
            for i, w in enumerate(first_interactors[:5], 1):
                addr = w.get("address", "")
                label = w.get("label", "")
                fresh = w.get("is_fresh", False)
                fresh_badge = " 🆕" if fresh else ""
                lines.append(f"    {i}. <code>{addr}</code>{fresh_badge} {label}")
        fresh_pct = creation.get("fresh_wallet_percentage")
        if fresh_pct is not None:
            try:
                fpct = float(fresh_pct)
            except (TypeError, ValueError):
                fpct = 0.0
            fresh_emoji = "\U0001f534" if fpct > 50 else "\U0001f7e1" if fpct > 20 else "\U0001f7e2"
            lines.append(f"  {fresh_emoji} Fresh Wallets at Launch: <b>{fpct:.0f}%</b>")
        deployer_funded_pct = creation.get("deployer_funded_percentage")
        if deployer_funded_pct is not None:
            try:
                dfpct = float(deployer_funded_pct)
            except (TypeError, ValueError):
                dfpct = 0.0
            df_emoji = "\U0001f534" if dfpct > 30 else "\U0001f7e1" if dfpct > 10 else "\U0001f7e2"
            lines.append(f"  {df_emoji} Deployer-Funded at Launch: <b>{dfpct:.0f}%</b>")
        lines.append("")

    # ── Deployer reputation ──
    if dev:
        lines.append("\U0001f468\u200d\U0001f4bb <b>Deployer Reputation</b>")
        lines.append(d)
        rep_score = dev.get("reputation_score")
        rep_level = str(dev.get("reputation_level") or "unknown")
        if rep_score is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  {_risk_level_emoji(rep_level)} Reputation: <b>{int(rep_score)}/100</b> ({rep_level.title()})"
                )
        if dev.get("total_launches") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f4e6 Launches: <b>{int(dev['total_launches'])}</b> | "
                    f"Rugged: <b>{int(dev.get('rugged_count') or 0)}</b>"
                )
        rug_rate = dev.get("rug_rate")
        if rug_rate is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f4a5 Rug Rate: <b>{float(rug_rate) * 100:.0f}%</b>")
        for w in (dev.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Recommendations ──
    lines.append("\U0001f4a1 <b>Recommendations</b>")
    lines.append(d)
    lines.extend(f"  {rec}" for rec in _sentinel_recommendations(risk, level, red_flags))
    lines.append("")
    lines.append(
        "\u26a0\ufe0f <i>Composite risk is 0-100 (higher = riskier). "
        "SENTINEL data is forensic evidence \u2014 always DYOR.</i>"
    )
    _addr = data.get("address", data.get("token", ""))
    _chain = data.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return "\n".join(lines)


def format_sentinel_scan_report(data: dict[str, Any]) -> str:
    """Standalone /sentinelscan report — SENTINEL deep scan only."""
    return format_sentinel_deepscan_section(data)


# --- Scan-report intel helpers (own-data engines) ---


def _format_deployer_section(r: dict[str, Any]) -> list[str]:
    """Format a dedicated deployer info section — always shown in scan output.

    Shows: deployer address, launchpad detection, reputation label,
    deployer history (if repeat deployer), and RAG cross-reference.
    """
    lines = []
    d = thin_sep()

    deployer_addr = r.get("deployer_address") or r.get("dev_wallet") or ""
    reputation_label = r.get("reputation_label", "")
    reputation_score = r.get("reputation_score")
    deployer_profile = r.get("deployer_profile", {})
    chain = r.get("chain", "")

    # Header
    lines.append("")
    lines.append("\U0001f468\u200d\U0001f4bb <b>Deployer</b>")
    lines.append(d)

    # Deployer address (always shown)
    if deployer_addr and deployer_addr != "unknown":
        lines.append(f"  \U0001f464 Address: <code>{deployer_addr}</code>")
    else:
        lines.append("  \U0001f464 Address: <i>unknown</i>")

    # Launchpad detection
    try:
        from app.domains.scanner.engines.launchpad_registry import detect_launchpad

        launchpad = detect_launchpad(deployer_addr, chain)
        if launchpad:
            lp_name = launchpad.get("name", "unknown")
            lp_type = launchpad.get("type", "")
            lines.append(f"  \U0001f680 Launchpad: <b>{lp_name}</b> ({lp_type})")
    except (ConnectionError, OSError, ImportError):
        pass

    # Reputation label
    if reputation_label and reputation_label != "UNKNOWN":
        emoji = "🔴" if reputation_label in ("CRITICAL", "HIGH") else "🟡" if reputation_label == "MEDIUM" else "🟢"
        lines.append(f"  {emoji} Reputation: <b>{reputation_label}</b>")

    # Reputation score (0-100, higher = more trusted)
    if reputation_score is not None:
        try:
            score = float(reputation_score)
            emoji = "🟢" if score >= 70 else "🟡" if score >= 40 else "🔴"
            lines.append(f"  {emoji} Trust Score: <b>{score:.0f}/100</b>")
        except (TypeError, ValueError):
            pass

    # Deployer metrics (from own engine)
    deployer_metrics = r.get("deployer_metrics", {})
    if deployer_metrics:
        total_deployed = deployer_metrics.get("total_tokens_deployed", 0)
        rugpull_rate = deployer_metrics.get("rugpull_rate", 0.0)
        honeypot_rate = deployer_metrics.get("honeypot_rate", 0.0)
        if total_deployed:
            lines.append(f"  \U0001f4ca Tokens Deployed: <b>{total_deployed}</b>")
        if rugpull_rate:
            lines.append(f"  \U0001f4a3 Rugpull Rate: <b>{float(rugpull_rate) * 100:.0f}%</b>")
        if honeypot_rate:
            lines.append(f"  \U0001f52a Honeypot Rate: <b>{float(honeypot_rate) * 100:.0f}%</b>")

    # Repeat deployer profile
    if deployer_profile and isinstance(deployer_profile, dict):
        total_tokens = deployer_profile.get("total_tokens_launched", 0)
        rugged_pct = deployer_profile.get("rugged_pct", 0.0)
        honeypot_pct = deployer_profile.get("honeypot_pct", 0.0)
        avg_life = deployer_profile.get("avg_lifespan_days")
        chains_used = deployer_profile.get("chains_used", [])
        label = deployer_profile.get("label", "")

        if total_tokens and total_tokens > 1:
            lines.append(f"  \U0001f464 Repeat Deployer: <b>{total_tokens} tokens</b>")
        if rugged_pct:
            try:
                rpct = float(rugged_pct) * 100 if float(rugged_pct) <= 1.0 else float(rugged_pct)
                emoji = "🔴" if rpct >= 50 else "🟡" if rpct >= 20 else "🟢"
                lines.append(f"  {emoji} Rug Rate: <b>{rpct:.0f}%</b>")
            except (TypeError, ValueError):
                pass
        if honeypot_pct:
            try:
                hpct = float(honeypot_pct) * 100 if float(honeypot_pct) <= 1.0 else float(honeypot_pct)
                lines.append(f"  \U0001f52a Honeypot Rate: <b>{hpct:.0f}%</b>")
            except (TypeError, ValueError):
                pass
        if avg_life:
            try:
                life_f = float(avg_life)
                life_str = f"{round(life_f)} days" if life_f >= 1 else f"{round(life_f * 24)} hours"
                lines.append(f"  \u23f0 Avg Token Lifespan: <b>{life_str}</b>")
            except (TypeError, ValueError):
                pass
        if chains_used:
            lines.append(f"  \U0001f310 Chains: <b>{', '.join(chains_used)}</b>")
        if label:
            lines.append(f"  🏷️ Label: <b>{label}</b>")

    # Deployment info
    deployed_at = r.get("deployed_at") or r.get("deployment_timestamp")
    if deployed_at:
        lines.append(f"  \U0001f4c5 Deployed: <b>{str(deployed_at)[:19]}</b>")

    deployment_tx = r.get("deployment_tx") or r.get("deployment_hash")
    if deployment_tx:
        lines.append(f"  \U0001f517 TX: <code>{str(deployment_tx)[:20]}...</code>")

    return lines


def format_wallet_composition_section(r: dict[str, Any], token_created_ts: str | None = None) -> str:
    """Full wallet-composition block for /deepscan (own funding_analysis engine).

    Combines ``wallet_metrics`` (fresh <30d / CEX-funded / first-tx-this-token)
    with the ``funding_analysis`` evidence (mixer-funded %, risk assessment).
    Returns an empty string when neither payload has data — deepscan simply
    omits the section.

    Coverage honesty: ``wallet_metrics.coverage_start_ts`` is the start of
    the indexer's transfer window for this token. When the token's creation
    time (``token_created_ts``, from DexScreener) predates coverage by >7
    days, the indexer missed the token's early history and every holder
    looks <30 days old — fresh/brand-new percentages are artifacts. In that
    case only the label-based CEX line renders, under a data-coverage caveat.
    """
    wm = r.get("wallet_metrics") if isinstance(r.get("wallet_metrics"), dict) else {}
    if wm.get("result") == "unavailable":  # type: ignore[union-attr]  # PTB None-able fields
        wm = {}
    funding = (r.get("evidence") or {}).get("funding_analysis") or {}
    if not isinstance(funding, dict):
        funding = {}

    def _f(val: object) -> float | None:
        try:
            return float(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    cex = _f(wm.get("cex_funded_pct"))  # type: ignore[union-attr]  # PTB None-able fields
    analyzed = int(wm.get("holders_analyzed") or 0)  # type: ignore[union-attr]  # PTB None-able fields
    scope = f" (top {analyzed} holders)" if analyzed else ""

    # ── Coverage-gap branch: fresh/brand-new are indexer artifacts ──
    coverage_iso = wm.get("coverage_start_ts")  # type: ignore[union-attr]  # PTB None-able fields
    if cex is not None and coverage_iso and token_created_ts:
        try:
            import datetime as _dt

            cov = _dt.datetime.fromisoformat(str(coverage_iso).replace("Z", "+00:00"))
            created = _dt.datetime.fromisoformat(str(token_created_ts).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=_dt.UTC)
            if cov.tzinfo is None:
                cov = cov.replace(tzinfo=_dt.UTC)
            gap_days = (cov - created).total_seconds() / 86400.0
            if gap_days > 7:
                lines = [
                    "\U0001f45b <b>Wallet Composition</b>" + scope,
                    thin_sep(),
                    (
                        f"\u26a0\ufe0f Transfer coverage starts "
                        f"{cov.date().isoformat()} (token created {created.date().isoformat()}) — "
                        "fresh/brand-new stats skipped, indexer missed early history."
                    ),
                ]
                if cex is not None:
                    lines.append(f"\U0001f3e6 Exchange-funded (CEX, label-based): <b>{cex:.1f}%</b>")
                return "\n".join(lines)
        except (TypeError, ValueError):
            pass  # unparseable dates — fall through to the full block

    fresh = _f(wm.get("fresh_wallet_pct"))  # type: ignore[union-attr]  # PTB None-able fields
    first_tx = _f(wm.get("first_tx_pct"))  # type: ignore[union-attr]  # PTB None-able fields
    mixer = _f(funding.get("mixer_funded_pct"))
    risk = str(funding.get("risk_assessment") or "").upper()

    lines = []
    if fresh is not None:
        lines.append(f"\U0001f195 Fresh wallets (<30d): <b>{fresh:.1f}%</b>")
    if first_tx is not None:
        lines.append(f"\u2728 Brand-new (1st tx = this token): <b>{first_tx:.1f}%</b>")
    if cex is not None:
        lines.append(f"\U0001f3e6 Exchange-funded (CEX): <b>{cex:.1f}%</b>")
    if mixer:
        lines.append(f"\U0001f977 Mixer-funded: <b>{mixer:.1f}%</b>")
    if risk and risk not in ("", "UNKNOWN"):
        emoji = {"LOW": "\U0001f7e2", "MEDIUM": "\U0001f7e1", "HIGH": "\U0001f534", "CRITICAL": "\U0001f6a8"}.get(
            risk, "\u26aa"
        )
        lines.append(f"{emoji} Funding risk: <b>{risk}</b>")
    if not lines:
        return ""
    return "\U0001f45b <b>Wallet Composition</b>" + scope + "\n" + thin_sep() + "\n" + "\n".join(lines)


# ── SENTINEL warning code → human-readable description map ──
# --- Format scan report (professional redesign) ---
def format_scan_report(r: dict[str, Any]) -> str:
    s = sep()
    d = thin_sep()

    # ── Header ──
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
    lines = [
        "\U0001f6e1\ufe0f <b>RMI Token Scan</b>",
        s,
        f"<b>{r['name']}</b> ({r['symbol']}) {chain_str}{age_str}",
        f"<code>{r['address']}</code>",
    ]

    # Bundle alert (prominent)
    bundle_alert = _bundle_alert_line(r)
    if bundle_alert:
        lines.append(bundle_alert)

    # ── Market Data ──
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

    # ── Security Assessment ──
    risk = r.get("risk_score", 0)
    lines.extend(
        [
            "",
            "\U0001f512 <b>Security Assessment</b>",
            d,
            risk_bar(risk),
            f"  {_risk_gauge(risk)}",
        ]
    )

    # Contract checks
    checks = []
    if r.get("honeypot") is not None:
        checks.append(threat_indicator("Honeypot", r["honeypot"]))
    if r.get("contract_verified") is not None:
        checks.append(threat_indicator("Verified", not r["contract_verified"]))
    if r.get("mintable") is not None:
        checks.append(threat_indicator("Mintable", r["mintable"]))
    if r.get("owner_renounced") is not None:
        checks.append(threat_indicator("Ownership Renounced", not r["owner_renounced"]))
    if checks:
        lines.append("")
        lines.extend(checks)

    # Taxes & LP
    tax_lines = []
    if r.get("buy_tax") is not None and r["buy_tax"] > 0:
        tax_lines.append(f"  \U0001f4e5 Buy Tax: {r['buy_tax'] * 100:.1f}%")
    if r.get("sell_tax") is not None and r["sell_tax"] > 0:
        tax_lines.append(f"  \U0001f4e4 Sell Tax: {r['sell_tax'] * 100:.1f}%")
    if r.get("lp_locked") is not None:
        tax_lines.append(f"  \U0001f510 LP Locked: {r['lp_locked'] * 100:.0f}%")
    if tax_lines:
        lines.append("")
        lines.extend(tax_lines)

    # ── SENTINEL Forensic Flags ──
    sentinel_flags = _format_sentinel_pro(r)
    if sentinel_flags:
        lines.extend(["", "\U0001f6e1\ufe0f <b>SENTINEL Forensics</b>", d])
        lines.extend(sentinel_flags)

    # ── Top Holders ──
    if r.get("top_holders"):
        lines.extend(["", "\U0001f465 <b>Top Holders</b>", d])
        for i, h in enumerate(r["top_holders"][:5], 1):
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            cf = " \U0001f4c4" if h.get("is_contract") else ""
            lines.append(f"  {i}. <code>{h['address']}</code> - {h['pct']:.1f}%{tag}{cf}")

    # ── Advanced Analysis ──
    adv = []
    if r.get("bundle_detected") and not bundle_alert:
        adv.append("\U0001f578\ufe0f Bundle activity detected")
    wallet_line = _wallet_freshness_line(r)
    if wallet_line:
        adv.append(wallet_line)
    elif r.get("fresh_wallet_pct", 0) > 0:
        adv.append(f"\U0001f195 Fresh wallets: {r['fresh_wallet_pct']:.0f}%")
    # ── Deployer Section (always shown) ──
    deployer_lines = _format_deployer_section(r)
    if deployer_lines:
        lines.extend(deployer_lines)

    # ── Advanced Analysis ──
    adv = []
    deployer_line = _deployer_history_line(r)
    if deployer_line:
        adv.append(deployer_line)
    if r.get("volume_real_ratio") is not None:
        ratio = r["volume_real_ratio"]
        emoji = "🔴" if ratio > 5 else "🟡" if ratio > 2 else "🟢"
        adv.append(f"{emoji} Vol/MCap ratio: {ratio:.2f}x")
    if r.get("dev_wallet") and not any("Dev:" in l for l in deployer_lines):
        adv.append(f"\U0001f468\u200d\U0001f4bb Dev: <code>{r['dev_wallet']}</code>")
    wallet_line = _wallet_freshness_line(r)
    if wallet_line:
        adv.append(wallet_line)
    elif r.get("fresh_wallet_pct", 0) > 0:
        adv.append(f"\U0001f195 Fresh wallets: {r['fresh_wallet_pct']:.0f}%")
    if adv:
        lines.extend(["", "\U0001f52c <b>Advanced Analysis</b>", d])
        lines.extend(adv)

    # ── Socials ──
    socials = r.get("socials", {})
    if socials:
        soc_parts = []
        for stype, surl in socials.items():
            if stype == "twitter":
                soc_parts.append(f'<a href="{surl}">\U0001d54f</a>')
            elif stype == "telegram":
                soc_parts.append(f'<a href="{surl}">\U0001f4f1</a>')
            elif stype == "website":
                soc_parts.append(f'<a href="{surl}">\U0001f310</a>')
        if soc_parts:
            lines.extend(["", f"\U0001f517 Socials: {' | '.join(soc_parts)}"])

    # ── Footer ──
    lines.extend(["", s, "\U0001f4b1 <b>Trade:</b>"])
    _addr = r.get("address", r.get("token", ""))
    _chain = r.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return "\n".join(lines)


def _format_sentinel_pro(r: dict[str, Any]) -> list[str]:
    """Professional sentinel flag rendering with decoded warning codes."""
    sentinel = r.get("sentinel")
    if not isinstance(sentinel, dict):
        return []
    if is_bluechip(r.get("address", "")):
        return ["  \u2705 <b>Known blue-chip</b> — concentration & lock signals suppressed"]

    free = _sentinel_free(sentinel)
    flags: list[str] = []

    # Holder concentration
    ha = free.get("holder_analysis")
    if isinstance(ha, dict):
        hhi = ha.get("hhi")
        level = str(ha.get("risk_level") or "").lower()
        if hhi is not None:
            try:
                hhi_f = float(hhi)
            except (TypeError, ValueError):
                hhi_f = 0.0
            emoji = "🔴" if level in ("high", "critical") else "🟡" if level == "medium" else "🟢"
            flags.append(f"{emoji} Holder concentration: HHI {hhi_f:.0f} ({level})")

    # LP lock
    lv = free.get("liquidity_verification")
    if isinstance(lv, dict):
        locked = lv.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            emoji = "🔴" if locked_f < 20 else "🟡" if locked_f < 80 else "🟢"
            flags.append(f"{emoji} LP Locked: {locked_f:.0f}%")
        liq_usd = lv.get("liquidity_usd")
        if liq_usd:
            flags.append(f"  Liquidity USD: {_fmt_usd_amount(liq_usd)}")

    # Contract authority
    ca = free.get("contract_authority")
    if isinstance(ca, dict):
        ca_level = str(ca.get("risk_level") or "LOW").upper()
        if ca_level in ("HIGH", "CRITICAL"):
            flags.append(f"🔴 Contract authority risk: {ca_level}")
        elif ca_level == "MEDIUM":
            flags.append(f"🟡 Contract authority: {ca_level}")

    # Proxy
    px = free.get("proxy_detection")
    if isinstance(px, dict):
        if px.get("can_upgrade"):
            flags.append("⚠️ Upgradeable proxy (admin can change implementation)")
        elif px.get("is_proxy"):
            flags.append(f"📦 Proxy contract ({px.get('proxy_type') or 'unknown'})")

    # Dev reputation
    dr = free.get("dev_reputation")
    if isinstance(dr, dict):
        rep = str(dr.get("reputation_level") or "").lower()
        if rep in ("suspicious", "dangerous"):
            score = dr.get("reputation_score")
            score_str = f"{int(score)}/100" if score is not None else "N/A"
            flags.append(f"🔴 Dev reputation: {rep} ({score_str})")

    # Decode raw warning codes (CR01, RP05, etc.)
    for w in _module_warnings(
        free,
        "holder_analysis",
        "liquidity_verification",
        "contract_authority",
        "proxy_detection",
        "metadata_fingerprint",
        "dev_reputation",
    ):
        decoded = _decode_sentinel_warning(w)
        if decoded and decoded not in flags:
            if len(flags) >= 8:
                break
            flags.append(f"⚠️ {decoded}")

    if not flags:
        flags.append("✅ No SENTINEL red flags")

    return flags


def format_news_card(nc: dict[str, Any] | None) -> list[str]:
    """Format a Ground News-style news card into scan-report lines.

    Accepts the engine payload from ``get_token_news_card``:

    .. code-block:: text

        📰 News Sentiment: BEARISH 🔴 (72 trending)
        ────────────────────────
        ├─ CoinDesk: "Coldcard exploit..." [VERIFIED] [HIGH IMPACT]
        ├─ TheBlock: "Bitcoin wallets drained..." [REPORTED]
        └─ 3 sources corroborate · on-chain evidence ✓

    Returns an empty list when there is no usable data so callers can
    skip the block entirely.
    """
    if not isinstance(nc, dict):
        return []
    articles = nc.get("articles")
    if not isinstance(articles, list) or not articles:
        return []

    sentiment = str(nc.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    trending = int(nc.get("trending_score") or 0)

    lines = [
        "",
        f"\U0001f4f0 News Sentiment: <b>{sentiment}</b> {emoji} ({trending} trending)",
        thin_sep(),
    ]
    for art in articles[:5]:
        source = str(art.get("source") or "Unknown")
        raw_title = str(art.get("title") or "").strip()
        title = raw_title[:60] + ("..." if len(raw_title) > 60 else "")
        factuality = str(art.get("factuality") or "").upper()
        impact = str(art.get("market_impact") or "").upper()
        tags = []
        if factuality in ("VERIFIED", "REPORTED", "OPINION", "RUMOR"):
            tags.append(f"[{factuality}]")
        if impact:
            tags.append(f"[{impact} IMPACT]")
        line = f'\u251c\u2500 {source}: "{title}"'
        if tags:
            line += " " + " ".join(tags)
        lines.append(line)

    corr = nc.get("corroboration") or {}
    if isinstance(corr, dict):
        total = int(corr.get("total_sources") or 0)
        on_chain = bool(corr.get("on_chain_evidence"))
        if total >= 2 or on_chain:
            corr_line = f"\u2514\u2500 {total} sources corroborate"
            if on_chain:
                corr_line += " \u00b7 on-chain evidence \u2713"
            lines.append(corr_line)
    return lines


def format_news_sentiment(ns) -> list[str]:
    """Format a news_sentiment dict into scan-report lines (empty when absent).

    Accepts the engine payload from ``analyze_token_news_sentiment`` (or a
    dict with the same keys). Returns an empty list when there is no usable
    data so callers can skip the block entirely.
    """
    if not isinstance(ns, dict):
        return []
    if not ns.get("article_count"):
        return []
    sentiment = str(ns.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    lines = [
        "",
        "\U0001f4f0 <b>News Sentiment</b>",
        thin_sep(),
        f"{emoji} Sentiment: <b>{sentiment}</b> (confidence {float(ns.get('confidence') or 0) * 100:.0f}%)",
        f"\U0001f4f0 Articles: {int(ns.get('article_count') or 0)}",
    ]
    breakdown = ns.get("sentiment_breakdown") or {}
    if isinstance(breakdown, dict):
        lines.append(
            f"\U0001f4c8 Breakdown: \U0001f7e2{breakdown.get('positive', 0)} "
            f"\U0001f534{breakdown.get('negative', 0)} "
            f"\U0001f7e1{breakdown.get('neutral', 0)}"
        )
    if ns.get("trending_score") is not None:
        lines.append(f"\U0001f525 Media Attention: <b>{int(ns['trending_score'])}/100</b>")
    flags = ns.get("flags") or []
    if isinstance(flags, list) and flags:
        lines.append(f"\U0001f3c6 {', '.join(str(f) for f in flags[:3])}")
    return lines

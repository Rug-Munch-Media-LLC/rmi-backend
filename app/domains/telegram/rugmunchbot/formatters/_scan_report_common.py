"""Shared pure text helpers for the scan-report / SENTINEL formatters.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: generic string utils used by every section module.
No bot or keyboard dependencies.

Also hosts the canonical scan-report LAYOUT helpers (Batch 2026-08-16):
every scan report — regular ``/scan``, ``/scandeep``, ``/sentinelscan`` and
the ML ``/deepscan`` — shares the same 4-part skeleton:

  1. one-line VERDICT up top
  2. key metrics as a tight bold table
  3. evidence bullets
  4. a short "what to watch" line

``_key_metrics_rows`` and ``_what_to_watch_line`` render parts 2 and 4 from
a legacy-scan report dict so the regular assembler and the advanced
formatters stay on ONE layout. All data-derived strings are HTML-escaped
here (user/token content must never inject markup into a Telegram-HTML
payload).
"""

from __future__ import annotations

import html
import re
from typing import Any

from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import _fmt_usd_amount

__all__ = [
    "_clip",
    "_collapse_blank_lines",
    "_key_metrics_rows",
    "_launch_intel_rows",
    "_lp_lock_pct",
    "_mint_auth",
    "_short_addr",
    "_what_to_watch_line",
]


def _clip(text: object, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, adding a leading-ellipsis mark
    (``…``) ONLY when content was actually cut. Avoids the mid-tag / mid-word
    garbage that a bare ``[:n]`` can leave in a Telegram-HTML payload."""
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "\u2026"


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 3+ newlines to 2 (one blank line).

    Guard for section interleaving: a red-alert banner followed by the next
    section header used to render a double blank line. Pure string pass, so
    every section keeps its own leading blank without stacking extras.
    """
    return re.sub(r"\n{3,}", "\n\n", text)


def _short_addr(addr: str) -> str:
    """Compact ``0xAb12...34cd`` form for long wallet addresses."""
    addr = str(addr)
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


# ── Canonical scan-report layout (regular /scan + advanced deep scans) ─────
# Separator kept local so this module stays import-free (matches
# ``formatters._base.thin_sep``: "─" x 24).

_THIN_SEP = "\u2500" * 24

#: Risk thresholds matching ``formatters._base._risk_level`` (70 high / 40 medium).
_RISK_HIGH = 70.0
_RISK_MID = 40.0


def _risk_level_word(score: float | None) -> str:
    """Plain risk word (HIGH / MEDIUM / LOW) for a 0-100 score."""
    if score is None:
        return "UNKNOWN"
    if score >= _RISK_HIGH:
        return "HIGH"
    if score >= _RISK_MID:
        return "MEDIUM"
    return "LOW"


def _risk_emoji(score: float | None) -> str:
    """Risk color emoji for a 0-100 score (unknown → ⚪)."""
    if score is None:
        return "\u26aa"  # ⚪
    if score >= _RISK_HIGH:
        return "\U0001f534"  # 🔴
    if score >= _RISK_MID:
        return "\U0001f7e1"  # 🟠
    return "\U0001f7e2"  # 🟢


def _fmt_bool_emoji(value: bool | None, *, danger_on: bool = True) -> str:
    """Emoji for a boolean signal: 🔴 when the danger state is on, else 🟢."""
    if value is None:
        return "\u26aa"  # ⚪ unknown
    return "\U0001f534" if value == danger_on else "\U0001f7e2"


def _fmt_num(value: Any, *, suffix: str = "") -> str:
    """Format a number (thousands separators) plus optional suffix, N/A-safe."""
    try:
        return f"{float(value):,.0f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _lp_lock_pct(r: dict[str, Any]) -> float | None:
    """LP lock percentage (0-100) from any source, or None.

    Prefers the SENTINEL liquidity-verification percentage, then the legacy
    ``lp_locked`` fraction (0-1) or 0-100 value.
    """
    try:
        sentinel = r.get("sentinel") or {}
        free = sentinel.get("free") if isinstance(sentinel, dict) else None
        lv = free.get("liquidity_verification") if isinstance(free, dict) else None
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            return float(lv["total_locked_percentage"])
    except (TypeError, ValueError):
        pass
    raw = r.get("lp_locked")
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return f * 100 if f <= 1 else f


def _mint_auth(r: dict[str, Any]) -> bool | None:
    """Mint-authority-active flag (True = dangerous), or None when unknown."""
    v = r.get("mintable")
    if v is None:
        rs = r.get("risk_signals")
        if isinstance(rs, dict):
            v = rs.get("mintable")
    if v is None:
        raw = r.get("mint_authority")
        if isinstance(raw, str) and raw.lower() not in ("", "unknown", "none", "null"):
            return True
    return v if isinstance(v, bool) else None


def _freeze_auth(r: dict[str, Any]) -> bool | None:
    """Freeze/blacklist-authority-active flag (True = dangerous), or None."""
    v = r.get("freezable")
    if v is None:
        rs = r.get("risk_signals")
        if isinstance(rs, dict):
            v = rs.get("freezable")
    if v is None:
        v = r.get("can_blacklist")
    if v is None:
        raw = r.get("freeze_authority")
        if isinstance(raw, str) and raw.lower() not in ("", "unknown", "none", "null"):
            return True
    return v if isinstance(v, bool) else None


def _holders_count(r: dict[str, Any]) -> int | None:
    """Total holder count from any source, or None."""
    for source in (
        (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None,
        (r.get("sentinel") or {}).get("free", {}).get("holder_analysis")
        if isinstance(r.get("sentinel"), dict)
        else None,
        r.get("holder_analysis"),
    ):
        if isinstance(source, dict) and source.get("total_holders") is not None:
            try:
                return int(float(source["total_holders"]))
            except (TypeError, ValueError):
                pass
    try:
        holders = r.get("holders")
        return int(float(holders)) if holders is not None else None
    except (TypeError, ValueError):
        return None


def _fake_volume_pct(r: dict[str, Any]) -> float | None:
    """Fake-volume / wash-trading percentage (0-100), or None."""
    wash = r.get("wash_trading")
    if isinstance(wash, dict) and wash.get("fake_volume_pct") is not None:
        try:
            return float(wash["fake_volume_pct"])
        except (TypeError, ValueError):
            return None
    ratio = r.get("volume_real_ratio")
    if ratio is None:
        return None
    try:
        rv = float(ratio)
    except (TypeError, ValueError):
        return None
    return min(100.0, max(0.0, (rv - 2.0) * 20.0)) if rv > 2.0 else 0.0


def _fresh_wallet_pct(r: dict[str, Any]) -> float | None:
    """Fresh-wallet percentage (0-100), or None when no wallet metrics."""
    wm = r.get("wallet_metrics")
    if isinstance(wm, dict) and wm.get("result") != "unavailable" and wm.get("fresh_wallet_pct") is not None:
        try:
            return float(wm["fresh_wallet_pct"])
        except (TypeError, ValueError):
            return None
    raw = r.get("fresh_wallet_pct")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _tax_pct_str(value: Any) -> str:
    """Tax percent string from a 0-1 fraction or a 0-100 value (N/A-safe)."""
    if value is None:
        return "N/A"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{f * 100:.1f}%" if f <= 1 else f"{f:.1f}%"


def _key_metrics_rows(r: dict[str, Any], *, deep: bool = False) -> list[str]:
    """Tight bold key-metrics table shared by /scan and the deep scans.

    Rows: risk score, liquidity, holders, mint auth, freeze auth and
    buy/sell tax (always); LP lock %, bundle verdict and snipers/insiders
    (deep only). Values are bold and labels fixed-width so the block reads
    as a table on narrow Telegram screens. Emits the section heading + thin
    separator; returns an empty list only for an empty report dict.
    """
    risk_raw = r.get("risk_score")
    try:
        risk = float(risk_raw) if risk_raw is not None else None
    except (TypeError, ValueError):
        risk = None

    rows: list[str] = [
        "  Risk Score     " + f"{_risk_emoji(risk)} <b>{risk:.0f}/100</b> {_risk_level_word(risk)}"
        if risk is not None
        else f"  Risk Score     {_risk_emoji(None)} <b>N/A</b>",
    ]
    liquidity = r.get("liquidity_usd") or r.get("liquidity")
    rows.append(f"  Liquidity      <b>{_fmt_usd_amount(liquidity)}</b>")
    holders = _holders_count(r)
    rows.append(f"  Holders        <b>{holders:,}</b>" if holders is not None else "  Holders        <b>N/A</b>")

    mint = _mint_auth(r)
    rows.append(
        f"  Mint Auth      {_fmt_bool_emoji(mint)} <b>{'ACTIVE' if mint else 'renounced' if mint is not None else 'N/A'}</b>"
    )
    freeze = _freeze_auth(r)
    rows.append(
        f"  Freeze Auth    {_fmt_bool_emoji(freeze)} <b>{'ACTIVE' if freeze else 'renounced' if freeze is not None else 'N/A'}</b>"
    )
    buy_tax = _tax_pct_str(r.get("buy_tax"))
    sell_tax = _tax_pct_str(r.get("sell_tax"))
    rows.append(f"  Buy/Sell Tax   <b>{buy_tax}</b> / <b>{sell_tax}</b>")

    if deep:
        lp = _lp_lock_pct(r)
        rows.append(
            f"  LP Lock        {_risk_emoji(lp)} <b>{lp:.0f}%</b>" if lp is not None else "  LP Lock        <b>N/A</b>"
        )
        ba = r.get("bundle_analysis")
        if isinstance(ba, dict) and ba.get("is_bundled"):
            conf = _fmt_num(ba.get("confidence"), suffix="%")
            rows.append(
                "  Bundle         "
                + f"\U0001f534 <b>{conf}</b> {html.escape(str(ba.get('risk_label') or 'unknown')).upper()}"
            )
        gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
        if isinstance(gmgn_holders, dict) and (gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")):
            parts = []
            if gmgn_holders.get("sniper_count"):
                parts.append(f"\U0001f3af snipers <b>{int(gmgn_holders['sniper_count'])}</b>")
            if gmgn_holders.get("insider_count"):
                parts.append(f"\U0001f575\ufe0f insiders <b>{int(gmgn_holders['insider_count'])}</b>")
            sep_dot = " \u00b7 "
            rows.append(f"  Snipers        {sep_dot.join(parts)}")

    return ["", "\U0001f4ca <b>Key Metrics</b>", _THIN_SEP, *rows]


def _token_2022_flag(r: dict[str, Any]) -> str | None:
    """Token-2022 program flag (Solana) — None when the scan carries no data."""
    if r.get("token_2022"):
        return "Token-2022 program — extensions may hide transfer hooks / freeze"
    if str(r.get("token_program") or "").lower() in ("spl-token-2022", "tokenzqdbnblqap5vehdkas6epflc1phnbqcxeppxueb"):
        return "Token-2022 program — extensions may hide transfer hooks / freeze"
    try:
        sentinel = r.get("sentinel") or {}
        free = sentinel.get("free") if isinstance(sentinel, dict) else None
        mf = free.get("metadata_fingerprint") if isinstance(free, dict) else None
        if isinstance(mf, dict) and str(mf.get("token_program") or "").lower() in (
            "spl-token-2022",
            "token-2022",
        ):
            return "Token-2022 program — extensions may hide transfer hooks / freeze"
    except (AttributeError, TypeError):
        pass
    return None


def _mev_note(r: dict[str, Any]) -> str | None:
    """MEV note — only when the scan actually carried MEV risk data."""
    try:
        sentinel = r.get("sentinel") or {}
        free = sentinel.get("free") if isinstance(sentinel, dict) else None
        mev = free.get("mev_detection") if isinstance(free, dict) else None
        if isinstance(mev, dict):
            level = str(mev.get("risk_level") or "").lower()
            if level in ("high", "critical"):
                return f"MEV risk <b>{html.escape(level.upper())}</b> — sandwich / front-run exposure"
            warnings = mev.get("warnings")
            if isinstance(warnings, list) and warnings:
                return f"MEV: {html.escape(str(warnings[0])[:90])}"
    except (AttributeError, TypeError):
        pass
    return None


def _launch_intel_rows(r: dict[str, Any]) -> list[str]:
    """Deep-scan-only "Launch & Trading Intel" rows (empty when no data).

    Surfaces what a trader actually needs beyond the base report: bundle
    launch details (confidence, deployer-funded, first-time buyers), sniper
    / insider counts, Token-2022 program flags and MEV notes. Every row is
    data-driven — nothing renders when the scan carries no such data.
    """
    rows: list[str] = []
    ba = r.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled"):
        conf = _fmt_num(ba.get("confidence"), suffix="%")
        rows.append(
            f"  \U0001f4e6 Bundle launch: <b>{conf}</b> confidence, "
            f"{html.escape(str(ba.get('risk_label') or 'unknown')).upper()} risk"
        )
        if ba.get("deployer_funded_pct") is not None:
            rows.append(
                f"  \U0001f468\u200d\U0001f4bb Deployer-funded: <b>{_fmt_num(ba.get('deployer_funded_pct'), suffix='%')}</b>"
            )
        if ba.get("first_time_buyer_pct") is not None:
            rows.append(
                f"  \U0001f195 First-time buyers: <b>{_fmt_num(ba.get('first_time_buyer_pct'), suffix='%')}</b>"
            )
    gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
    if isinstance(gmgn_holders, dict) and (gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")):
        parts = []
        if gmgn_holders.get("sniper_count"):
            parts.append(f"\U0001f3af <b>{int(gmgn_holders['sniper_count'])}</b> snipers")
        if gmgn_holders.get("insider_count"):
            parts.append(f"\U0001f575\ufe0f <b>{int(gmgn_holders['insider_count'])}</b> insiders")
        rows.append("  " + " \u00b7 ".join(parts))
    token_2022 = _token_2022_flag(r)
    if token_2022:
        rows.append(f"  \U0001f9ea {token_2022}")
    mev = _mev_note(r)
    if mev:
        rows.append(f"  \U0001f50d {mev}")
    return rows


def _what_to_watch_line(r: dict[str, Any]) -> str:
    """Short actionable "what to watch" line (HTML-safe, single line).

    Picks the single most important behavior to watch from the scan's top
    finding. Priority mirrors the verdict sentence: honeypot > mint >
    freeze/blacklist > ownership > unverified > LP > bundle > fake volume >
    fresh wallets; otherwise a generic caution line.
    """
    if r.get("honeypot") is True:
        return "Don't buy \u2014 sells may be blocked. Verify with a small test sell first."
    if _mint_auth(r) is True:
        return "Supply can be inflated anytime \u2014 watch for sudden dilution dumps."
    if _freeze_auth(r) is True:
        return "Owner can freeze/blacklist wallets \u2014 one bad txn locks you out."
    if r.get("owner_renounced") is False:
        return "Deployer still controls the contract \u2014 watch for ownership abuse."
    if r.get("contract_verified") is False:
        return "Contract is unverified \u2014 treat the code as a black box."
    lp = _lp_lock_pct(r)
    if lp is not None and lp < 50:
        return f"LP only {lp:.0f}% locked \u2014 a pull drains liquidity fast."
    ba = r.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled"):
        return "Bundled launch \u2014 volume/concentration may be fabricated; don't trust momentum."
    fake_vol = _fake_volume_pct(r)
    if fake_vol is not None and fake_vol > 10:
        return "Fake volume suspected \u2014 price action may not be real."
    fresh = _fresh_wallet_pct(r)
    if fresh is not None and fresh > 50:
        return "Fresh wallets dominate \u2014 likely a sniped / coordinated launch."
    return "Standard caution \u2014 size in small and watch sell pressure on the first green candle."

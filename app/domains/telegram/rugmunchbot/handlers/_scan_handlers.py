#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (degodded 2026-08-14).

Extracted from ``handlers/scanning.py``: ``cmd_scan``, ``_poll_v2_scan_completion``,
``cmd_deepscan``, ``sentinel_scan``, ``cmd_wallet``, ``cmd_compare``, ``cmd_quick``,
``cmd_deployer``, ``cmd_hype``. Re-exported by ``handlers.scanning`` so
existing importers/tests keep working unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    _price_diff_pct,
    _risk_indicator,
    detect_chain,
    footer_links,
    format_deep_scan_report,
    format_scan_report,
    format_sentinel_deepscan_section,
    format_wallet_composition_section,
    format_wallet_report,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    render_analyst_read_block,
    sep,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.handlers._image import _maybe_send_token_image
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    ACTION_TRY_AGAIN,
    failure_card,
    token_not_found_card,
)
from app.domains.telegram.rugmunchbot.handlers._scanning_helpers import (
    _bucket_emoji,
    _ds_created_iso,
    _ds_pairs,
    _merge_completed_scan,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._delivery import _keep_typing
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    deep_scan_result_kb,
    scan_result_kb,
    upgrade_nudge_kb,
)
from app.domains.telegram.rugmunchbot.scanner import (
    analyze_wallet,
    analyze_wallet_v2,
    fetch_deployer_fingerprint,
    fetch_hype_score,
    scan_token,
    scan_token_v2,
    sentinel_scan as fetch_sentinel_scan,
    v2_token_to_legacy,
    v2_wallet_to_legacy,
)
from app.domains.telegram.rugmunchbot.states import check_spam
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client, http_get_json
from app.domains.telegram.rugmunchbot.tools.scanner import (
    _scan_cache_get,
    _scan_cache_set,
    _supplement_deployer_socials,
    render_ai_verdict,
)

if TYPE_CHECKING:
    from telegram import Message
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def _edit_stage_if_slow(
    msg: Message,
    text: str,
    *,
    started: float,
    min_elapsed: float = 3.0,
) -> None:
    """Refresh the in-flight scan message to ``text`` once the scan has been
    running for at least ``min_elapsed`` seconds.

    Fast scans (cache hits, quick providers) return before the threshold and
    never see placeholder churn; slow scans read like a human analyst working
    through the checks. Fail-open: any Telegram hiccup is logged, not raised.
    """
    if time.monotonic() - started < min_elapsed:
        return
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("scan stage edit skipped", exc_info=True)


async def _staged_progress(
    msg: Message,
    stages: list[tuple[str, float]],
    *,
    started: float,
) -> None:
    """Drive ``msg`` through ``stages`` (``(text, min_elapsed_s)``) while a
    long scan runs in a sibling task.

    Each stage fires only once the scan has been running for at least
    ``min_elapsed_s`` seconds. The caller cancels this task the moment the
    scan result lands, so a fast scan is never clobbered by an interim edit.
    """
    for text, min_elapsed in stages:
        wait = started + min_elapsed - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML)
        except (TelegramError, ConnectionError, OSError, TimeoutError):
            logger.debug("scan staged progress edit skipped", exc_info=True)


async def _stop_task(task: asyncio.Task[Any] | None) -> None:
    """Cancel a background helper task and await its shutdown (no dangling
    coroutine / CancelledError noise). Safe to call with ``None``."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan a token contract: usage, rate-limit gate, then live security scan."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f50d <b>Token Scanner</b>\n\n"
            "Usage: /scan <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /scan <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /scan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "\U0001f4a1 <i>Or just paste a contract address directly!</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            # Show upgrade nudge when hitting limit
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    started = time.monotonic()
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Scanning token...</b>\n<code>{target}</code>\n\n\u23f3 Running multi-source security checks...",
        parse_mode=ParseMode.HTML,
    )
    # Human-analyst feel: keep the typing bubble alive while the providers
    # run; the placeholder is refreshed with staged progress only when the
    # scan is genuinely slow (see _edit_stage_if_slow gates below).
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    try:
        # Short-TTL scan cache: repeat /scan of the same token is instant —
        # skip the whole provider chain (v2 POST + DexScreener supplement +
        # legacy fallback) within the 30s window. Fail-open: a miss rescans.
        chain_key = chain or detect_chain(target)
        cached = _scan_cache_get(target, chain_key)
        if cached is not None:
            logger.debug("cmd_scan cache hit for %s", target)
            result = cached
        else:
            result_v2 = await scan_token_v2(target, chain)
            if result_v2 is not None:
                result = v2_token_to_legacy(result_v2)
                # Supplement with DexScreener data when the v2 payload left
                # liquidity / deployer / socials unresolved (free-tier scans
                # skip the backend deployer module, so this is what fills the
                # report's Deployer + Socials blocks). One fetch, fail-open,
                # real data only.
                result = await _supplement_deployer_socials(result, target, chain or str(result.get("chain") or ""))
            else:
                # v2 unavailable → fall back to legacy multi-source scanner
                result = await scan_token(target, chain)
            _scan_cache_set(target, chain_key, result)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                token_not_found_card(target, cmd="scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        # Stage 2 (gated >3s): providers answered — surface the holder /
        # deployer analysis step only when the scan actually took time.
        await _edit_stage_if_slow(
            msg,
            f"\U0001f468\u200d\U0001f4bb <b>Analyzing deployer + holders...</b>\n<code>{target}</code>",
            started=started,
            min_elapsed=3.0,
        )
        report = format_scan_report(result)
        # AI verdict summary on top: the snappy LLM reads the structured scan
        # facts into a 2-4 sentence plain-English verdict + "what to watch".
        # Fail-open — when the LLM is unavailable the report's existing
        # template verdict (format_verdict_header) stays as-is.
        ai_verdict = await render_ai_verdict(result)
        if ai_verdict:
            report = f"{ai_verdict}\n\n{report}"
        # Stage 3 (gated >6s): the verdict is composed — show it only once the
        # whole flow has genuinely taken time (LLM verdict + providers).
        await _edit_stage_if_slow(
            msg,
            f"\U0001f9e0 <b>Writing verdict...</b>\n<code>{target}</code>",
            started=started,
            min_elapsed=6.0,
        )
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        # Use richer keyboard for premium users — reference/trade links now
        # live in the report footer, so the keyboard is actions only.
        user_tier = db.get_user_tier(user.id)
        if user_tier in ("hunter", "pro"):
            result_kb = deep_scan_result_kb(target, result["chain"])
        else:
            result_kb = scan_result_kb(target, result["chain"])
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=result_kb,
            disable_web_page_preview=True,
        )
        # Token image "pops up" as a photo reply (fail-open, text stays).
        await _maybe_send_token_image(msg, result)
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.error(f"Scan error: {e}")
        await msg.edit_text(
            failure_card("Scan Failed", "The scanner hit an unexpected error.", ACTION_CHECK_ADDRESS),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        # The report (or failure card) has landed — drop the typing bubble.
        await _stop_task(typing_task)


async def _poll_v2_scan_completion(address: str, chain: str, max_wait_s: float = 24.0) -> dict[str, Any] | None:
    """Poll the v2 scan status endpoint until background phases complete.

    The v2 scan API returns within ~25s while deep phases (wallet metrics,
    SENTINEL, news) continue in a background task. /deepscan waits a little
    longer so the wallet-composition section has real data. Returns the
    completed status payload (legacy serializer shape), or None on
    timeout/failure — the caller then renders whatever the fast path had.
    """
    url = f"{BACKEND_URL}/api/v2/scanner/token/scan/{chain}/{address}/status"
    deadline = time.monotonic() + max_wait_s
    await asyncio.sleep(4)  # head start for the background phases
    while time.monotonic() < deadline:
        try:
            client = get_shared_client(10)
            r = await client.get(url, headers=backend_headers())
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("background_status") == "complete":
                    return data
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as exc:
            logger.debug("v2 scan status poll failed for %s: %s", address[:12], exc)
        await asyncio.sleep(4)
    return None


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Full multi-phase deep scan (v6).

    Runs ALL scanner phases and enriches the result with RugMaps cluster
    data + on-chain intel for a much more detailed report than /scan.
    Slower (~1-2 min) but shows every phase it ran.
    """
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f52c <b>Deep Token Scan</b>\n\n"
            "Usage: /deepscan <code>address</code> [chain]\n\n"
            "Runs ALL phases:\n"
            "\u2460 Market data (price, volume, liquidity)\n"
            "\u2461 Contract security (honeypot, mint, taxes)\n"
            "\u2462 Holder analysis (top holders, concentration)\n"
            "\u2463 Deployer fingerprint (dev history, rugs)\n"
            "\u2464 Funding analysis (fresh wallets, bundles)\n"
            "\u2465 AI risk verdict + RugMap cluster data\n\n"
            "\u23f3 Slower than /scan (\u2248 1-2 min) but far more detailed.",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    started = time.monotonic()
    msg = await update.message.reply_text(
        f"\U0001f52c <b>Deep scan started...</b>\n"
        f"<code>{target}</code>\n\n"
        f"\u25b6\ufe0f \u2460 Market \u2192 \u2461 Security \u2192 \u2462 Holders \u2192 "
        f"\u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict\n"
        f"\u23f3 This can take up to 2 minutes...",
        parse_mode=ParseMode.HTML,
    )
    # Keep the typing bubble alive for the whole multi-phase run.
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    # Phase 1: Market data
    await asyncio.sleep(2)
    try:
        await msg.edit_text(
            f"\U0001f52c <b>Deep scan in progress...</b>\n"
            f"<code>{target}</code>\n\n"
            f"\u2705 \u2460 Market \u2192 \u25b6\ufe0f \u2461 Security \u2192 \u2462 Holders \u2192 "
            f"\u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.debug("deepscan phase-1 progress edit failed", exc_info=True)
    try:
        result_v2 = await scan_token_v2(target, chain)
        if (
            result_v2 is not None
            and result_v2.get("background_status") == "running"
            and not result_v2.get("wallet_metrics")
        ):
            # Deep phases still running — wait briefly so the wallet
            # composition (fresh/CEX/brand-new %) lands before we render.
            completed = await _poll_v2_scan_completion(target, chain or result_v2.get("chain") or detect_chain(target))
            if completed:
                _merge_completed_scan(result_v2, completed)
        # Phase 2: Security + Holders complete
        try:
            await msg.edit_text(
                f"\U0001f52c <b>Deep scan in progress...</b>\n"
                f"<code>{target}</code>\n\n"
                f"\u2705 \u2460 Market \u2705 \u2461 Security \u2705 \u2462 Holders \u2192 "
                f"\u25b6\ufe0f \u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.debug("deepscan phase-2 progress edit failed", exc_info=True)
        if result_v2 is not None:
            result = v2_token_to_legacy(result_v2)
            # Fill deployer + socials (and backfill liquidity) from
            # DexScreener when the v2 payload left them unresolved.
            result = await _supplement_deployer_socials(result, target, chain or str(result.get("chain") or ""))
        else:
            result = await scan_token(target, chain)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                token_not_found_card(target, cmd="deepscan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # ── Enrichment phase (best-effort, non-blocking) ──
        enrichment_lines: list[str] = []
        chain_norm = chain or result.get("chain") or detect_chain(target)
        token_created_ts: str | None = None
        try:
            client = get_shared_client(15)
            g_r, i_r = await asyncio.gather(
                client.get(
                    f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
                    params={"token_address": target, "chain": chain_norm, "limit": 30},
                    headers=backend_headers(),
                    timeout=12,
                ),
                client.get(
                    f"{DEXSCREENER}/tokens/{target}",
                    timeout=10,
                ),
                return_exceptions=True,
            )
            if isinstance(g_r, httpx.Response) and g_r.status_code == 200:
                gd = g_r.json()
                nodes = gd.get("nodes") or []
                links = gd.get("links") or []
                enrichment_lines.append(
                    f"\U0001f5fa\ufe0f <b>RugMap Cluster</b>\n{thin_sep()}\n"
                    f"\U0001f465 Nodes: <b>{len(nodes)}</b> | Links: <b>{len(links)}</b>"
                )
                if gd.get("risk_level"):
                    enrichment_lines.append(
                        f"\u26a0\ufe0f Graph Risk: <b>{gd.get('risk_level', 'unknown').upper()}</b> "
                        f"({gd.get('risk_score', 0)})"
                    )
                if gd.get("similar_scams"):
                    enrichment_lines.append(f"\U0001f6a8 Similar scams: {len(gd['similar_scams'])}")
            if isinstance(i_r, httpx.Response) and i_r.status_code == 200:
                pairs = _ds_pairs(i_r.json())
                if pairs:
                    p = pairs[0]
                    token_created_ts = _ds_created_iso(p)
                    enrichment_lines.append(
                        f"\U0001f4c8 <b>Market Snapshot</b>\n{thin_sep()}\n"
                        f"\U0001f4b0 {fmt_number(p.get('priceUsd', 0))} | "
                        f"\U0001f4c8 MCap {fmt_number(p.get('marketCap') or 0)} | "
                        f"\U0001f4a7 Liq {fmt_number(p.get('liquidity', {}).get('usd') or 0)}\n"
                        f"\U0001f4ca 24h Vol {fmt_number(p.get('volume', {}).get('h24') or 0)} | "
                        f"24h {fmt_pct(p.get('priceChange', {}).get('h24') or 0)}"
                    )
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError):
            logger.debug("deepscan enrichment unavailable for %s", target, exc_info=True)

        # Wallet composition from the own funding_analysis engine (fresh /
        # brand-new / CEX-funded %) — the deepscan-only forensic block.
        # ``token_created_ts`` (DexScreener pair creation) lets the formatter
        # suppress fresh/brand-new stats when the indexer's coverage starts
        # long after the token launched (coverage artifact, not a signal).
        wallet_section = format_wallet_composition_section(result, token_created_ts=token_created_ts)
        if wallet_section:
            enrichment_lines.append(wallet_section)

        # Stage 3 (gated >10s): enrichment is done — the verdict is being
        # composed. Deep scans are always slow, so this reads naturally.
        await _edit_stage_if_slow(
            msg,
            f"\U0001f9e0 <b>Writing verdict...</b>\n<code>{target}</code>",
            started=started,
            min_elapsed=10.0,
        )

        # Canonical deep-scan report: the /scan skeleton plus deep-only intel
        # (LP lock, bundle, snipers/insiders) and the enrichment sections
        # (RugMap cluster, market snapshot, wallet composition) rendered
        # INSIDE the report — before the footer, never appended after it.
        report = format_deep_scan_report(result, extra_sections=enrichment_lines)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "deep", result["risk_score"])
        user_tier = db.get_user_tier(user.id)
        if user_tier in ("hunter", "pro"):
            result_kb = deep_scan_result_kb(target, result["chain"])
        else:
            result_kb = scan_result_kb(target, result["chain"])
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=result_kb,
            disable_web_page_preview=True,
        )
        # Token image "pops up" as a photo reply (fail-open, text stays).
        await _maybe_send_token_image(msg, result)
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_deepscan_error", exc_info=True)
        logger.error(f"Deep scan error: {e}")
        await msg.edit_text(
            failure_card("Deep Scan Failed", "The deep scanner hit an unexpected error.", ACTION_CHECK_ADDRESS),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        # The report (or failure card) has landed — drop the typing bubble.
        await _stop_task(typing_task)


async def sentinel_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """SENTINEL deep forensic scan — POST /api/v1/x402-tools/sentinel_scan.

    Standalone command for the 9-module SENTINEL pipeline (holder HHI, LP
    lock, dev reputation, contract authority, proxy, bundle, wash trading,
    honeypot). Slower than /deepscan but runs every forensic module and
    renders via format_sentinel_deepscan_section.
    """
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f52c <b>AEGIS Deep Scan</b>\n\n"
            "Usage: /sentinelscan <code>address</code> [chain]\n\n"
            "Runs the full 9-module forensic pipeline:\n"
            "\u2460 Holder HHI & concentration\n"
            "\u2461 LP lock & liquidity verification\n"
            "\u2462 Dev reputation & rug rate\n"
            "\u2463 Contract authority (proxy, mint, taxes)\n"
            "\u2464 Bundle & wash trading detection\n"
            "\u2465 Honeypot analysis\n"
            "\u2466 Composite risk score + red flags\n\n"
            "\u23f3 Slowest scan (\u2248 1-2 min) \u2014 most thorough.",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    started = time.monotonic()
    msg = await update.message.reply_text(
        "\U0001f52c <b>AEGIS scanning...</b>\n\n\U0001f4aa Running all 9 forensic modules \u2014 this takes a bit.",
        parse_mode=ParseMode.HTML,
    )
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    progress_task: asyncio.Task[Any] | None = None
    try:
        # Run the forensic pipeline in the background and drive the placeholder
        # through staged, elapsed-gated progress edits while it works — a
        # multi-second scan reads like an analyst, not a spinner.
        scan_task = asyncio.create_task(fetch_sentinel_scan(target, chain))
        progress_task = asyncio.create_task(
            _staged_progress(
                msg,
                [
                    ("\U0001f50d <b>Scanning security providers...</b>", 2.0),
                    ("\U0001f468\u200d\U0001f4bb <b>Analyzing deployer + holders...</b>", 6.0),
                    ("\U0001f9e0 <b>Writing verdict...</b>", 12.0),
                ],
                started=started,
            )
        )
        result = await scan_task
        # Stop the stager BEFORE the final edit so it can never clobber it.
        await _stop_task(progress_task)
        if not result:
            await msg.edit_text(
                failure_card("AEGIS Scan Failed", "Could not reach the forensic pipeline.", ACTION_TRY_AGAIN),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        db.increment_usage(user.id, scan=True)
        # AI "Analyst Read" — plain-language bundle/cluster interpretation,
        # rendered BEFORE the deterministic sections. Fail-open: empty block
        # (LLM down / no bundle data) is skipped entirely.
        analyst_read = await render_analyst_read_block(result)
        report = format_sentinel_deepscan_section(result, analyst_read=analyst_read)
        report += f"\n\n{footer_links()}"
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb(target, result.get("chain") or chain or "unknown"),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("sentinel_scan_error", exc_info=True)
        logger.error(f"SENTINEL scan error: {e}")
        await msg.edit_text(
            failure_card("AEGIS Scan Failed", "The forensic pipeline hit an unexpected error.", ACTION_CHECK_ADDRESS),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        await _stop_task(typing_task)
        await _stop_task(progress_task)


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run wallet forensics on an address (usage, then wallet analysis)."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f9db <b>Wallet Forensics</b>\n\n"
            "Usage: /wallet <code>address</code>\n\n"
            "<b>Example:</b>\n"
            "  /wallet <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "<i>Hunter+ tiers get full P&L, funding source tracing, and connected wallet analysis.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f9db <b>Analyzing wallet...</b>\n\u23f3 Querying on-chain data...", parse_mode=ParseMode.HTML
    )
    try:
        result_v2 = await analyze_wallet_v2(addr, chain)
        if result_v2 is not None:
            result = v2_wallet_to_legacy(result_v2)
            if chain:
                result["chain"] = chain
        else:
            result = await analyze_wallet(addr, chain)
        report = format_wallet_report(result)
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(report, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True)
    except httpx.HTTPError as e:
        logger.debug("cmd_wallet_error", exc_info=True)
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(
            failure_card("Wallet Analysis Failed", "Wallet forensics hit an unexpected error.", ACTION_TRY_AGAIN),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /ta — real technical analysis from RugCharts OHLCV ──────────────────────
# (cmd_ta now lives in handlers/_ta_handlers.py; this section header is kept
# for context. The RugCharts fetchers live in handlers/_charts.py.)


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Compare two token contracts and render a verdict card."""
    user = update.effective_user
    if user is None:
        return
    if len(ctx.args or []) < 2:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u2696\ufe0f <b>Compare Tokens</b>\n\n"
            "Usage: /compare <code>addr1</code> <code>addr2</code>\n\n"
            "Example: /compare <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code> "
            "<code>0x2170Ed0880ac9A755fd29B2688956BD959F933F8</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    msg = await update.message.reply_text("\u2696\ufe0f <b>Comparing tokens...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    try:
        v2_1, v2_2 = await asyncio.gather(scan_token_v2((ctx.args or [])[0]), scan_token_v2((ctx.args or [])[1]))
        if v2_1 is not None and v2_2 is not None:
            r1, r2 = v2_token_to_legacy(v2_1), v2_token_to_legacy(v2_2)
        else:
            r1, r2 = await asyncio.gather(scan_token((ctx.args or [])[0]), scan_token((ctx.args or [])[1]))
        risk1 = r1["risk_score"]
        risk2 = r2["risk_score"]
        better = []
        if risk1 < risk2:
            better.append(f"\u2705 <b>{r1['name']}</b> \u2014 lower risk ({risk1}% vs {risk2}%)")
        elif risk2 < risk1:
            better.append(f"\u2705 <b>{r2['name']}</b> \u2014 lower risk ({risk2}% vs {risk1}%)")
        else:
            better.append(f"\u26aa Both at {risk1}% risk")
        liq1 = r1["liquidity"] or 0
        liq2 = r2["liquidity"] or 0
        if liq1 > liq2 and liq1 > 0:
            better.append(f"\U0001f4a7 <b>{r1['name']}</b> \u2014 higher liquidity")
        elif liq2 > liq1 and liq2 > 0:
            better.append(f"\U0001f4a7 <b>{r2['name']}</b> \u2014 higher liquidity")
        price_diff = _price_diff_pct(r1["price"], r2["price"])

        def mini(r: dict[str, Any]) -> str:
            """Render one compact per-token comparison block."""
            risk_ind = _risk_indicator(r["risk_score"])
            return (
                f"<b>{r['name']}</b> ({r['symbol']})\n"
                f"  <code>{r['address']}</code>\n"
                f"  \U0001f4b0 Price: <b>{fmt_number(r['price'])}</b>\n"
                f"  \U0001f4c8 MCap: {fmt_number(r['mcap'])} | \U0001f4a7 Liq: {fmt_number(r['liquidity'])}\n"
                f"  \U0001f4ca Vol: {fmt_number(r['volume_24h'])} | 24h: {fmt_pct(r['price_change_24h'])}\n"
                f"  {risk_ind} Risk: <b>{r['risk_score']}%</b> | Threats: {len(r['threats'])}"
            )

        text = (
            f"\u2696\ufe0f <b>Token Comparison</b>\n{sep()}\n\n"
            f"<b>1\ufe0f\u20e3 Token A</b>\n{mini(r1)}\n\n"
            f"<b>2\ufe0f\u20e3 Token B</b>\n{mini(r2)}\n\n"
            f"<b>\U0001f4ca Price Difference:</b> {price_diff}\n\n"
            f"<b>\U0001f3c6 Verdict</b>\n{thin_sep()}\n" + "\n".join(better) + footer_links()
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True)
    except (httpx.HTTPError, KeyError, TypeError) as e:
        logger.debug("mini_error", exc_info=True)
        logger.error(f"cmd_compare failed: {e}")
        await msg.edit_text(
            "\u274c <b>Comparison Failed</b>\n\nPlease check both addresses and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick price check - same as $TOKEN but as a command."""
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001fa99 Usage: /quick <code>SYMBOL</code>\nExample: /quick PEPE", parse_mode=ParseMode.HTML
        )
        return
    symbol = ctx.args[0].upper().lstrip("$")
    body = await http_get_json(f"{DEXSCREENER}/search/?q={symbol}", timeout=10)
    if body:
        pairs = _ds_pairs(body)
        if pairs:
            p = pairs[0]
            addr = p.get("baseToken", {}).get("address", "")
            chain = p.get("chainId", "")
            price = float(p.get("priceUsd", 0))
            h24 = p.get("priceChange", {}).get("h24") or 0
            vol = float(p.get("volume", {}).get("h24") or 0)
            mcap = float(p.get("marketCap") or 0)
            name = p.get("baseToken", {}).get("name", symbol)
            text = (
                f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                f"<code>{addr}</code>\n\n"
                f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                f"{footer_links()}"
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}")],
                    [web_scan_button(addr, chain)],
                ]
            )
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return
    else:
        logger.warning(f"cmd_quick DexScreener lookup failed for symbol {symbol}")
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\u274c Could not find token: <b>{symbol}</b>\n\n"
        "Double-check the symbol, or paste the full address with /scan.",
        parse_mode=ParseMode.HTML,
    )


# ── /deployer — quick deployer fingerprint check ──


async def cmd_deployer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick deployer fingerprint: run a v2 scan and surface dev reputation."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f468\u200d\U0001f4bb <b>Deployer Fingerprint</b>\n\n"
            "Usage: /deployer <code>address</code>\n\n"
            "Example: /deployer <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "Runs a v2 security scan and extracts the deployer reputation: "
            "dev risk score, deployment history, rug count.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f468\u200d\U0001f4bb <b>Checking deployer fingerprint...</b>\n<code>{target}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await scan_token_v2(target, chain)
        if data is None:
            await msg.edit_text(
                failure_card("Deployer Check Failed", "The scanner is unavailable right now.", ACTION_TRY_AGAIN),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        evidence = data.get("evidence") or {}
        dev = evidence.get("deployer") or {}
        if not dev:
            await msg.edit_text(
                f"\u26aa <b>No Deployer Data</b>\n\n"
                f"<code>{target}</code>\n"
                f"No deployer reputation data returned by the scanner.\n\n"
                f"Safety: {data.get('safety_score', 'N/A')}/100 | "
                f"Risk level: {data.get('risk_level', 'unknown')}",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        risk_score = dev.get("risk_score")
        risk_label = dev.get("risk_label") or dev.get("reputation_label") or "UNKNOWN"
        dev_addr = dev.get("deployer_address") or dev.get("address") or "unknown"
        chain_norm = chain or detect_chain(target)

        # Enrich with the C7 deployer behavioral fingerprint (risk bucket,
        # success rate, launchpad) when the backend can compute it.
        fingerprint = await fetch_deployer_fingerprint(dev_addr, chain_norm)
        fp = fingerprint or {}

        risk_bucket = fp.get("risk_bucket") or risk_label
        total_deployments = fp.get("total_deployments")
        if total_deployments is None:
            total_deployments = dev.get("total_deployments") or dev.get("deployment_count")
        success_rate = fp.get("success_rate")
        launchpad = fp.get("launchpad")
        lines = [
            "\U0001f468\u200d\U0001f4bb <b>Deployer Fingerprint</b>",
            sep(),
            f"<code>{dev_addr}</code>\n",
            "\U0001f3af <b>Risk Profile</b>",
            thin_sep(),
        ]
        dev_risk = fp.get("risk_score")
        if dev_risk is None:
            dev_risk = risk_score
        if dev_risk is not None:
            emoji = "\U0001f7e2" if dev_risk < 30 else "\U0001f7e1" if dev_risk < 60 else "\U0001f534"
            lines.append(f"{emoji} Dev Risk Score: <b>{dev_risk}/100</b>")
        if risk_bucket:
            lines.append(f"{_bucket_emoji(str(risk_bucket))} Risk Bucket: <b>{risk_bucket}</b>")
        if total_deployments is not None:
            lines.append(f"\U0001f4e6 Total Deployments: <b>{total_deployments}</b>")
        if success_rate is not None:
            lines.append(f"\U0001f4c8 Success Rate: <b>{success_rate * 100:.0f}%</b>")
        if launchpad:
            lines.append(f"\U0001f680 Known Launchpad: <b>{launchpad}</b>")
        if dev.get("rug_count") is not None:
            lines.append(f"\U0001f4a5 Rug Count: <b>{dev['rug_count']}</b>")
        if fp.get("median_lifespan_hours") is not None:
            lines.append(f"\u231b Median Lifespan: <b>{fp['median_lifespan_hours']:.0f}h</b>")
        if evidence.get("holders"):
            h = evidence["holders"]
            lines.append(
                f"\U0001f465 Top-10 Holders: <b>{h.get('top_10_pct', 'N/A')}%</b> "
                f"| Holders: {h.get('holder_count', 'N/A')}"
            )
        if evidence.get("liquidity"):
            liq = evidence["liquidity"]
            locked = liq.get("locked_pct")
            if locked is not None:
                lines.append(f"\U0001f510 LP Locked: <b>{locked:.0f}%</b>")
        if evidence.get("security", {}).get("checks"):
            checks = evidence["security"]["checks"]
            fails = sum(1 for c in checks.values() if c.get("status") == "fail")
            lines.append(f"\U0001f512 Failed Security Checks: <b>{fails}</b>")
        lines.append("")
        lines.append(f"\U0001f50d Full scan: /scan <code>{target}</code>{footer_links()}")
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        logger.debug("cmd_deployer_error", exc_info=True)
        logger.error(f"cmd_deployer failed: {e}")
        await msg.edit_text(
            failure_card("Deployer Check Failed", "The deployer lookup hit an unexpected error.", ACTION_TRY_AGAIN),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /hype — quick hype score ──


async def cmd_hype(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick hype score: boosts, volume, age, and price momentum."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f525 <b>Hype Score</b>\n\n"
            "Usage: /hype <code>address_or_symbol</code>\n\n"
            "Example: /hype <code>PEPE</code>\n\n"
            "Returns a 0-100 hype score based on market activity, "
            "volume momentum, token age, and price action.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    target = ctx.args[0]
    msg = await update.message.reply_text("\U0001f525 <b>Measuring hype...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    try:
        # Resolve address: if it looks like a symbol, search DexScreener
        address = target
        chain = ""
        client = get_shared_client(15)
        if not (is_evm(target) or is_sol(target)):
            r = await client.get(f"{DEXSCREENER}/search/?q={target}")
            if r.status_code == 200:
                pairs = _ds_pairs(r.json())
                if pairs:
                    address = pairs[0].get("baseToken", {}).get("address", "")
                    chain = pairs[0].get("chainId", "")
        if not address:
            await msg.edit_text(
                f"\u274c Could not resolve token: <b>{target}</b>\n\n"
                "Check the symbol, or paste the full address with /scan.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Boosts (paid hype signal)
        boosts = 0
        try:
            br = await client.get(f"{DEXSCREENER}/token-boosts/top/v1")
            if br.status_code == 200:
                boost_list = br.json()
                if isinstance(boost_list, list):
                    for b in boost_list:
                        if b.get("tokenAddress", "").lower() == address.lower():
                            boosts = int(b.get("boostCount", 0) or 0)
                            break
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

        # Pair data
        tr = await client.get(f"{DEXSCREENER}/tokens/{address}")
        if tr.status_code != 200:
            await msg.edit_text(
                "\u274c Token not found.\n\nCheck the address and try again.", parse_mode=ParseMode.HTML
            )
            return
        pairs = _ds_pairs(tr.json())
        if not pairs:
            await msg.edit_text(
                "\u274c No trading pairs found.\n\nEnsure the token has an active pair and try again.",
                parse_mode=ParseMode.HTML,
            )
            return
        p = pairs[0]
        name = p.get("baseToken", {}).get("name", "???")
        symbol = p.get("baseToken", {}).get("symbol", "???")
        chain = chain or p.get("chainId", "")
        price = float(p.get("priceUsd", 0))
        h1 = float(p.get("priceChange", {}).get("h1") or 0)
        h24 = float(p.get("priceChange", {}).get("h24") or 0)
        vol24 = float(p.get("volume", {}).get("h24") or 0)
        liq = float(p.get("liquidity", {}).get("usd") or 0)
        created = p.get("pairCreatedAt")
        from datetime import UTC, datetime as dt

        age_hours = 0.0
        if created:
            with contextlib.suppress(OSError, ValueError, TypeError):
                age_hours = max(0, (dt.now(UTC) - dt.fromtimestamp(created / 1000, tz=UTC)).total_seconds() / 3600)

        # ── C5 hype score from backend (preferred) ──
        # Composite 0-100 with per-signal breakdown: social velocity, volume
        # growth, holder growth, price momentum. Falls back to the local
        # DexScreener computation below when unavailable.
        c5 = await fetch_hype_score(address, chain)
        if c5 is not None and c5.get("hype_score") is not None:
            hype = float(c5["hype_score"])
            signals = c5.get("signals") or {}
            social = signals.get("social_velocity") or {}
            volume = signals.get("volume_growth") or {}
            holder = signals.get("holder_growth") or {}
            price_sig = signals.get("price_momentum") or {}
            emoji = "\U0001f525" if hype >= 70 else "\U0001f7e1" if hype >= 40 else "\U0001f7e2"
            lines = [
                f"{emoji} <b>Hype Score: {hype:.0f}/100</b>",
                sep(),
                f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}",
                f"<code>{address}</code>\n",
                "\U0001f4ca <b>Components</b>",
                thin_sep(),
                f"\U0001f4e2 Social Velocity: <b>{float(social.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f4c8 Volume Growth: <b>{float(volume.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f465 Holder Growth: <b>{float(holder.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f680 Price Momentum: <b>{float(price_sig.get('score', 0) or 0):.0f}/100</b>",
            ]
            if c5.get("inactivity_hours") is not None:
                lines.append(f"\u23f3 Inactivity: {c5['inactivity_hours']:.0f}h (decay x{c5.get('decay_factor', 1.0)})")
            details = []
            for sig in (social, volume, holder, price_sig):
                if isinstance(sig, dict) and sig.get("detail"):
                    details.append(str(sig["detail"])[:60])
            if details:
                lines.append("")
                lines.append("\U0001f4cb <i>" + " | ".join(details[:4]) + "</i>")
            lines.append("")
            lines.append(f"\U0001f4b0 Price: {fmt_number(price)}")
            lines.append("")
            lines.append(f"\U0001f50d Full scan: /scan <code>{address}</code>{footer_links()}")
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [web_scan_button(address, chain)],
                ]
            )
            db.increment_usage(user.id, scan=True)
            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return

        # ── Hype scoring (local fallback) ──
        boost_score = min(boosts * 8, 40)  # 0-40 from boost count
        vol_score = 0
        if vol24 > 0:
            if vol24 >= 1_000_000:
                vol_score = 20
            elif vol24 >= 100_000:
                vol_score = 14
            elif vol24 >= 10_000:
                vol_score = 8
            elif vol24 > 0:
                vol_score = 4
        momentum = 0
        if h1 > 20 and h24 > 30:
            momentum = 20
        elif h1 > 10 and h24 > 15:
            momentum = 14
        elif h24 > 5:
            momentum = 8
        elif h24 < -20:
            momentum = 0
        else:
            momentum = 4
        age_score = 0
        if age_hours and age_hours < 24:
            age_score = 10
        elif age_hours and age_hours < 72:
            age_score = 6
        elif age_hours and age_hours < 168:
            age_score = 3
        liquidity_score = 10 if liq >= 100_000 else 5 if liq >= 10_000 else 0
        hype = min(boost_score + vol_score + momentum + age_score + liquidity_score, 100)

        emoji = "\U0001f525" if hype >= 70 else "\U0001f7e1" if hype >= 40 else "\U0001f7e2"
        lines = [
            f"{emoji} <b>Hype Score: {hype}/100</b>",
            sep(),
            f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}",
            f"<code>{address}</code>\n",
            "\U0001f4ca <b>Components</b>",
            thin_sep(),
            f"\U0001f525 Boosts: {boosts} (x8, max 40) -> {boost_score}",
            f"\U0001f4c8 Volume 24h: {fmt_number(vol24)} -> {vol_score}/20",
            f"\U0001f504 Momentum (1h {fmt_pct(h1)} / 24h {fmt_pct(h24)}): -> {momentum}/20",
            f"\u231b Age: {age_hours:.0f}h -> {age_score}/10",
            f"\U0001f4a7 Liquidity: {fmt_number(liq)} -> {liquidity_score}/10",
            "",
            f"\U0001f4b0 Price: {fmt_number(price)}",
        ]
        lines.append("")
        lines.append(f"\U0001f50d Full scan: /scan <code>{address}</code>{footer_links()}")
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}")],
                [web_scan_button(address, chain)],
            ]
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        logger.debug("cmd_hype_error", exc_info=True)
        logger.error(f"cmd_hype failed: {e}")
        await msg.edit_text(
            failure_card("Hype Check Failed", "The hype score lookup hit an unexpected error.", ACTION_TRY_AGAIN),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

#!/usr/bin/env python3
"""
RugMunchBot -- OSINT command handlers (/osint /track /investigate /email /phone /face).

Wraps the self-hosted OSINT section (``app.osint`` + ``app.osint_tools``) into
Telegram commands. Every lookup renders an :class:`OsintReport` (or, for
usernames, an :class:`OsintResult`) as a compact HTML message: top findings
first, then "+N more…". Graceful degradation: an unavailable tool/service or
any exception becomes a helpful message instead of a crash.

Conventions follow ``handlers/price.py`` (reply with a status message, then
``edit_text`` the result; ``back_kb`` on usage/errors). All lookups are
blocking (network + subprocess) so they run via ``asyncio.to_thread`` to keep
the event loop responsive.
"""

from __future__ import annotations

import asyncio
import html
import io
import re
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.formatters import footer_links, thin_sep
from app.domains.telegram.rugmunchbot.handlers.investigate import cmd_investigate as _pro_investigate
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.osint import OsintNotAvailable, OsintReport
from app.osint.domain import lookup_domain
from app.osint.email import lookup_email
from app.osint.face import HAARCASCADE_MODEL_PATH, YUNET_MODEL_PATH
from app.osint.phone import lookup_phone
from app.osint.wallet import lookup_wallet
from app.osint_tools import OsintNotAvailable as ToolsOsintNotAvailable, OsintResult, lookup_username

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.osint")

# ─── Tunables ────────────────────────────────────────────────────────

#: Max findings rendered inline; the rest collapse to "+N more…".
_MAX_FINDINGS = 8
#: Max username accounts rendered inline before "+N more…".
_MAX_SITES = 10

#: Domain-ish arg detection for /investigate routing (see cmd_investigate).
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE)

#: Per-lookup wall-clock budgets (seconds) passed to each OSINT module.
_TIMEOUTS: dict[str, int] = {
    "username": 25,
    "wallet": 45,
    "domain": 45,
    "email": 30,
    "phone": 20,
}

#: Category → emoji for a scannable findings list.
_CATEGORY_EMOJI: dict[str, str] = {
    "chain": "\u26d3\ufe0f",
    "entity": "\U0001f3f7\ufe0f",
    "exchange": "\U0001f3e6",
    "sanction": "\U0001f6ab",
    "scam": "\u26a0\ufe0f",
    "registrant": "\U0001f3e2",
    "registrar": "\U0001f3e2",
    "dns": "\U0001f310",
    "subdomain": "\U0001f9e9",
    "ssl": "\U0001f512",
    "archive": "\U0001f4e6",
    "fingerprint": "\U0001f5a5\ufe0f",
    "breach": "\U0001f573\ufe0f",
    "account": "\U0001f464",
    "reputation": "\u2b50",
    "disposable": "\U0001f5d1\ufe0f",
    "validation": "\u2705",
    "region": "\U0001f4cd",
    "carrier": "\U0001f4f1",
    "risk": "\u26a0\ufe0f",
    "timezone": "\U0001f550",
}

#: Confidence → emoji.
_CONFIDENCE_EMOJI: dict[str, str] = {
    "high": "\U0001f7e2",
    "medium": "\U0001f7e1",
    "low": "\U0001f534",
}

#: Reverse-image-search landing pages surfaced by /face.
_REVERSE_SEARCH_LINKS: list[tuple[str, str]] = [
    ("PimEyes", "https://pimeyes.com"),
    ("Google Lens", "https://lens.google.com"),
    ("Yandex Images", "https://yandex.com/images/"),
]


# ─── Face detection (optional, degrades gracefully) ──────────────────


try:
    import cv2  # opencv-python ships a stub; attribute access is untyped below
except ImportError:  # pragma: no cover — optional dependency
    cv2 = None  # type: ignore[assignment]


def _detect_faces(image: bytes) -> tuple[int, str | None]:
    """Detect faces in an image with OpenCV's committed models.

    Uses ``cv2.FaceDetectorYN`` (YuNet ONNX — the only face detector in
    opencv-python 5.x, which dropped ``CascadeClassifier``) and falls back to
    ``cv2.CascadeClassifier`` (Haar XML — classic OpenCV 4.x). Both models are
    committed under ``app/osint/models/`` (opencv 5.x ships no cascades in
    ``cv2.data.haarcascades``). Returns ``(face_count, warning)``; ``warning``
    is set (and the count is 0) when OpenCV or both models are unavailable so
    callers can show a helpful message plus the reverse-search hint.
    """
    if cv2 is None:
        return 0, "face detection is not installed on this server (opencv-python missing)"
    try:
        import numpy as np

        arr = np.frombuffer(image, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 0, "could not decode the photo"

        # Preferred: YuNet (OpenCV 5.x). DNN wants BGR, contiguous.
        if YUNET_MODEL_PATH.is_file() and hasattr(cv2, "FaceDetectorYN"):
            try:
                detector = cv2.FaceDetectorYN.create(
                    str(YUNET_MODEL_PATH),
                    "",
                    (img.shape[1], img.shape[0]),
                    score_threshold=0.9,
                    nms_threshold=0.3,
                    top_k=5000,
                )
                _ok, faces = detector.detect(np.ascontiguousarray(img))
                if faces is not None and len(faces):
                    return len(faces), None
            except Exception as exc:
                logger.warning("yunet detection failed, trying haar: %s", exc)

        # Fallback: Haar cascade (classic OpenCV 4.x).
        if HAARCASCADE_MODEL_PATH.is_file() and hasattr(cv2, "CascadeClassifier"):
            cascade = cv2.CascadeClassifier(str(HAARCASCADE_MODEL_PATH))
            if not cascade.empty():
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                return len(faces), None

        return 0, "face detection model not available on this server"
    except Exception as exc:
        logger.warning("face detection failed: %s", exc)
        return 0, "face detection failed for this photo"


# ─── Input validation ────────────────────────────────────────────────


def _looks_like_domain(value: str) -> bool:
    """True when a command arg looks like a domain (has a dot, no spaces)."""
    v = value.strip().lower()
    return bool(_DOMAIN_RE.match(v))


def _valid_username(value: str) -> bool:
    """Mirror ``app.osint_tools`` constraints: 1-128 chars, no whitespace/path separators."""
    return bool(re.match(r"^[^\s/\\]{1,128}$", value)) and "@" not in value


def _valid_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def _valid_phone(value: str) -> bool:
    return bool(re.match(r"^\+?[0-9][0-9\s().-]{4,19}$", value))


# ─── Rendering ───────────────────────────────────────────────────────


def _format_report(report: OsintReport, title: str) -> str:
    """Render an OsintReport as a compact HTML message (top findings first)."""
    parts = [f"{title}\n{thin_sep()}", f"<code>{html.escape(report.target)}</code>"]
    if report.errors:
        parts.append(f"\u26a0\ufe0f <i>{html.escape('; '.join(report.errors[:2]))}</i>")
    findings = report.findings[:_MAX_FINDINGS]
    if not findings:
        parts.append("<i>No public findings for this target.</i>")
    else:
        for f in findings:
            conf = _CONFIDENCE_EMOJI.get(f.confidence, "\U0001f7e1")
            cat = _CATEGORY_EMOJI.get(f.category, "\u2022")
            parts.append(f"{cat} {conf} <b>{html.escape(f.category.title())}</b> \u2014 {html.escape(f.summary)}")
        extra = len(report.findings) - _MAX_FINDINGS
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more</i>")
    parts.append(footer_links())
    return "\n".join(parts)


def _format_username_result(result: OsintResult) -> str:
    """Render a username lookup (found accounts first, then '+N more…')."""
    sites = [s for s in result.sites if s.exists]
    parts = ["\U0001f575\ufe0f <b>Username OSINT</b>\n" + thin_sep(), f"<code>{html.escape(result.username)}</code>"]
    if not sites:
        parts.append("<i>No public accounts found.</i>")
    else:
        parts.append(f"Found <b>{len(sites)}</b> account(s):")
        for s in sites[:_MAX_SITES]:
            url = s.url_user or s.url_main
            label = html.escape(s.site)
            if url:
                parts.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{label}</a>')
            else:
                parts.append(f"\u2022 {label}")
        extra = len(sites) - _MAX_SITES
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more</i>")
    parts.append(footer_links())
    return "\n".join(parts)


def _format_face_result(count: int, warning: str | None) -> str:
    """Render /face output: face count (or a graceful notice) + reverse-search hint."""
    parts = ["\U0001f5bc\ufe0f <b>Face Analysis</b>\n" + thin_sep()]
    if warning:
        parts.append(f"\u26a0\ufe0f {html.escape(warning)}")
        parts.append("<i>Use reverse image search instead.</i>")
    elif count > 0:
        parts.append(f"Detected <b>{count}</b> face(s) in this photo.")
    else:
        parts.append("No faces detected in this photo.")
    parts.append("\U0001f50e <b>Reverse search</b> \u2014 upload this photo to:")
    for label, url in _REVERSE_SEARCH_LINKS:
        parts.append(f'\u2022 <a href="{url}">{label}</a>')
    parts.append(footer_links())
    return "\n".join(parts)


def _unavailable_text(target: str, exc: Exception) -> str:
    """Helpful message when an OSINT tool/service is unavailable."""
    detail = str(exc).strip()
    if not detail:
        detail = "The required OSINT tool is not installed on this server."
    return f"\u26a0\ufe0f <b>OSINT Unavailable</b>\n\n<code>{html.escape(target)}</code>\n\n{html.escape(detail)}"


def _error_text(target: str, exc: Exception) -> str:
    """Friendly error card for a failed lookup."""
    logger.warning("osint lookup failed for %s: %s", target, exc)
    return (
        "\u274c <b>OSINT Lookup Failed</b>\n\n"
        f"<code>{html.escape(target)}</code>\n\n"
        "Could not complete the lookup. Please try again later."
    )


# ─── Command handlers ────────────────────────────────────────────────


async def cmd_osint(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/osint <username> — find a username's public accounts (maigret)."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f575\ufe0f <b>Username OSINT</b>\n\n"
            "Usage: /osint <code>username</code>\n\n"
            "Example: /osint <code>cryptorugmunch</code>\n\n"
            "Checks the handle across social platforms (maigret).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    username = ctx.args[0].strip().lstrip("@")
    if not _valid_username(username):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid username</b>\n\n"
            "Usernames must be 1-128 characters with no spaces or path separators.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Searching accounts for</b> <code>{html.escape(username)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await asyncio.to_thread(lookup_username, username, timeout=_TIMEOUTS["username"])
        await msg.edit_text(_format_username_result(result), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except (ToolsOsintNotAvailable, OsintNotAvailable) as exc:
        await msg.edit_text(_unavailable_text(username, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(username, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/track <wallet> — wallet entity/sanction/scam attribution."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f50d <b>Wallet Track</b>\n\n"
            "Usage: /track <code>address</code>\n\n"
            "Examples:\n"
            "  /track <code>0xBE0eB53FC46b790099138e3d32C721856d41e865</code>\n"
            "  /track <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>\n\n"
            "Entity labels, exchange attribution, scam reports and sanctions.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = ctx.args[0].strip()
    if len(address) < 20 or any(c.isspace() for c in address):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid wallet address</b>\n\n"
            "Pass a full EVM <code>0x\u2026</code> or Solana base58 address.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Attributing wallet</b> <code>{html.escape(address)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_wallet, address, timeout=_TIMEOUTS["wallet"])
        await msg.edit_text(
            _format_report(report, "\U0001f50d <b>Wallet Track</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(address, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(address, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def _cmd_domain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/investigate <domain> — WHOIS/DNS/subdomains/SSL/archive summary."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f575\ufe0f <b>Domain OSINT</b>\n\n"
            "Usage: /investigate <code>domain</code>\n\n"
            "Example: /investigate <code>scam-example.com</code>\n\n"
            "WHOIS, DNS, subdomains, SSL certificate and web-archive summary.\n"
            "Pass a token <code>address</code> for the deep investigation report (Pro).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    domain = ctx.args[0].strip()
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Investigating domain</b> <code>{html.escape(domain)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_domain, domain, timeout=_TIMEOUTS["domain"])
        await msg.edit_text(
            _format_report(report, "\U0001f575\ufe0f <b>Domain OSINT</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(domain, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(domain, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def cmd_investigate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/investigate — routes domain-looking args to OSINT, else the Pro deep report.

    Keeps the pre-existing Pro-tier token investigation intact: addresses and
    symbols fall through to ``handlers.investigate.cmd_investigate``.
    """
    if ctx.args and _looks_like_domain(ctx.args[0]):
        await _cmd_domain(update, ctx)
        return
    await _pro_investigate(update, ctx)


async def cmd_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/email <email> — breach + reputation check (HIBP, emailrep.io)."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4e7 <b>Email OSINT</b>\n\n"
            "Usage: /email <code>email</code>\n\n"
            "Example: /email <code>victim@example.com</code>\n\n"
            "Breach history (HIBP), pastes and reputation (emailrep.io).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    email = ctx.args[0].strip().lower()
    if not _valid_email(email):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid email address</b>\n\nPass a valid address like <code>name@example.com</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Checking email</b> <code>{html.escape(email)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_email, email, timeout=_TIMEOUTS["email"])
        await msg.edit_text(
            _format_report(report, "\U0001f4e7 <b>Email OSINT</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(email, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(email, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def cmd_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/phone <phone> — validation, carrier, region and line-type risk."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4f1 <b>Phone OSINT</b>\n\n"
            "Usage: /phone <code>number</code>\n\n"
            "Example: /phone <code>+14155552671</code>\n\n"
            "Validation, carrier, region, timezone and line-type risk.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    phone = ctx.args[0].strip()
    if not _valid_phone(phone):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid phone number</b>\n\n"
            "Pass an E.164 or national number like <code>+14155552671</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Looking up phone</b> <code>{html.escape(phone)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_phone, phone, timeout=_TIMEOUTS["phone"])
        await msg.edit_text(
            _format_report(report, "\U0001f4f1 <b>Phone OSINT</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(phone, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(phone, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def cmd_face(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/face — reply to a photo: detected faces + reverse-search hint."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    reply = update.message.reply_to_message if update.message else None
    if reply is None or not reply.photo:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f5bc\ufe0f <b>Face Analysis</b>\n\n"
            "Usage: <b>reply to a photo</b> with /face\n\n"
            "Detects faces in the image and gives a reverse-search hint.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f50d <b>Analyzing photo</b>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        photo = reply.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        count, warning = await asyncio.to_thread(_detect_faces, buf.getvalue())
        await msg.edit_text(
            _format_face_result(count, warning),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=back_kb(),
        )
    except (TelegramError, ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text("photo", exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


# ─── Aggregate: /osintall ────────────────────────────────────────────

#: Max aggregate findings rendered inline; the rest collapse to "+N more…".
_MAX_AGGREGATE_FINDINGS = 8
#: Max per-module errors surfaced in the footer note.
_MAX_AGGREGATE_ERRORS = 2
#: Wall-clock budget for the whole aggregate run (shared by every module).
_AGGREGATE_TIMEOUT = 90

#: Valid explicit ``type`` values accepted by /osintall (mirrors engine).
_OSINT_TYPES = ("username", "email", "domain", "wallet", "phone", "face")


def _format_aggregate_result(result: dict[str, Any]) -> str:
    """Render the merged ``run_aggregate`` result as a compact HTML message.

    Shows per-module hit counts, the top findings grouped by source/category,
    then "+N more…" and a short errors note.
    """
    target = str(result.get("target") or "?")
    target_type = str(result.get("target_type") or "unknown")
    results = result.get("results") or {}
    findings = result.get("findings") or []
    errors = result.get("errors") or []

    parts = [
        f"\U0001f575\ufe0f <b>OSINT Aggregate</b> \u2014 <b>{html.escape(target_type.upper())}</b>",
        thin_sep(),
        f"<code>{html.escape(target)}</code>",
        "",
    ]

    # Per-module hit counts.
    if results:
        parts.append("\U0001f4ca <b>Modules</b>")
        for key in sorted(results):
            report = results[key]
            if not isinstance(report, dict):
                continue
            count = len(report.get("findings") or [])
            module_errors = len(report.get("errors") or [])
            label = html.escape(str(key))
            note = f"<b>{count}</b> finding(s)"
            if module_errors:
                note += f" \u00b7 {module_errors} err"
            parts.append(f"\u2022 {label}: {note}")
    else:
        parts.append("<i>No modules produced a report.</i>")
    parts.append("")

    # Top findings, grouped by source/category.
    parts.append(f"\U0001f50e <b>Findings</b> (top {_MAX_AGGREGATE_FINDINGS})")
    shown = list(findings)[:_MAX_AGGREGATE_FINDINGS]
    if not shown:
        parts.append("<i>No findings collected.</i>")
    else:
        for finding in shown:
            if not isinstance(finding, dict):
                continue
            source = html.escape(str(finding.get("source") or "source"))
            category = str(finding.get("category") or "finding")
            summary = html.escape(str(finding.get("summary") or ""))
            confidence = str(finding.get("confidence") or "medium")
            conf = _CONFIDENCE_EMOJI.get(confidence, "\U0001f7e1")
            cat = _CATEGORY_EMOJI.get(category, "\u2022")
            parts.append(f"{cat} {conf} <b>{html.escape(category.title())}</b> \u2014 {summary} <i>({source})</i>")
        extra = len(findings) - _MAX_AGGREGATE_FINDINGS
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more findings</i>")

    # Errors note.
    if errors:
        parts.append("")
        shown_errors = [html.escape(str(e)) for e in errors[:_MAX_AGGREGATE_ERRORS]]
        parts.append(f"\u26a0\ufe0f <i>{'; '.join(shown_errors)}</i>")
        extra_errors = len(errors) - _MAX_AGGREGATE_ERRORS
        if extra_errors > 0:
            parts.append(f"<i>\u2026and {extra_errors} more source errors</i>")

    parts.append(footer_links())
    return "\n".join(parts)


async def cmd_osintall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/osintall <target> [type] — run every applicable OSINT module at once.

    Auto-detects the target type (or takes an explicit type from the second
    arg), then runs all applicable modules in parallel via the engine's
    ``run_aggregate`` and merges every finding into one summary.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f575\ufe0f <b>OSINT Aggregate</b>\n\n"
            "Usage: /osintall <code>target</code> [type]\n\n"
            "Examples:\n"
            "  /osintall <code>scam-example.com</code>\n"
            "  /osintall <code>0xBE0eB53FC46b790099138e3d32C721856d41e865</code> wallet\n\n"
            "Auto-detects the target type (username / email / domain / wallet / "
            "phone / face) and runs every applicable module in parallel, then "
            "merges all findings into one summary.\n\n"
            "\u23f3 Can take up to 90s.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    target = ctx.args[0].strip()
    if not target:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid target</b>\n\n"
            "Pass a username, email, domain, wallet address, phone or photo description.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    target_type = ctx.args[1].strip().lower() if len(ctx.args) > 1 else "auto"
    if target_type != "auto" and target_type not in _OSINT_TYPES:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u26a0\ufe0f <b>Invalid type</b>\n\nExpected one of: <code>{', '.join(_OSINT_TYPES)}</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Investigating everything...</b>\n<code>{html.escape(target)}</code>\n\n"
        "\u23f3 Running all applicable OSINT modules (up to 90s)...",
        parse_mode=ParseMode.HTML,
    )
    try:
        from app.osint.engine import run_aggregate
    except (ImportError, AttributeError) as exc:
        logger.warning("osint engine unavailable: %s", exc)
        await msg.edit_text(
            "\u26a0\ufe0f <b>OSINT engine unavailable</b>\n\n"
            "The aggregate OSINT engine is not installed on this server.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    try:
        result = await asyncio.to_thread(run_aggregate, target, target_type, timeout=_AGGREGATE_TIMEOUT)
        await msg.edit_text(
            _format_aggregate_result(result),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # best-effort aggregate; never crash the handler
        logger.warning("osintall failed for %s: %s", target, exc)
        await msg.edit_text(
            "\u274c <b>OSINT Aggregate Failed</b>\n\n"
            f"<code>{html.escape(target)}</code>\n\n"
            "Could not complete the aggregate lookup. Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

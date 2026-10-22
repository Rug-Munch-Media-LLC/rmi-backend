#!/usr/bin/env python3
"""Scan contract addresses found in Telegram channels and flag what matters.

Pipeline: read the last N messages per channel -> extract contract addresses
(EVM 0x + Solana base58) -> dedupe -> score each unique address through the
scanner API -> write ``scans_of_interest.jsonl`` -> build the "post scan of
interest" payload and **dry-run** it (nothing is sent).

Two readers, one shape:

* ``--source telethon`` — the burner user session, read-only. Requires
  ``RMI_TG_API_ID`` / ``RMI_TG_API_HASH`` (and an authorised session file).
  It only ever calls ``iter_messages``; it never posts, joins or reacts.
* ``--source preview`` — public ``t.me/s/<channel>`` web previews. Read-only,
  no credentials, and the same parser downstream. Use it to validate the
  pipeline before the burner session is wired.

Usage:
    python scripts/scan_channels.py --source preview --channels lookonchain,certik --limit 12
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.telegram.channel_classify import assess  # noqa: E402
from app.domains.telegram.scan_alert import send_scan_card  # noqa: E402

SCANNER_URL = os.getenv("SCANNER_URL", "http://localhost:8000/api/v2/scanner/token/scan")
OUT_JSONL = Path(os.getenv("SCAN_OUT", "/tmp/scan5/scans_of_interest.jsonl"))
OUT_PAYLOADS = Path(os.getenv("SCAN_PAYLOADS", "/tmp/scan5/dryrun_payloads.json"))


def _strip_tags(fragment: str) -> str:
    """Preview text as plain text: drop tags, keep line structure, unescape."""
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


async def _preview_messages(channel: str, limit: int) -> list[dict]:
    """Read a channel's public preview page — no login, no credentials."""
    url = f"https://t.me/s/{channel.lstrip('@')}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return []
    posts = re.findall(r'data-post="([^"]+)/(\d+)"(.*?)(?=data-post="|$)', resp.text, re.S)
    rows = []
    for slug, message_id, body in posts[-limit:]:
        match = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', body, re.S)
        rows.append(
            {
                "channel": slug,
                "message_id": int(message_id),
                "text": _strip_tags(match.group(1)) if match else "",
            }
        )
    return rows


async def _telethon_messages(channel: str, limit: int) -> list[dict]:
    """Read a channel through the burner session. Read-only: iter_messages only."""
    from telethon import TelegramClient

    api_id, api_hash = os.getenv("RMI_TG_API_ID", ""), os.getenv("RMI_TG_API_HASH", "")
    session = os.getenv("RMI_TG_SESSION", "/root/.crm_forensic.session")
    if not (api_id and api_hash):
        raise RuntimeError("set RMI_TG_API_ID and RMI_TG_API_HASH to read via the burner session")
    async with TelegramClient(session, int(api_id), api_hash) as client:
        entity = await client.get_entity(channel)
        rows = []
        async for msg in client.iter_messages(entity, limit=limit):
            rows.append(
                {
                    "channel": str(channel).lstrip("@"),
                    "message_id": msg.id,
                    "text": msg.message or "",
                }
            )
        return rows


async def _is_token(address: str, chain: str) -> bool:
    """True when a DEX pair exists for the address.

    The base58 pattern also matches Bitcoin bech32 addresses, handles and random
    ids, so 44 characters is not evidence of a mint. DexScreener is the cheapest
    arbiter; on failure we fail open and let the scanner judge.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
            return bool(resp.json().get("pairs"))
    except (httpx.HTTPError, ValueError):
        return True


async def _scan(address: str, chain: str) -> dict:
    """Score one address; a failure is reported as such, never scored as safe."""
    body = {"address": address, "chain": chain}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(SCANNER_URL, json=body)
            return resp.json() if resp.status_code == 200 else {"error": f"http_{resp.status_code}"}
    except httpx.HTTPError as exc:
        return {"error": str(exc)[:120]}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channels", default="@lookonchain", help="comma-separated channels")
    parser.add_argument("--limit", type=int, default=12, help="messages per channel")
    parser.add_argument("--source", choices=("preview", "telethon"), default="preview")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-verify", action="store_true", help="scan addresses with no DEX pair too")
    args = parser.parse_args()

    reader = _preview_messages if args.source == "preview" else _telethon_messages
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    messages: list[dict] = []
    for channel in channels:
        try:
            messages.extend(await reader(channel, args.limit))
        except Exception as exc:  # noqa: BLE001 - a dead channel must not kill the run
            print(f"[warn] {channel}: {type(exc).__name__}: {exc}", file=sys.stderr)

    assessed = [assess(m) for m in messages]
    seen: dict[str, dict] = {}
    for row in assessed:
        for item in row["addresses"]:
            seen.setdefault(
                item["address"],
                {
                    **item,
                    "channel": row["channel"],
                    "message_id": row["message_id"],
                    "why_interesting": row["why_interesting"],
                    "label": row["label"],
                },
            )

    results, payloads, rejected = [], [], []
    for address, meta in list(seen.items())[:12]:
        if not args.no_verify and not await _is_token(address, meta["chain"]):
            rejected.append({"address": address, "chain": meta["chain"], "why": "no DEX pair — not a token"})
            results.append({
                "channel": meta["channel"], "message_id": meta["message_id"], "address": address,
                "chain": meta["chain"], "risk": "unscannable", "safety_score": None,
                "why_interesting": meta["why_interesting"] or "address posted, no scam wording",
                "label": meta["label"], "scanner_error": "no DEX pair",
            })
            continue
        scan = await _scan(address, meta["chain"])
        risk = scan.get("risk_level", "unknown")
        results.append(
            {
                "channel": meta["channel"],
                "message_id": meta["message_id"],
                "address": address,
                "chain": meta["chain"],
                "risk": risk,
                "safety_score": scan.get("safety_score"),
                "why_interesting": meta["why_interesting"] or "address posted, no scam wording",
                "label": meta["label"],
                "scanner_error": scan.get("error"),
            }
        )
        if risk in ("high", "critical") or meta["why_interesting"]:
            payloads.append(await send_scan_card(scan if "error" not in scan else {}, dry_run=True))

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSONL.write_text("\n".join(json.dumps(r) for r in results) + "\n")
    OUT_PAYLOADS.write_text(json.dumps(payloads, indent=1))
    print(
        f"messages={len(messages)} addresses={len(seen)} scored={len(results)} payloads={len(payloads)}"
    )
    print(f"wrote {OUT_JSONL} and {OUT_PAYLOADS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

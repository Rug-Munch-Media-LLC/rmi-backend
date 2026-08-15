"""Badges, x402 bot payments, and RAG ingestion for bulletin board."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

from .manager import BulletinBoardManager

if TYPE_CHECKING:
    from .models import Post

logger = get_logger(__name__)

BADGES = {
    "first_post": {"name": "First Words", "icon": "💬", "desc": "Made your first post", "tier": "bronze"},
    "10_posts": {"name": "Chatterbox", "icon": "📢", "desc": "10 posts", "tier": "bronze"},
    "50_posts": {"name": "Board Regular", "icon": "🎙️", "desc": "50 posts", "tier": "silver"},
    "100_posts": {"name": "Terminally Online", "icon": "🖥️", "desc": "100 posts - touch grass", "tier": "gold"},
    "10_upvotes": {"name": "Approved", "icon": "👍", "desc": "10 upvotes on a post", "tier": "bronze"},
    "50_upvotes": {"name": "Crowd Favorite", "icon": "⭐", "desc": "50 upvotes", "tier": "silver"},
    "100_upvotes": {"name": "Legendary", "icon": "👑", "desc": "100 upvotes", "tier": "gold"},
    "rug_reporter": {"name": "Rug Detective", "icon": "🔍", "desc": "Reported 3 verified rugs", "tier": "silver"},
    "scam_buster": {"name": "Scam Buster", "icon": "🛡️", "desc": "10 scam alerts verified", "tier": "gold"},
    "honeypot_hunter": {"name": "Honeypot Hunter", "icon": "🍯", "desc": "Found 5 honeypots", "tier": "silver"},
    "whale_watcher": {"name": "Whale Watcher", "icon": "🐋", "desc": "Tracked 10 whale moves", "tier": "silver"},
    "alpha_caller": {"name": "Alpha Caller", "icon": "📈", "desc": "Called 5 pumps", "tier": "gold"},
    "degen": {"name": "Certified Degen", "icon": "🎰", "desc": "Posted in every category", "tier": "gold"},
    "rug_survivor": {"name": "Rug Survivor", "icon": "💀", "desc": "Posted about getting rugged", "tier": "bronze"},
    "diamond_hands": {"name": "Diamond Hands", "icon": "💎", "desc": "Held through -90%", "tier": "diamond"},
    "based": {"name": "Based", "icon": "🧠", "desc": "Called a 10x before it happened", "tier": "diamond"},
    "bot_verified": {"name": "Verified Bot", "icon": "🤖", "desc": "Registered x402 bot", "tier": "silver"},
    "bot_pro": {"name": "Bot Pro", "icon": "⚡", "desc": "100+ API calls", "tier": "gold"},
}
BADGE_TIERS = {"bronze": "#CD7F32", "silver": "#C0C0C0", "gold": "#FFD700", "diamond": "#B9F2FF"}
X402_BB_POST_PRICE = "$1.00"

# Solana USDC (mint: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v). 6 decimals,
# so $1.00 == 1_000_000 raw units — the minimum bot membership payment.
USDC_MINT_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
X402_USDC_MIN_RAW = 1_000_000


def _x402_payment_address() -> str:
    """Solana address that receives bot-membership USDC (configured by ops).

    Membership is fail-closed: without this env var no payment can be
    verified, so no bot posting is granted.
    """
    return os.getenv("BB_X402_PAYMENT_ADDRESS", "").strip()


def _tx_pays_usdc(tx: dict[str, Any], payment_address: str, min_raw: int) -> bool:
    """True when ``tx`` is a successful USDC transfer of >= ``min_raw`` to ``payment_address``.

    Parses the RPC `getTransaction` result's pre/post token balance deltas for
    the USDC mint owned by the payment address. A matching account must gain
    at least ``min_raw`` raw units (the account may be newly created, in which
    case the pre-balance is treated as zero).
    """
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return False
    pre = {b.get("accountIndex"): b for b in meta.get("preTokenBalances") or []}
    post = {b.get("accountIndex"): b for b in meta.get("postTokenBalances") or []}
    pay = payment_address.lower()
    for idx, entry in post.items():
        if entry.get("mint") != USDC_MINT_SOLANA or (entry.get("owner") or "").lower() != pay:
            continue
        try:
            post_amount = int(entry["uiTokenAmount"]["amount"])
        except (KeyError, TypeError, ValueError):
            continue
        pre_amount = 0
        if idx in pre:
            pre_entry = pre[idx]
            if pre_entry.get("mint") != USDC_MINT_SOLANA or (pre_entry.get("owner") or "").lower() != pay:
                continue
            try:
                pre_amount = int(pre_entry["uiTokenAmount"]["amount"])
            except (KeyError, TypeError, ValueError):
                continue
        if post_amount - pre_amount >= min_raw:
            return True
    return False


async def get_user_badges(user_id: str) -> list[Any]:
    try:
        r = await BulletinBoardManager._get_redis()
        ids = await r.smembers(f"bb:badges:{user_id}")
        await r.close()
        return [{"id": bid, **BADGES[bid]} for bid in ids if bid in BADGES]
    except (ConnectionError, OSError, TimeoutError):
        return []


async def award_badge(user_id: str, badge_id: str) -> bool:
    if badge_id not in BADGES:
        return False
    try:
        r = await BulletinBoardManager._get_redis()
        ok = await r.sadd(f"bb:badges:{user_id}", badge_id)
        await r.close()
        return ok > 0
    except (ConnectionError, OSError, TimeoutError):
        return False


async def get_user_reputation(user_id: str) -> dict[str, Any]:
    try:
        r = await BulletinBoardManager._get_redis()
        p = r.pipeline()
        p.get(f"bb:rep:{user_id}")
        p.scard(f"bb:badges:{user_id}")
        p.get(f"bb:posts:{user_id}")
        rep, bc, pc = await p.execute()
        await r.close()
        return {"reputation": int(rep or 0), "badge_count": bc or 0, "post_count": int(pc or 0)}
    except (ConnectionError, OSError):
        return {"reputation": 0, "badge_count": 0, "post_count": 0}


async def verify_x402_bot(tx_hash: str, bot_addr: str) -> bool:
    """Verify a real on-chain x402 bot membership payment.

    Requirements (all must hold):
      * ``tx_hash`` has not been used before (Redis replay guard).
      * The transaction exists on Solana, executed successfully, and the
        bot's wallet is the fee payer / signer.
      * The transaction moves >= $1 USDC to the configured payment address
        (``BB_X402_PAYMENT_ADDRESS``).

    The tx is fetched from the chain RPC the backend already uses
    (Helius → QuickNode via ``app.chain_client``). This fails CLOSED: when
    the payment address is unset, the RPC is unavailable, or the tx does not
    match, no membership is granted — the previous honor-system that accepted
    any tx_hash is removed.
    """
    r = await BulletinBoardManager._get_redis()
    try:
        # Replay guard: a tx_hash can only be redeemed once
        if await r.get(f"bb:x402:tx:{tx_hash}"):
            return False

        payment_address = _x402_payment_address()
        if not payment_address:
            logger.warning("x402 bot-post rejected: BB_X402_PAYMENT_ADDRESS not configured")
            return False

        try:
            from app.chain_client import get_chain_client

            txs = await get_chain_client().get_transactions([tx_hash])
        except Exception as exc:  # RPC down / circuit open — fail closed, never grant
            logger.warning("x402 bot-post: on-chain verification unavailable: %s", exc)
            return False

        if not txs:
            logger.warning("x402 bot-post rejected: tx not found on chain: %s", tx_hash[:16])
            return False

        tx = txs[0]
        # The bot must be the fee payer / signer of the payment tx
        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        if not account_keys or account_keys[0].lower() != bot_addr.lower():
            logger.warning("x402 bot-post rejected: signer mismatch for tx %s", tx_hash[:16])
            return False

        if not _tx_pays_usdc(tx, payment_address, X402_USDC_MIN_RAW):
            logger.warning(
                "x402 bot-post rejected: no USDC >= $1 to payment address in tx %s",
                tx_hash[:16],
            )
            return False

        await r.setex(f"bb:x402:tx:{tx_hash}", 86400, bot_addr)
        await r.setex(f"bb:x402:bot:{bot_addr}", 2592000, "1")
        return True
    except (ConnectionError, OSError, TimeoutError):
        logger.warning("x402 bot-post: verification failed (store unavailable) for tx %s", tx_hash[:16])
        return False
    finally:
        await r.close()


async def ingest_post_to_rag(post: Post) -> dict[str, Any]:
    """Ingest a published bulletin board post into the RAG research collection."""
    try:
        from app.rag.ingestion import ingest_research_post as _ingest_research

        report = await _ingest_research(
            post_id=post.post_id,
            title=post.title,
            content=post.content,
            category=post.category,
            tags=post.tags,
            author=post.author_name,
        )
        return {
            "success": report.status in ("completed", "partial"),
            "status": report.status,
            "chunks": report.chunks_total,
        }
    except Exception as exc:
        return {"success": False, "status": "failed", "error": str(exc)[:300]}

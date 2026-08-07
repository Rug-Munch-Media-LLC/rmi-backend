"""
Telegram Integration Router
============================
FastAPI endpoints for voice/video chat management, group embedding,
and headless Telegram operations.

Prefix: /api/v1/telegram
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import BOT_TOKEN

logger = get_logger("telegram.router")

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


# ── Request/Response Models ─────────────────────────────────────────────


class CreateVoiceChatRequest(BaseModel):
    chat_id: int = Field(..., description="Telegram group chat ID")
    title: str | None = Field(None, description="Optional voice chat title")
    is_video: bool = Field(False, description="Video chat vs audio-only")


class EndVoiceChatRequest(BaseModel):
    chat_id: int = Field(..., description="Telegram group chat ID")


class VoiceChatStatusResponse(BaseModel):
    active: bool
    chat_id: int
    title: str | None = None
    is_video: bool = False
    participant_count: int = 0
    invite_link: str | None = None
    duration_seconds: float | None = None


class GroupProvisionRequest(BaseModel):
    creator_name: str = Field(..., description="Creator display name")
    coin_ticker: str = Field(..., description="Coin ticker symbol")
    creator_user_id: int = Field(..., description="Creator's Telegram user ID")


class GroupProvisionResponse(BaseModel):
    success: bool
    chat_id: int | None = None
    title: str | None = None
    username: str | None = None
    invite_link: str | None = None
    error: str | None = None


class SendGroupMessageRequest(BaseModel):
    chat_id: int
    message: str
    parse_mode: str = "markdown"
    pin: bool = False


class ModerationActionRequest(BaseModel):
    chat_id: int
    user_id: int
    reason: str | None = None


class HealthResponse(BaseModel):
    bot_configured: bool
    headless_available: bool
    active_voice_chats: int
    managed_groups: int


# ── Helper ──────────────────────────────────────────────────────────────


def _get_bot_or_raise():
    """Get Telegram Bot instance or raise 503."""
    if not BOT_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Telegram bot not configured (no BOT_TOKEN)",
        )
    from telegram import Bot

    return Bot(token=BOT_TOKEN)


# ── Voice Chat Endpoints ───────────────────────────────────────────────


@router.post("/voice-chat/create", response_model=VoiceChatStatusResponse)
async def api_create_voice_chat(req: CreateVoiceChatRequest):
    """Create a voice/video chat in a Telegram group."""
    bot = _get_bot_or_raise()
    try:
        from app.domains.telegram.voice_chat import create_voice_chat

        session = await create_voice_chat(
            bot,
            req.chat_id,
            title=req.title,
            is_video=req.is_video,
        )
        return VoiceChatStatusResponse(
            active=True,
            chat_id=session.chat_id,
            title=session.title,
            is_video=session.is_video,
            participant_count=session.participant_count,
            invite_link=session.invite_link,
            duration_seconds=session.duration_seconds,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/voice-chat/end")
async def api_end_voice_chat(req: EndVoiceChatRequest):
    """End an active voice/video chat."""
    bot = _get_bot_or_raise()
    try:
        from app.domains.telegram.voice_chat import end_voice_chat

        success = await end_voice_chat(bot, req.chat_id)
        return {"success": success}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/voice-chat/status/{chat_id}", response_model=VoiceChatStatusResponse)
async def api_voice_chat_status(chat_id: int):
    """Check status of a voice/video chat."""
    bot = _get_bot_or_raise()
    try:
        from app.domains.telegram.voice_chat import get_voice_chat_status

        session = await get_voice_chat_status(bot, chat_id)
        if session:
            return VoiceChatStatusResponse(
                active=True,
                chat_id=session.chat_id,
                title=session.title,
                is_video=session.is_video,
                participant_count=session.participant_count,
                invite_link=session.invite_link,
                duration_seconds=session.duration_seconds,
            )
        return VoiceChatStatusResponse(active=False, chat_id=chat_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/voice-chat/list")
async def api_list_voice_chats():
    """List all active voice/video chats."""
    bot = _get_bot_or_raise()
    try:
        from app.domains.telegram.voice_chat import list_voice_chats

        chats = await list_voice_chats(bot)
        return {
            "active_chats": len(chats),
            "chats": [
                {
                    "chat_id": cid,
                    "title": s.title,
                    "is_video": s.is_video,
                    "participant_count": s.participant_count,
                    "duration_seconds": s.duration_seconds,
                }
                for cid, s in chats.items()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── Headless Group Management Endpoints ────────────────────────────────


@router.post("/group/provision", response_model=GroupProvisionResponse)
async def api_provision_creator_group(req: GroupProvisionRequest):
    """Auto-create a Telegram group for a new creator coin."""
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        group = await client.provision_creator_coin_group(
            creator_name=req.creator_name,
            coin_ticker=req.coin_ticker,
            creator_user_id=req.creator_user_id,
        )
        return GroupProvisionResponse(
            success=True,
            chat_id=group.chat_id,
            title=group.title,
            username=group.username,
            invite_link=group.invite_link,
        )
    except RuntimeError as e:
        logger.error("Group provision failed: %s", e)
        return GroupProvisionResponse(success=False, error=str(e))
    except ImportError:
        return GroupProvisionResponse(
            success=False,
            error="Telethon not installed. Run: pip install telethon",
        )


@router.post("/group/send-message")
async def api_send_group_message(req: SendGroupMessageRequest):
    """Send a message to a managed group."""
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        success = await client.send_message(
            req.chat_id,
            req.message,
            parse_mode=req.parse_mode,
            pin=req.pin,
        )
        return {"success": success}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/group/ban")
async def api_ban_user(req: ModerationActionRequest):
    """Ban a user from a group."""
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        success = await client.ban_user(
            req.chat_id,
            req.user_id,
            reason=req.reason,
        )
        return {"success": success}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/group/unban")
async def api_unban_user(req: ModerationActionRequest):
    """Unban a user from a group."""
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        success = await client.unban_user(req.chat_id, req.user_id)
        return {"success": success}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── Health & Status ────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def api_telegram_health():
    """Health check for Telegram integration."""
    from app.domains.telegram.voice_chat import _active_voice_chats

    headless_available = False
    try:
        import telethon  # noqa: F401 — availability probe

        headless_available = True
    except ImportError:
        pass

    try:
        from app.domains.telegram.headless import _client_instance

        managed_groups = 0
        if _client_instance and hasattr(_client_instance, "_managed_groups"):
            managed_groups = len(_client_instance._managed_groups)
    except (ImportError, AttributeError):
        managed_groups = 0

    return HealthResponse(
        bot_configured=bool(BOT_TOKEN),
        headless_available=headless_available,
        active_voice_chats=len(_active_voice_chats),
        managed_groups=managed_groups,
    )


@router.get("/group/embed/{chat_id}")
async def api_group_embed_info(chat_id: int):
    """Get group embed information for frontend widget."""
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()

        # Get group info from telegram
        try:
            # Try to get via direct fetch
            info = await client._fetch_group_info(chat_id)
        except Exception:
            # Return minimal info from cached state
            info = None
            for g in client._managed_groups.values():
                if g.chat_id == chat_id:
                    info = g
                    break
            if not info:
                raise HTTPException(
                    status_code=404,
                    detail=f"Group {chat_id} not found",
                ) from None

        return {
            "chat_id": info.chat_id,
            "title": info.title,
            "username": info.username,
            "invite_link": info.invite_link,
            "member_count": info.member_count,
            "is_public": info.is_public,
            "embed_url": (f"https://t.me/{info.username}?embed=1" if info.username else None),
        }
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Telethon not installed",
        ) from None

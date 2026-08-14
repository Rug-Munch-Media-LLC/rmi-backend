"""Multi-platform social publisher for RugMunchBot.

Posts a short piece of text (optionally with a media URL) to every enabled
social platform through one common interface. The bot today publishes to
Telegram only; this module bakes in the *possibility* of posting to X,
Farcaster, Mastodon and Discord (the aixbt-style multi-account play) without
requiring a live client on any of them.

Fail-open contract
------------------
Every platform is independent: a disabled adapter is skipped, a raising
adapter is caught, and one failure never blocks the other platforms.
``publish_everywhere`` returns ``{platform: {"ok": bool, "post_id": str | None, "error": str | None}}``
for every requested platform — disabled / unknown platforms appear with
``ok=False`` and a short ``error`` explaining why.

Platform registry
-----------------
=============  ==================  ======================================================
Platform key   Adapter             Status
=============  ==================  ======================================================
telegram       TelegramPublisher   REAL — reuses the headless Telethon transport
farcaster      FarcasterPublisher  REAL — Neynar cast API (same path as newsletter)
x              XPublisher          STUB — interface + config only, no client yet
mastodon       MastodonPublisher   STUB — interface + config only, no client yet
discord        DiscordPublisher    STUB — interface + config only, no client yet
postiz         PostizPublisher     REAL — 30+ platforms via the Postiz domain service
=============  ==================  ======================================================

Per-platform configuration (env vars)
-------------------------------------
telegram
    ``RMI_MAIN_CHANNEL_ID`` — chat id (numeric) or ``@handle`` of the bot's
    main broadcast channel. The adapter goes through the existing headless
    client (``app.domains.telegram.headless``) with HTML parse mode. Telethon
    is imported lazily; when it is unavailable the adapter fails open.

farcaster
    ``FARCASTER_API_KEY`` (Neynar API key) and ``FARCASTER_SIGNER_UUID``
    (Neynar signer UUID) — the same credentials the newsletter publisher
    uses. A raw signer key also exists in the secrets manager at
    ``rmi/infra/farcaster_signer_key`` (and ``FARCASTER_SIGNER_KEY`` env) for
    a future direct-hub signing client; it is *not* required by this adapter.
    No signer → adapter disabled (fail-open).

x
    Stub config — ``X_API_KEY``, ``X_API_SECRET``, ``X_ACCESS_TOKEN``,
    ``X_ACCESS_TOKEN_SECRET`` (v1.1) or ``X_BEARER_TOKEN`` (v2). ``enabled``
    flips when any is present; ``post`` still returns an error until a real
    client is wired (see ``docs/SOCIAL_PUBLISHER.md``).

mastodon
    ``MASTODON_ACCESS_TOKEN`` + ``MASTODON_INSTANCE_URL`` (already typed in
    ``app.config.Settings``). Stub until a Mastodon.py client is wired.

discord
    ``DISCORD_WEBHOOK_URL`` — a channel webhook is the minimal integration.
    Stub until the webhook POST is wired (or a bot-token client).

Usage
-----
.. code-block:: python

    from app.domains.social import social_publisher

    results = await social_publisher.publish_everywhere(
        "GM, RugMunch here 🧵",
        media_url="https://rugmunch.io/post/1",
        platforms=["telegram", "farcaster"],  # optional subset
    )
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypedDict

import httpx

from app.config import settings as _settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

logger = get_logger(__name__)

#: Registry order — the canonical platform keys used everywhere.
ALL_PLATFORMS: tuple[str, ...] = ("telegram", "farcaster", "x", "mastodon", "discord", "postiz")

#: Neynar cast API (same endpoint as app/domains/newsletter/publisher.py).
_NEYNAR_CAST_URL = "https://api.neynar.com/v2/farcaster/cast"

_INT_RE = re.compile(r"^-?\d+$")


class PostResult(TypedDict):
    """Shape of one platform's result: ``{"ok": bool, "post_id": str | None, "error": str | None}``."""

    ok: bool
    post_id: str | None
    error: str | None


def _env(name: str) -> str | None:
    """Trimmed env value, or None when unset/blank."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _ok(post_id: str | None = None) -> PostResult:
    """Success result: ``{"ok": True, "post_id": ..., "error": None}``."""
    return {"ok": True, "post_id": post_id, "error": None}


def _error(error: str) -> PostResult:
    """Failure result: ``{"ok": False, "post_id": None, "error": ...}``."""
    return {"ok": False, "post_id": None, "error": error}


class PlatformAdapter(ABC):
    """Common contract for a single social-platform sink.

    Subclasses implement :attr:`enabled` (config present?) and :meth:`post`
    (publish). :attr:`implemented` is ``False`` for documented stubs whose
    ``post`` intentionally returns an error until a real client is wired.
    """

    name: str = ""
    implemented: bool = True

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """True when the platform's credentials/config are present."""

    @abstractmethod
    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        """Publish ``text`` (optionally with ``media_url``).

        Returns ``{"ok": bool, "post_id": str | None, "error": str | None}``.
        Must never raise — errors are returned in the dict.
        """


# ── Telegram (REAL) ─────────────────────────────────────────────────────


class TelegramPublisher(PlatformAdapter):
    """Posts through the existing headless Telethon transport.

    Config: ``RMI_MAIN_CHANNEL_ID`` (numeric chat id or ``@handle``) for the
    bot's main broadcast channel. The headless client is imported lazily so
    this module stays importable without telethon installed; when the client
    cannot be loaded the adapter fails open.
    """

    name = "telegram"

    def __init__(self, chat_id: int | str | None = None) -> None:
        # Injectable target (tests/orchestrators); None → read env at post time.
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return self._resolve_chat_id() is not None

    def _resolve_chat_id(self) -> int | str | None:
        if self._chat_id is not None:
            return self._chat_id
        raw = _env("RMI_MAIN_CHANNEL_ID")
        if raw is None:
            return None
        return int(raw) if _INT_RE.fullmatch(raw) else raw

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        chat_id = self._resolve_chat_id()
        if chat_id is None:
            return _error("Telegram not enabled: RMI_MAIN_CHANNEL_ID unset")
        try:
            from app.domains.telegram.headless import get_headless_client
        except ImportError as exc:
            return _error(f"Telegram transport unavailable (telethon not installed): {exc}")

        body = text + (f"\n🔗 {media_url}" if media_url else "")
        try:
            client = await get_headless_client()
            ok = await client.send_message(chat_id, body, parse_mode="html")  # type: ignore[arg-type]  # @-handles accepted by telethon
        except Exception as exc:
            logger.error("social.telegram_post_failed", error=str(exc))
            return _error(f"Telegram send failed: {exc}")
        if not ok:
            return _error("Telegram send_message returned False")
        return _ok()  # send_message returns bool; Telegram ids are transport-internal


# ── Farcaster (REAL, Neynar) ────────────────────────────────────────────


class FarcasterPublisher(PlatformAdapter):
    """Casts via the Neynar API — same credentials as the newsletter publisher.

    Config: ``FARCASTER_API_KEY`` (Neynar API key) and ``FARCASTER_SIGNER_UUID``
    (Neynar signer UUID). The raw signer key (``FARCASTER_SIGNER_KEY`` env /
    a secrets-manager entry) is *not* consumed here — it is
    documented for a future direct-hub signing client. Without a signer the
    adapter is disabled (fail-open stub behaviour).
    """

    name = "farcaster"
    MAX_CAST_CHARS = 300  # Farcaster limit is 320; stay under for embeds.

    @property
    def enabled(self) -> bool:
        return bool(_settings.farcaster_api_key and _settings.farcaster_signer_uuid)

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        api_key = _settings.farcaster_api_key
        signer_uuid = _settings.farcaster_signer_uuid
        if not api_key or not signer_uuid:
            return _error("Farcaster not enabled: FARCASTER_API_KEY / FARCASTER_SIGNER_UUID unset")

        payload: dict[str, Any] = {
            "signer_uuid": signer_uuid,
            "text": text[: self.MAX_CAST_CHARS],
            "embeds": [{"url": media_url}] if media_url else [],
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.post(
                    _NEYNAR_CAST_URL,
                    json=payload,
                    headers={"api_key": api_key, "Content-Type": "application/json"},
                )
            if resp.status_code not in (200, 201):
                return _error(f"Neynar API returned {resp.status_code}: {resp.text[:200]}")
            cast_hash = resp.json().get("cast", {}).get("hash", "")
            return _ok(cast_hash or None)
        except Exception as exc:
            logger.error("social.farcaster_post_failed", error=str(exc))
            return _error(f"Farcaster cast failed: {exc}")


# ── X (STUB) ────────────────────────────────────────────────────────────


class XPublisher(PlatformAdapter):
    """Documented stub — interface + config only.

    Config: ``X_API_KEY``, ``X_API_SECRET``, ``X_ACCESS_TOKEN``,
    ``X_ACCESS_TOKEN_SECRET`` (v1.1) or ``X_BEARER_TOKEN`` (v2). ``enabled``
    reflects the presence of any credential; ``post`` returns an error until
    a real client is wired. See ``docs/SOCIAL_PUBLISHER.md`` for where.
    """

    name = "x"
    implemented = False
    STUB_HINT = "wire twitter-api (X API v2) or tweepy; see docs/SOCIAL_PUBLISHER.md"

    _REQUIRED_ENV = (
        "X_API_KEY",
        "X_API_SECRET",
        "X_ACCESS_TOKEN",
        "X_ACCESS_TOKEN_SECRET",
        "X_BEARER_TOKEN",
    )

    @property
    def enabled(self) -> bool:
        return any(_env(k) for k in self._REQUIRED_ENV)

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        return _error(f"{type(self).__name__} is a stub — {self.STUB_HINT}")


# ── Mastodon (STUB) ─────────────────────────────────────────────────────


class MastodonPublisher(PlatformAdapter):
    """Documented stub — interface + config only.

    Config: ``MASTODON_ACCESS_TOKEN`` + ``MASTODON_INSTANCE_URL`` (already
    typed in ``app.config.Settings``). ``enabled`` reflects both being
    present; ``post`` returns an error until a Mastodon.py client is wired.
    """

    name = "mastodon"
    implemented = False
    STUB_HINT = "wire Mastodon.py (mastodon.Mastodon) against MASTODON_INSTANCE_URL; see docs/SOCIAL_PUBLISHER.md"

    @property
    def enabled(self) -> bool:
        return bool(_settings.mastodon_access_token and _settings.mastodon_instance_url)

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        return _error(f"{type(self).__name__} is a stub — {self.STUB_HINT}")


# ── Discord (STUB) ──────────────────────────────────────────────────────


class DiscordPublisher(PlatformAdapter):
    """Documented stub — interface + config only.

    Config: ``DISCORD_WEBHOOK_URL`` — a channel webhook is the minimal
    integration (no bot token needed). ``enabled`` reflects the webhook being
    present; ``post`` returns an error until the webhook POST is wired (or a
    bot-token client).
    """

    name = "discord"
    implemented = False
    STUB_HINT = "wire discord webhook POST (or discord.py bot client); see docs/SOCIAL_PUBLISHER.md"

    @property
    def enabled(self) -> bool:
        return _env("DISCORD_WEBHOOK_URL") is not None

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        return _error(f"{type(self).__name__} is a stub — {self.STUB_HINT}")


# ── Postiz (REAL — 30+ platforms via the Postiz domain service) ─────────


#: Postiz platform ids that already have a REAL direct adapter — excluded from
#: the Postiz default target so a platform is not posted to twice.
_DIRECT_ADAPTER_PLATFORM_IDS = frozenset({"telegram", "farcaster"})


class PostizPublisher(PlatformAdapter):
    """Posts through the Postiz domain service — one schedule call targets
    every Postiz platform (30+) as a single content item.

    No credentials live here: ``enabled`` is True whenever the Postiz
    service/store can be imported (``app.domains.postiz.services``). By default
    ``post`` targets every id in ``app.domains.postiz.platforms.PLATFORMS``
    except the platforms with a real direct adapter (``telegram``,
    ``farcaster``) so they are not double-posted; pass ``platform_ids`` to
    override. The service is called with ``scheduled_at`` unset (post
    immediately) and the created post's ``id`` is returned as ``post_id``.

    The service function is imported lazily so this module stays importable
    without the Postiz domain; when it is unavailable the adapter fails open.
    """

    name = "postiz"

    def __init__(
        self,
        *,
        schedule_post: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        platform_ids: Iterable[str] | None = None,
    ) -> None:
        # Injectable service callable (tests/orchestrators); None → lazy import.
        self._schedule_post = schedule_post
        self._platform_ids = tuple(platform_ids) if platform_ids else None

    @staticmethod
    def _service_available() -> bool:
        """True when the Postiz domain service + store can be loaded."""
        try:
            from app.domains.postiz.services.scheduling import schedule_post  # noqa: F401
            from app.domains.postiz.services.store import get_store

            get_store()
            return True
        except Exception:
            return False

    @property
    def enabled(self) -> bool:
        return self._schedule_post is not None or self._service_available()

    def _target_platform_ids(self) -> list[str]:
        if self._platform_ids is not None:
            return [p.lower() for p in self._platform_ids]
        from app.domains.postiz.platforms import PLATFORMS

        return [pid for pid in PLATFORMS if pid not in _DIRECT_ADAPTER_PLATFORM_IDS]

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        service = self._schedule_post
        if service is None:
            try:
                from app.domains.postiz.services.scheduling import schedule_post as _schedule_service
            except ImportError as exc:
                return _error(f"Postiz service unavailable: {exc}")
            service = _schedule_service

        platform_ids = self._target_platform_ids()
        if not platform_ids:
            return _error("Postiz has no target platforms configured")

        request: dict[str, Any] = {
            "text": text,
            "platforms": platform_ids,
            "media_urls": [media_url] if media_url else [],
        }
        try:
            scheduled = service(request)
        except Exception as exc:
            logger.error("social.postiz_post_failed", error=str(exc))
            return _error(f"Postiz schedule failed: {exc}")
        return _ok((scheduled or {}).get("id"))


# ── Registry / orchestrator ─────────────────────────────────────────────


def default_adapters() -> dict[str, PlatformAdapter]:
    """Build the production adapter set keyed by platform name."""
    adapters: dict[str, PlatformAdapter] = {
        a.name: a
        for a in (
            TelegramPublisher(),
            FarcasterPublisher(),
            XPublisher(),
            MastodonPublisher(),
            DiscordPublisher(),
            PostizPublisher(),
        )
    }
    return adapters


class SocialPublisher:
    """Fail-open orchestrator over :class:`PlatformAdapter` instances.

    ``post`` / ``publish_everywhere`` iterate the requested platforms, skip
    disabled ones and never let one platform's failure block the others.
    """

    def __init__(self, adapters: Mapping[str, PlatformAdapter] | None = None) -> None:
        self._adapters: dict[str, PlatformAdapter] = dict(adapters or default_adapters())

    @property
    def adapters(self) -> dict[str, PlatformAdapter]:
        """Snapshot of the registered adapters keyed by platform name."""
        return dict(self._adapters)

    def enabled_platforms(self) -> list[str]:
        """Names of adapters whose config is present (registry order)."""
        return [name for name in ALL_PLATFORMS if name in self._adapters and self._adapters[name].enabled]

    async def post(self, text: str, *, media_url: str | None = None) -> dict[str, PostResult]:
        """Common interface — post to every enabled platform.

        Returns ``{platform: {"ok": bool, "post_id": str | None, "error": str | None}}``.
        """
        return await self.publish_everywhere(text, media_url=media_url)

    async def publish_everywhere(
        self,
        text: str,
        *,
        media_url: str | None = None,
        platforms: list[str] | None = None,
    ) -> dict[str, PostResult]:
        """Post ``text`` to all enabled platforms (or the requested subset).

        ``platforms`` filters to a subset of platform keys; when omitted every
        registered platform is considered. Disabled platforms are skipped (the
        adapter is never invoked) but still appear in the result with
        ``ok=False`` / ``error="not enabled"``. A platform that raises is
        caught and recorded without blocking the rest.
        """
        targets = list(platforms) if platforms is not None else list(self._adapters)
        results: dict[str, PostResult] = {}

        for name in targets:
            adapter = self._adapters.get(name)
            if adapter is None:
                results[name] = _error(f"unknown platform {name!r}")
                continue
            if not adapter.enabled:
                logger.warning("social.platform_disabled", platform=name)
                results[name] = _error("not enabled")
                continue
            try:
                results[name] = await adapter.post(text, media_url=media_url)
            except Exception as exc:
                logger.error("social.platform_post_failed", platform=name, error=str(exc))
                results[name] = _error(str(exc))

        return results


#: Process-wide publisher singleton (mirrors ``app.config.settings``).
social_publisher = SocialPublisher()

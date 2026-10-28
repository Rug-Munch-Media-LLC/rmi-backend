"""Lane E — delivery for the three daily editions and tiered story routing.

Public surface (everything else in the package is internal):

* :func:`publish_edition` — render + deliver one edition, idempotently;
* :func:`plan_delivery` — Lane C decisions → capped, deduped, promoted send plan;
* :func:`deliver` — execute a plan with flood control, retry and lane isolation;
* :func:`build_preview` — the dry run (payloads, plan, lane table, schedule).

Chat ids never live in this package: :mod:`app.domains.telegram.editions.lanes`
resolves them through the shared channel registry, so a channel move is a
config change on one side only.
"""

from app.domains.telegram.editions.cli import build_preview, main, render_editions, render_routing
from app.domains.telegram.editions.delivery import (
    DeliveryReport,
    DeliveryResult,
    deliver,
)
from app.domains.telegram.editions.editions import (
    EDITIONS,
    Edition,
    edition_lanes,
    edition_title,
    get_edition,
    next_run,
    publish_edition,
    render_edition,
    schedule,
    site_url,
)
from app.domains.telegram.editions.lanes import (
    LANE_BINDINGS,
    LANES,
    LaneBinding,
    lane_chat_id,
    lane_live,
    parse_chat_id,
)
from app.domains.telegram.editions.lanes_report import lane_inventory, lane_source
from app.domains.telegram.editions.ledger import (
    DeliveryLedger,
    InMemoryLedger,
    LedgerRow,
    day_key,
    dedupe_key,
    slug_key,
)
from app.domains.telegram.editions.payloads import (
    CAPTION_LIMIT,
    TelegramPayload,
    normalise_tags,
)
from app.domains.telegram.editions.render import (
    canonical_link,
    edition_payload,
    story_payload,
)
from app.domains.telegram.editions.retry import DeliveryConfig, backoff_delay, is_retryable
from app.domains.telegram.editions.routing import (
    DEFAULT_CAPS,
    PROMOTION_LANES,
    DeliveryPlan,
    PlannedPost,
    RouteDecision,
    Skipped,
    lane_cap,
    parse_decision,
    plan_delivery,
)
from app.domains.telegram.editions.sender import HeadlessSender, RecordingSender, Sender, SentMessage

__all__ = [
    "CAPTION_LIMIT",
    "DEFAULT_CAPS",
    "EDITIONS",
    "LANES",
    "LANE_BINDINGS",
    "PROMOTION_LANES",
    "DeliveryConfig",
    "DeliveryLedger",
    "DeliveryPlan",
    "DeliveryReport",
    "DeliveryResult",
    "Edition",
    "HeadlessSender",
    "InMemoryLedger",
    "LaneBinding",
    "LedgerRow",
    "PlannedPost",
    "RecordingSender",
    "RouteDecision",
    "Sender",
    "SentMessage",
    "Skipped",
    "TelegramPayload",
    "backoff_delay",
    "build_preview",
    "canonical_link",
    "day_key",
    "dedupe_key",
    "deliver",
    "edition_lanes",
    "edition_payload",
    "edition_title",
    "get_edition",
    "is_retryable",
    "lane_cap",
    "lane_chat_id",
    "lane_inventory",
    "lane_live",
    "lane_source",
    "main",
    "next_run",
    "normalise_tags",
    "parse_chat_id",
    "parse_decision",
    "plan_delivery",
    "publish_edition",
    "render_edition",
    "render_editions",
    "render_routing",
    "schedule",
    "site_url",
    "slug_key",
    "story_payload",
]

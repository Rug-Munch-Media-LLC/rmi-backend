"""SQLAlchemy 2.0 ORM models. DeclarativeBase and all model imports.

Usage:
    from app.models import Base, User, ScanResult, Token, Alert, AlertSubscription, Label, Payment

Model files organized by domain:
    base.py   - TimestampMixin (shared created_at/updated_at)
    user.py   - User accounts
    token.py  - Token contract registry
    scan.py   - Token safety scan results
    alert.py  - Security alerts and subscriptions
    label.py  - Address labels
    payment.py - x402 micro-payment receipts
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models.

    All models inherit from this and TimestampMixin (created_at, updated_at).
    Managed by Alembic migrations in alembic/versions/.
    """


from app.models.alert import Alert, AlertSubscription  # noqa: E402 — module docstring precedes import block
from app.models.analytics import (  # noqa: E402 — module docstring precedes import block
    AudienceFollower,
    ContentPost,
    CreatorProfile,
    EngagementEvent,
    ViewEvent,
)
from app.models.boost import BoostEvent, BoostPurchase  # noqa: E402 — paid boost inventory
from app.models.kol import KOLWallet  # noqa: E402 — module docstring precedes import block
from app.models.kv_migration import (  # noqa: E402 — module docstring precedes import block
    Bookmark,
    ChatMessage,
    IdentityMapping,
)
from app.models.label import Label  # noqa: E402 — module docstring precedes import block
from app.models.live import (  # noqa: E402 — module docstring precedes import block
    AchievementDef,
    BattleGift,
    Challenge,
    CreatorStreak,
    LiveBattle,
    LiveEvent,
    UserAchievement,
    UserChallenge,
    ViewerXPTransaction,
)
from app.models.payment import Payment  # noqa: E402 — module docstring precedes import block
from app.models.poly_attribution import PolyAttributionEvent  # noqa: E402
from app.models.portfolio import (  # noqa: E402 — module docstring precedes import block
    UserPortfolioEntry,
    UserWatchlistEntry,
)
from app.models.scan import ScanResult  # noqa: E402 — module docstring precedes import block
from app.models.token import Token  # noqa: E402 — module docstring precedes import block
from app.models.user import User  # noqa: E402 — module docstring precedes import block

__all__ = [
    "AchievementDef",
    "Alert",
    "AlertSubscription",
    "AudienceFollower",
    "Base",
    "BattleGift",
    "Bookmark",
    "BoostEvent",
    "BoostPurchase",
    "Challenge",
    "ChatMessage",
    "ContentPost",
    "CreatorProfile",
    "CreatorStreak",
    "EngagementEvent",
    "IdentityMapping",
    "KOLWallet",
    "Label",
    # Newsletter models: import directly from app.domains.newsletter.models
    "LiveBattle",
    "LiveEvent",
    "Payment",
    "PolyAttributionEvent",
    "ScanResult",
    "Token",
    "User",
    "UserAchievement",
    "UserChallenge",
    "UserPortfolioEntry",
    "UserWatchlistEntry",
    "ViewEvent",
    "ViewerXPTransaction",
]

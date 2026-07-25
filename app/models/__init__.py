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


from app.models.analytics import (
    AudienceFollower,
    ContentPost,
    CreatorProfile,
    EngagementEvent,
    ViewEvent,
)
from app.models.alert import Alert, AlertSubscription
from app.models.label import Label
from app.models.live import (
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
from app.models.payment import Payment
from app.models.scan import ScanResult
from app.models.token import Token
from app.models.user import User

__all__ = [
    "AchievementDef",
    "Alert",
    "AlertSubscription",
    "AudienceFollower",
    "Base",
    "ContentPost",
    "CreatorProfile",
    "EngagementEvent",
    "BattleGift",
    "Challenge",
    "CreatorStreak",
    "Label",
    # Newsletter models: import directly from app.domains.newsletter.models
    "LiveBattle",
    "LiveEvent",
    "Payment",
    "ScanResult",
    "Token",
    "User",
    "UserAchievement",
    "UserChallenge",
    "ViewEvent",
    "ViewerXPTransaction",
]

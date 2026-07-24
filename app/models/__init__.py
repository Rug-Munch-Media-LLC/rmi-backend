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
    pass

from app.models.alert import Alert, AlertSubscription  # noqa: E402
from app.models.label import Label  # noqa: E402
from app.models.payment import Payment  # noqa: E402
from app.models.scan import ScanResult  # noqa: E402
from app.models.token import Token  # noqa: E402
from app.models.user import User  # noqa: E402

__all__ = [
    "Alert",
    "AlertSubscription",
    "Base",
    "Label",
    "Payment",
    "ScanResult",
    "Token",
    "User",
]

"""SQLAlchemy 2.0 ORM models. DeclarativeBase and all model imports."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
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
